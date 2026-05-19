import 'package:flutter/material.dart';

import '../../../services/app_config.dart';
import '../../../store/alert_store.dart';

class CameraMonitoringPage extends StatelessWidget {
  final String? initialStreamUrl;

  const CameraMonitoringPage({
    super.key,
    this.initialStreamUrl,
  });

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
          final streamUrl = initialStreamUrl ??
              status.cameraStreamUrl ??
              AppConfig.fallbackCameraUrl;

          return RefreshIndicator(
            onRefresh: store.refreshFromServer,
            child: ListView(
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
                        Text('연결 상태: ${status.online ? '온라인' : '서버 연결 전'}'),
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
                              '서버에서 camera_stream_url 또는 stream_url 값을 내려주면 이 화면에서 확인할 수 있습니다.',
                            ),
                          )
                        : Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Text(
                                '카메라 화면',
                                style: TextStyle(
                                  fontSize: 18,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              const SizedBox(height: 12),
                              AspectRatio(
                                aspectRatio: 16 / 9,
                                child: ClipRRect(
                                  borderRadius: BorderRadius.circular(12),
                                  child: Image.network(
                                    streamUrl,
                                    fit: BoxFit.cover,
                                    gaplessPlayback: true,
                                    errorBuilder: (context, error, stackTrace) {
                                      return Container(
                                        color: Colors.black12,
                                        alignment: Alignment.center,
                                        child: const Padding(
                                          padding: EdgeInsets.all(16),
                                          child: Text(
                                            '카메라 화면을 불러올 수 없습니다.\n'
                                            'URL, 네트워크, 서버 상태를 확인하세요.',
                                            textAlign: TextAlign.center,
                                          ),
                                        ),
                                      );
                                    },
                                  ),
                                ),
                              ),
                              const SizedBox(height: 10),
                              SelectableText(
                                streamUrl,
                                style: const TextStyle(fontSize: 12),
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
