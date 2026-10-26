import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_mjpeg/flutter_mjpeg.dart';

import '../../../services/app_config.dart';
import '../../../services/rviz_camera_controller.dart';
import '../../../services/rviz_control_client.dart';

class BodySimulationPage extends StatefulWidget {
  const BodySimulationPage({super.key});

  @override
  State<BodySimulationPage> createState() => _BodySimulationPageState();
}

class _BodySimulationPageState extends State<BodySimulationPage>
    with WidgetsBindingObserver {
  static const double _yawSensitivity = 0.003;
  static const double _pitchSensitivity = 0.003;

  // 실제 Jetson 연결 후 회전 방향이 반대라면
  // 해당 방향 계수만 1.0과 -1.0 사이에서 변경합니다.
  static const double _yawSign = 1.0;
  static const double _pitchSign = -1.0;

  late final RvizControlClient _controlClient;
  late final RvizCameraController _cameraController;

  StreamSubscription<RvizControlConnectionState>?
  _controlStateSubscription;

  StreamSubscription<String>? _controlErrorSubscription;

  RvizControlConnectionState _controlState =
      RvizControlConnectionState.disconnected;

  String? _controlError;

  int _streamGeneration = 0;

  int _previousPointerCount = 0;
  double _previousScale = 1.0;

  @override
  void initState() {
    super.initState();

    WidgetsBinding.instance.addObserver(this);

    _controlClient = RvizControlClient(
      url: AppConfig.simulationControlUrl,
    );

    _cameraController = RvizCameraController(
      client: _controlClient,
    );

    _controlStateSubscription =
        _controlClient.connectionStateStream.listen(
          _handleControlStateChanged,
        );

    _controlErrorSubscription = _controlClient.errorStream.listen(
      _handleControlError,
    );

    _cameraController.start();
    unawaited(_controlClient.connect());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);

    unawaited(_disposeControlResources());

    super.dispose();
  }

  Future<void> _disposeControlResources() async {
    final controlStateSubscription = _controlStateSubscription;
    final controlErrorSubscription = _controlErrorSubscription;

    _controlStateSubscription = null;
    _controlErrorSubscription = null;

    if (controlStateSubscription != null) {
      await controlStateSubscription.cancel();
    }

    if (controlErrorSubscription != null) {
      await controlErrorSubscription.cancel();
    }

    await _cameraController.dispose();
    await _controlClient.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    switch (state) {
      case AppLifecycleState.resumed:
        if (!mounted) {
          return;
        }

        _reconnectStream();
        _resumeControl();

      case AppLifecycleState.inactive:
      case AppLifecycleState.hidden:
      case AppLifecycleState.paused:
      case AppLifecycleState.detached:
        _pauseControl();
    }
  }

  void _resumeControl() {
    _resetGestureTracking();
    _cameraController.start();
    unawaited(_controlClient.connect());
  }

  void _pauseControl() {
    _resetGestureTracking();
    _cameraController.clearPendingCommands();

    unawaited(_cameraController.stop());
    unawaited(_controlClient.disconnect());
  }

  void _handleControlStateChanged(
    RvizControlConnectionState state,
  ) {
    if (!mounted) {
      return;
    }

    setState(() {
      _controlState = state;

      if (state == RvizControlConnectionState.ready) {
        _controlError = null;
      }
    });
  }

  void _handleControlError(String message) {
    if (!mounted) {
      return;
    }

    setState(() {
      _controlError = message;
    });
  }

  void _retryControlConnection() {
    if (!mounted) {
      return;
    }

    setState(() {
      _controlError = null;
    });

    _cameraController.start();
    unawaited(_controlClient.connect());
  }

  void _reconnectStream() {
    if (!mounted) {
      return;
    }

    setState(() {
      _streamGeneration += 1;
    });
  }

  void _handleScaleStart(ScaleStartDetails details) {
    _previousPointerCount = 0;
    _previousScale = 1.0;
  }

  void _handleScaleUpdate(ScaleUpdateDetails details) {
    if (!_cameraController.canAcceptCommands) {
      _resetGestureTracking();
      return;
    }

    final pointerCount = details.pointerCount;

    if (pointerCount <= 0) {
      _resetGestureTracking();
      return;
    }

    // 한 손가락과 두 손가락 상태가 바뀌는 순간에는
    // 기준값만 갱신하여 시점이 갑자기 튀는 것을 막습니다.
    if (_previousPointerCount != pointerCount) {
      _previousPointerCount = pointerCount;
      _previousScale = details.scale;
      return;
    }

    if (pointerCount == 1) {
      final dragDelta = details.focalPointDelta;

      final yawDelta =
          dragDelta.dx * _yawSensitivity * _yawSign;

      final pitchDelta =
          dragDelta.dy * _pitchSensitivity * _pitchSign;

      _cameraController.addRotationDelta(
        yawDelta: yawDelta,
        pitchDelta: pitchDelta,
      );
    } else {
      final currentScale = details.scale;
      final previousScale = _previousScale;

      if (currentScale.isFinite &&
          previousScale.isFinite &&
          currentScale > 0.0 &&
          previousScale > 0.0) {
        final zoomMultiplier = previousScale / currentScale;

        _cameraController.addZoomMultiplier(
          zoomMultiplier,
        );
      }
    }

    _previousScale = details.scale;
  }

  void _handleScaleEnd(ScaleEndDetails details) {
    _resetGestureTracking();
  }

  void _resetGestureTracking() {
    _previousPointerCount = 0;
    _previousScale = 1.0;
  }

  void _zoomIn() {
    _cameraController.zoomIn();
  }

  void _zoomOut() {
    _cameraController.zoomOut();
  }

  @override
  Widget build(BuildContext context) {
    final streamUrl = AppConfig.simulationStreamUrl;

    final canControl =
        _controlState == RvizControlConnectionState.ready &&
        _cameraController.canAcceptCommands;

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
            const SizedBox(height: 12),
            _ControlStatusCard(
              state: _controlState,
              errorMessage: _controlError,
              onRetry: _retryControlConnection,
            ),
            const SizedBox(height: 16),
            Card(
              clipBehavior: Clip.antiAlias,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Padding(
                    padding: EdgeInsets.fromLTRB(
                      16,
                      16,
                      16,
                      12,
                    ),
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
                  GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onScaleStart:
                        canControl ? _handleScaleStart : null,
                    onScaleUpdate:
                        canControl ? _handleScaleUpdate : null,
                    onScaleEnd:
                        canControl ? _handleScaleEnd : null,
                    child: AspectRatio(
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
                  ),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(
                      16,
                      14,
                      16,
                      8,
                    ),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(
                          Icons.touch_app_outlined,
                          color:
                              canControl
                                  ? Colors.blue
                                  : Colors.black38,
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            canControl
                                ? '한 손가락으로 드래그하여 '
                                    '시점을 회전하고, 두 손가락으로 '
                                    '확대하거나 축소할 수 있습니다.'
                                : '제어 서버가 연결되면 '
                                    '드래그와 핀치 조작을 '
                                    '사용할 수 있습니다.',
                            style: TextStyle(
                              color:
                                  canControl
                                      ? Colors.black87
                                      : Colors.black54,
                              height: 1.4,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(
                      16,
                      4,
                      16,
                      12,
                    ),
                    child: Row(
                      children: [
                        const Expanded(
                          child: Text(
                            'RViz 카메라 거리',
                            style: TextStyle(
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                        IconButton.filledTonal(
                          tooltip: '줌 인',
                          onPressed: canControl ? _zoomIn : null,
                          icon: const Icon(Icons.add),
                        ),
                        const SizedBox(width: 8),
                        IconButton.filledTonal(
                          tooltip: '줌 아웃',
                          onPressed: canControl ? _zoomOut : null,
                          icon: const Icon(Icons.remove),
                        ),
                      ],
                    ),
                  ),
                  const Padding(
                    padding: EdgeInsets.fromLTRB(
                      16,
                      0,
                      16,
                      16,
                    ),
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

class _ControlStatusCard extends StatelessWidget {
  const _ControlStatusCard({
    required this.state,
    required this.errorMessage,
    required this.onRetry,
  });

  final RvizControlConnectionState state;
  final String? errorMessage;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    late final Color color;
    late final Widget leading;
    late final String title;
    late final String description;
    late final bool showRetryButton;

    switch (state) {
      case RvizControlConnectionState.disconnected:
        color = Colors.grey;
        leading = const Icon(
          Icons.link_off,
          color: Colors.grey,
        );
        title = '현재 화면 제어를 사용할 수 없습니다.';
        description = '제어 서버 연결이 끊어져 있습니다.';
        showRetryButton = true;

      case RvizControlConnectionState.connecting:
        color = Colors.blue;
        leading = const SizedBox(
          width: 22,
          height: 22,
          child: CircularProgressIndicator(
            strokeWidth: 2.5,
          ),
        );
        title = '시뮬레이션 제어 연결 중';
        description = '제어 서버에 연결하고 있습니다.';
        showRetryButton = false;

      case RvizControlConnectionState.connectedWaitingReady:
        color = Colors.blue;
        leading = const SizedBox(
          width: 22,
          height: 22,
          child: CircularProgressIndicator(
            strokeWidth: 2.5,
          ),
        );
        title = '제어 서버 준비 확인 중';
        description = 'WebSocket 연결 후 ready 메시지를 기다리고 있습니다.';
        showRetryButton = false;

      case RvizControlConnectionState.ready:
        color = Colors.green;
        leading = const Icon(
          Icons.check_circle_outline,
          color: Colors.green,
        );
        title = '시뮬레이션 제어 연결됨';
        description = '드래그, 핀치 및 줌 버튼을 사용할 수 있습니다.';
        showRetryButton = false;

      case RvizControlConnectionState.reconnecting:
        color = Colors.orange;
        leading = const SizedBox(
          width: 22,
          height: 22,
          child: CircularProgressIndicator(
            strokeWidth: 2.5,
            color: Colors.orange,
          ),
        );
        title = '시뮬레이션 제어 재연결 중';
        description = '잠시 후 다시 연결을 시도합니다.';
        showRetryButton = false;

      case RvizControlConnectionState.error:
        color = Colors.redAccent;
        leading = const Icon(
          Icons.error_outline,
          color: Colors.redAccent,
        );
        title = '현재 화면 제어를 사용할 수 없습니다.';
        description = '제어 서버 상태를 확인한 후 다시 시도하세요.';
        showRetryButton = true;
    }

    return Card(
      color: color.withValues(alpha: 0.08),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: leading,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: TextStyle(
                      color: color,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    description,
                    style: const TextStyle(
                      color: Colors.black54,
                      height: 1.35,
                    ),
                  ),
                  if (errorMessage != null) ...[
                    const SizedBox(height: 6),
                    Text(
                      errorMessage!,
                      style: const TextStyle(
                        color: Colors.black45,
                        fontSize: 12,
                        height: 1.3,
                      ),
                    ),
                  ],
                ],
              ),
            ),
            if (showRetryButton) ...[
              const SizedBox(width: 8),
              TextButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.refresh),
                label: const Text('재시도'),
              ),
            ],
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
