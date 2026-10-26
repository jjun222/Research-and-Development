import 'dart:async';

import '../models/rviz_camera_command.dart';
import 'rviz_control_client.dart';

class RvizCameraController {
  RvizCameraController({
    required this.client,
    this.sendInterval = const Duration(milliseconds: 50),
  }) {
    if (sendInterval <= Duration.zero) {
      throw ArgumentError.value(
        sendInterval,
        'sendInterval',
        '전송 간격은 0보다 커야 합니다.',
      );
    }
  }

  static const double zoomInMultiplier = 0.80;
  static const double zoomOutMultiplier = 1.25;

  final RvizControlClient client;
  final Duration sendInterval;

  Timer? _sendTimer;

  StreamSubscription<RvizControlConnectionState>? _connectionStateSubscription;

  bool _started = false;
  bool _disposed = false;

  double _pendingYawDelta = 0.0;
  double _pendingPitchDelta = 0.0;
  double _pendingZoomMultiplier = 1.0;

  int _requestSequence = 0;

  bool get isStarted => _started;

  bool get canAcceptCommands {
    return _started && !_disposed && client.isReady;
  }

  void start() {
    _throwIfDisposed();

    if (_started) {
      return;
    }

    _started = true;
    clearPendingCommands();

    _connectionStateSubscription = client.connectionStateStream.listen((state) {
      if (state != RvizControlConnectionState.ready) {
        clearPendingCommands();
      }
    });

    _sendTimer = Timer.periodic(sendInterval, (_) => _flushPendingCommands());
  }

  bool addRotationDelta({
    required double yawDelta,
    required double pitchDelta,
  }) {
    if (!canAcceptCommands) {
      return false;
    }

    if (!yawDelta.isFinite || !pitchDelta.isFinite) {
      return false;
    }

    _pendingYawDelta += yawDelta;
    _pendingPitchDelta += pitchDelta;

    return true;
  }

  bool addZoomMultiplier(double zoomMultiplier) {
    if (!canAcceptCommands) {
      return false;
    }

    if (!zoomMultiplier.isFinite || zoomMultiplier <= 0.0) {
      return false;
    }

    final combinedZoom = _pendingZoomMultiplier * zoomMultiplier;

    if (!combinedZoom.isFinite || combinedZoom <= 0.0) {
      return false;
    }

    _pendingZoomMultiplier = combinedZoom;

    return true;
  }

  bool zoomIn() {
    return addZoomMultiplier(zoomInMultiplier);
  }

  bool zoomOut() {
    return addZoomMultiplier(zoomOutMultiplier);
  }

  void clearPendingCommands() {
    _pendingYawDelta = 0.0;
    _pendingPitchDelta = 0.0;
    _pendingZoomMultiplier = 1.0;
  }

  Future<void> stop() async {
    if (!_started) {
      clearPendingCommands();
      return;
    }

    _started = false;

    _sendTimer?.cancel();
    _sendTimer = null;

    final connectionStateSubscription = _connectionStateSubscription;

    _connectionStateSubscription = null;

    if (connectionStateSubscription != null) {
      await connectionStateSubscription.cancel();
    }

    clearPendingCommands();
  }

  Future<void> dispose() async {
    if (_disposed) {
      return;
    }

    await stop();
    _disposed = true;
  }

  void _flushPendingCommands() {
    if (!_started || _disposed) {
      clearPendingCommands();
      return;
    }

    if (!client.isReady) {
      clearPendingCommands();
      return;
    }

    final yawDelta = _pendingYawDelta;
    final pitchDelta = _pendingPitchDelta;
    final zoomMultiplier = _pendingZoomMultiplier;

    clearPendingCommands();

    final command = RvizCameraCommand(
      requestId: _createRequestId(),
      yawDelta: yawDelta,
      pitchDelta: pitchDelta,
      zoomMultiplier: zoomMultiplier,
    );

    if (!command.hasEffect) {
      return;
    }

    client.sendCameraDelta(command);
  }

  String _createRequestId() {
    _requestSequence += 1;

    return '${DateTime.now().toUtc().microsecondsSinceEpoch}'
        '-$_requestSequence';
  }

  void _throwIfDisposed() {
    if (_disposed) {
      throw StateError('이미 dispose된 RvizCameraController입니다.');
    }
  }
}
