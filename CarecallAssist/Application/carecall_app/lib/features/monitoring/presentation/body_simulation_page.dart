import 'package:flutter/material.dart';
import 'package:flutter_mjpeg/flutter_mjpeg.dart';

import '../../../services/app_config.dart';

class BodySimulationPage extends StatefulWidget {
  const BodySimulationPage({super.key});

  @override
  State<BodySimulationPage> createState() => _BodySimulationPageState();
}

class _BodySimulationPageState extends State<BodySimulationPage>
    with WidgetsBindingObserver {
  int _streamGeneration = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && mounted) {
      _reconnectStream();
    }
  }

  void _reconnectStream() {
    if (!mounted) return;

    setState(() {
      _streamGeneration += 1;
    });
  }

  @override
  Widget build(BuildContext context) {
    final streamUrl = AppConfig.simulationStreamUrl;

    return Scaffold(
      appBar: AppBar(
        title: const Text('신체 시뮬레이션'),
        actions: [
          IconButton(
            tooltip: '영상 다시 연결',
            onPressed: _reconnectStream,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Card(
              child: Padding(
                padding: EdgeInsets.all(16),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(
                      Icons.accessibility_new,
                      size: 32,
                    ),
                    SizedBox(width: 12),
                    Expanded(
                      child: Text(
                        'Jetson의 ROS2 데이터를 기반으로 생성된 '
                        '신체 시뮬레이션 화면입니다.',
                        style: TextStyle(
                          fontSize: 16,
                          height: 1.4,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            Card(
              clipBehavior: Clip.antiAlias,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Padding(
                    padding: EdgeInsets.fromLTRB(16, 16, 16, 12),
                    child: Row(
                      children: [
                        Expanded(
                          child: Text(
                            '실시간 신체 시뮬레이션',
                            style: TextStyle(
                              fontSize: 18,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                        _LiveBadge(),
                      ],
                    ),
                  ),
                  AspectRatio(
                    aspectRatio: 720 / 828,
                    child: ColoredBox(
                      color: Colors.black,
                      child: Mjpeg(
                        key: ValueKey(
                          '$streamUrl#$_streamGeneration',
                        ),
                        stream: streamUrl,
                        isLive: true,
                        fit: BoxFit.contain,
                        timeout: const Duration(seconds: 10),
                        loading: (context) {
                          return const ColoredBox(
                            color: Colors.black,
                            child: Center(
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  CircularProgressIndicator(
                                    color: Colors.white,
                                  ),
                                  SizedBox(height: 16),
                                  Text(
                                    '시뮬레이션 영상을 불러오는 중입니다.',
                                    style: TextStyle(
                                      color: Colors.white,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          );
                        },
                        error: (context, error, stackTrace) {
                          return ColoredBox(
                            color: Colors.black87,
                            child: Center(
                              child: Padding(
                                padding: const EdgeInsets.all(20),
                                child: Column(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    const Icon(
                                      Icons.videocam_off_outlined,
                                      color: Colors.white70,
                                      size: 48,
                                    ),
                                    const SizedBox(height: 14),
                                    const Text(
                                      '신체 시뮬레이션 영상을 '
                                      '불러올 수 없습니다.\n'
                                      'Jetson과 네트워크 상태를 확인하세요.',
                                      textAlign: TextAlign.center,
                                      style: TextStyle(
                                        color: Colors.white,
                                        height: 1.4,
                                      ),
                                    ),
                                    const SizedBox(height: 16),
                                    FilledButton.icon(
                                      onPressed: _reconnectStream,
                                      icon: const Icon(Icons.refresh),
                                      label: const Text('다시 연결'),
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          );
                        },
                      ),
                    ),
                  ),
                  const Padding(
                    padding: EdgeInsets.all(16),
                    child: Text(
                      '영상이 멈추거나 연결되지 않으면 '
                      '오른쪽 위의 새로고침 버튼을 눌러 주세요.',
                      style: TextStyle(
                        color: Colors.black54,
                        height: 1.4,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _LiveBadge extends StatelessWidget {
  const _LiveBadge();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: 9,
        vertical: 5,
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
    );
  }
}
