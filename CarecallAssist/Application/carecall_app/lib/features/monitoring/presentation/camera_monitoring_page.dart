import 'package:flutter/material.dart';
import 'package:flutter_mjpeg/flutter_mjpeg.dart';

import '../../../services/app_config.dart';
import '../../../store/alert_store.dart';

class CameraMonitoringPage extends StatelessWidget {
  final String? initialStreamUrl;

  const CameraMonitoringPage({
    super.key,
    this.initialStreamUrl,
  });

  String _resolveStreamUrl({
    required String? initialUrl,
    required String? statusUrl,
    required String fallbackUrl,
  }) {
    final candidates = <String?>[
      initialUrl,
      statusUrl,
      fallbackUrl,
    ];

    for (final candidate in candidates) {
      final url = candidate?.trim() ?? '';

      if (url.isNotEmpty) {
        return url;
      }
    }

    return '';
  }

  @override
  Widget build(BuildContext context) {
    final store = AlertStore.instance;

    return Scaffold(
      appBar: AppBar(
        title: const Text('실시간 모니터링'),
      ),
      body: AnimatedBuilder(
        animation: store,
        builder: (context, _) {
          final status = store.latestStatus;

          final streamUrl = _resolveStreamUrl(
            initialUrl: initialStreamUrl,
            statusUrl: status.cameraStreamUrl,
            fallbackUrl: AppConfig.fallbackCameraUrl,
          );

          return RefreshIndicator(
            onRefresh: store.refreshFromServer,
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.all(16),
              children: [
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(18),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          '현재 상태',
                          style: TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(height: 12),
                        Text('위치: ${status.room}'),
                        Text('행동 상태: ${status.posture}'),
                        Text(
                          '연결 상태: '
                          '${status.online ? '온라인' : '서버 연결 전'}',
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: streamUrl.isEmpty
                        ? const Padding(
                            padding: EdgeInsets.all(20),
                            child: Text(
                              '카메라 스트림 URL이 아직 설정되지 않았습니다.\n'
                              '서버에서 camera_stream_url 값을 내려주면 '
                              '이 화면에서 실시간 영상을 확인할 수 있습니다.',
                              textAlign: TextAlign.center,
                            ),
                          )
                        : Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                children: [
                                  const Expanded(
                                    child: Text(
                                      '카메라 화면',
                                      style: TextStyle(
                                        fontSize: 18,
                                        fontWeight: FontWeight.bold,
                                      ),
                                    ),
                                  ),
                                  Container(
                                    padding: const EdgeInsets.symmetric(
                                      horizontal: 8,
                                      vertical: 4,
                                    ),
                                    decoration: BoxDecoration(
                                      color: Colors.red,
                                      borderRadius: BorderRadius.circular(8),
                                    ),
                                    child: const Text(
                                      'LIVE',
                                      style: TextStyle(
                                        color: Colors.white,
                                        fontSize: 12,
                                        fontWeight: FontWeight.bold,
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 12),
                              AspectRatio(
                                aspectRatio: 16 / 9,
                                child: ClipRRect(
                                  borderRadius: BorderRadius.circular(12),
                                  child: Container(
                                    color: Colors.black,
                                    child: Mjpeg(
                                      key: ValueKey(streamUrl),
                                      stream: streamUrl,
                                      isLive: true,
                                      fit: BoxFit.cover,
                                      timeout:
                                          const Duration(seconds: 10),
                                      loading: (context) {
                                        return const ColoredBox(
                                          color: Colors.black,
                                          child: Center(
                                            child:
                                                CircularProgressIndicator(),
                                          ),
                                        );
                                      },
                                      error: (
                                        context,
                                        error,
                                        stackTrace,
                                      ) {
                                        return Container(
                                          color: Colors.black87,
                                          alignment: Alignment.center,
                                          child: const Padding(
                                            padding: EdgeInsets.all(16),
                                            child: Column(
                                              mainAxisSize:
                                                  MainAxisSize.min,
                                              children: [
                                                Icon(
                                                  Icons.videocam_off_outlined,
                                                  color: Colors.white70,
                                                  size: 42,
                                                ),
                                                SizedBox(height: 12),
                                                Text(
                                                  '실시간 카메라 영상을 '
                                                  '불러올 수 없습니다.\n'
                                                  '카메라 서버와 네트워크 '
                                                  '상태를 확인하세요.',
                                                  textAlign:
                                                      TextAlign.center,
                                                  style: TextStyle(
                                                    color: Colors.white,
                                                  ),
                                                ),
                                              ],
                                            ),
                                          ),
                                        );
                                      },
                                    ),
                                  ),
                                ),
                              ),
                              const SizedBox(height: 10),
                              const Text(
                                '스트림 URL',
                                style: TextStyle(
                                  fontSize: 12,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              const SizedBox(height: 4),
                              SelectableText(
                                streamUrl,
                                style: const TextStyle(
                                  fontSize: 12,
                                ),
                              ),
                            ],
                          ),
                  ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}
