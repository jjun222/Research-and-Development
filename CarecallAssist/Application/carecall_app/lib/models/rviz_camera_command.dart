class RvizCameraCommand {
  RvizCameraCommand._({
    required this.requestId,
    required this.yawDelta,
    required this.pitchDelta,
    required this.zoomMultiplier,
  });

  static const String commandType = 'camera_delta';

  static const double minimumRotationDelta = -0.5;
  static const double maximumRotationDelta = 0.5;
  static const double minimumZoomMultiplier = 0.5;
  static const double maximumZoomMultiplier = 2.0;

  static const double _comparisonEpsilon = 0.000000001;

  final String requestId;
  final double yawDelta;
  final double pitchDelta;
  final double zoomMultiplier;

  factory RvizCameraCommand({
    required String requestId,
    required double yawDelta,
    required double pitchDelta,
    required double zoomMultiplier,
  }) {
    final normalizedRequestId = requestId.trim();

    if (normalizedRequestId.isEmpty) {
      throw ArgumentError.value(
        requestId,
        'requestId',
        'requestId는 비어 있을 수 없습니다.',
      );
    }

    return RvizCameraCommand._(
      requestId: normalizedRequestId,
      yawDelta: _normalizeValue(
        yawDelta,
        minimum: minimumRotationDelta,
        maximum: maximumRotationDelta,
        fallback: 0.0,
      ),
      pitchDelta: _normalizeValue(
        pitchDelta,
        minimum: minimumRotationDelta,
        maximum: maximumRotationDelta,
        fallback: 0.0,
      ),
      zoomMultiplier: _normalizeValue(
        zoomMultiplier,
        minimum: minimumZoomMultiplier,
        maximum: maximumZoomMultiplier,
        fallback: 1.0,
      ),
    );
  }

  bool get hasEffect {
    final hasYaw = yawDelta.abs() > _comparisonEpsilon;
    final hasPitch = pitchDelta.abs() > _comparisonEpsilon;
    final hasZoom =
        (zoomMultiplier - 1.0).abs() > _comparisonEpsilon;

    return hasYaw || hasPitch || hasZoom;
  }

  Map<String, dynamic> toJson() {
    return <String, dynamic>{
      'type': commandType,
      'request_id': requestId,
      'yaw_delta': yawDelta,
      'pitch_delta': pitchDelta,
      'zoom_multiplier': zoomMultiplier,
    };
  }

  static double _normalizeValue(
    double value, {
    required double minimum,
    required double maximum,
    required double fallback,
  }) {
    if (!value.isFinite) {
      return fallback;
    }

    return value.clamp(minimum, maximum).toDouble();
  }
}
