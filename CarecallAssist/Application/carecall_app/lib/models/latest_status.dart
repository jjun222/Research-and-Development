class LatestStatus {
  final String userId;
  final String room;
  final String posture;
  final String lastEventType;
  final String? bodyPart;
  final DateTime? lastImpactAt;
  final DateTime updatedAt;
  final String? cameraStreamUrl;
  final bool online;

  const LatestStatus({
    required this.userId,
    required this.room,
    required this.posture,
    required this.lastEventType,
    required this.updatedAt,
    required this.online,
    this.bodyPart,
    this.lastImpactAt,
    this.cameraStreamUrl,
  });

  factory LatestStatus.initial() {
    return LatestStatus(
      userId: 'user_01',
      room: '확인 전',
      posture: '확인 전',
      lastEventType: 'none',
      updatedAt: DateTime.now(),
      online: false,
    );
  }

  factory LatestStatus.fromJson(Map<String, dynamic> json) {
    return LatestStatus(
      userId: _readString(
        json,
        keys: const ['user_id', 'userId'],
        fallback: 'user_01',
      ),
      room: _readString(
        json,
        keys: const ['room', 'location', 'space'],
        fallback: '위치 정보 없음',
      ),
      posture: _readString(
        json,
        keys: const ['posture', 'pose', 'state'],
        fallback: '상태 정보 없음',
      ),
      lastEventType: _readString(
        json,
        keys: const ['last_event_type', 'lastEventType'],
        fallback: 'none',
      ),
      bodyPart: _readNullableString(json, const ['body_part', 'bodyPart']),
      lastImpactAt: _parseDateTime(
        _readNullableString(
          json,
          const ['last_impact_at', 'lastImpactAt'],
        ),
      ),
      updatedAt: _parseDateTime(
            _readNullableString(
              json,
              const ['updated_at', 'updatedAt', 'timestamp'],
            ),
          ) ??
          DateTime.now(),
      cameraStreamUrl: _readNullableString(
        json,
        const ['camera_stream_url', 'cameraStreamUrl', 'stream_url', 'streamUrl'],
      ),
      online: _readBool(
        json,
        keys: const ['online', 'is_online', 'connected'],
        fallback: true,
      ),
    );
  }

  LatestStatus copyWith({
    String? userId,
    String? room,
    String? posture,
    String? lastEventType,
    String? bodyPart,
    DateTime? lastImpactAt,
    DateTime? updatedAt,
    String? cameraStreamUrl,
    bool? online,
  }) {
    return LatestStatus(
      userId: userId ?? this.userId,
      room: room ?? this.room,
      posture: posture ?? this.posture,
      lastEventType: lastEventType ?? this.lastEventType,
      bodyPart: bodyPart ?? this.bodyPart,
      lastImpactAt: lastImpactAt ?? this.lastImpactAt,
      updatedAt: updatedAt ?? this.updatedAt,
      cameraStreamUrl: cameraStreamUrl ?? this.cameraStreamUrl,
      online: online ?? this.online,
    );
  }

  static String _readString(
    Map<String, dynamic> data, {
    required List<String> keys,
    required String fallback,
  }) {
    for (final key in keys) {
      final value = data[key];
      if (value != null && value.toString().trim().isNotEmpty) {
        return value.toString();
      }
    }
    return fallback;
  }

  static String? _readNullableString(
    Map<String, dynamic> data,
    List<String> keys,
  ) {
    for (final key in keys) {
      final value = data[key];
      if (value != null && value.toString().trim().isNotEmpty) {
        return value.toString();
      }
    }
    return null;
  }

  static bool _readBool(
    Map<String, dynamic> data, {
    required List<String> keys,
    required bool fallback,
  }) {
    for (final key in keys) {
      final value = data[key];
      if (value is bool) return value;
      if (value is String) {
        if (value.toLowerCase() == 'true') return true;
        if (value.toLowerCase() == 'false') return false;
      }
    }
    return fallback;
  }

  static DateTime? _parseDateTime(String? value) {
    if (value == null || value.isEmpty) return null;

    try {
      return DateTime.parse(value);
    } catch (_) {
      return null;
    }
  }
}
