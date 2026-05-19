import 'package:firebase_messaging/firebase_messaging.dart';

class AlertEvent {
  final String id;
  final String title;
  final String body;

  final String location;
  final String deviceId;
  final String eventType;
  final String severity;

  final String? userId;
  final String? bodyPart;
  final String? posture;
  final double? confidence;
  final String? imageUrl;
  final String? streamUrl;

  final DateTime occurredAt;
  final bool acknowledged;

  const AlertEvent({
    required this.id,
    required this.title,
    required this.body,
    required this.location,
    required this.deviceId,
    required this.eventType,
    required this.severity,
    required this.occurredAt,
    required this.acknowledged,
    this.userId,
    this.bodyPart,
    this.posture,
    this.confidence,
    this.imageUrl,
    this.streamUrl,
  });

  factory AlertEvent.fromRemoteMessage(RemoteMessage message) {
    final data = message.data;
    final now = DateTime.now();

    return AlertEvent(
      id: _readString(
        data,
        keys: const ['event_id', 'id'],
        fallback: message.messageId ?? 'event_${now.millisecondsSinceEpoch}',
      ),
      title: message.notification?.title ??
          _readString(
            data,
            keys: const ['title'],
            fallback: '돌봄 알림 발생',
          ),
      body: message.notification?.body ??
          _readString(
            data,
            keys: const ['body', 'message', 'description'],
            fallback: '사용자 상태 확인이 필요합니다.',
          ),
      location: _readString(
        data,
        keys: const ['location', 'room', 'space'],
        fallback: '위치 정보 없음',
      ),
      deviceId: _readString(
        data,
        keys: const ['device_id', 'deviceId'],
        fallback: 'unknown_device',
      ),
      eventType: _readString(
        data,
        keys: const ['event_type', 'eventType', 'type'],
        fallback: 'help_request',
      ),
      severity: _readString(
        data,
        keys: const ['severity', 'level'],
        fallback: 'warning',
      ),
      userId: _readNullableString(data, const ['user_id', 'userId']),
      bodyPart: _readNullableString(data, const ['body_part', 'bodyPart']),
      posture: _readNullableString(data, const ['posture', 'pose']),
      confidence: _readDouble(data, const ['confidence', 'score']),
      imageUrl: _readNullableString(data, const ['image_url', 'imageUrl']),
      streamUrl: _readNullableString(data, const ['stream_url', 'streamUrl']),
      occurredAt: _parseDateTime(
            _readNullableString(
              data,
              const ['occurred_at', 'pressed_at', 'created_at', 'timestamp'],
            ),
          ) ??
          now,
      acknowledged: false,
    );
  }

  factory AlertEvent.fromJson(Map<String, dynamic> json) {
    final now = DateTime.now();

    return AlertEvent(
      id: _readString(
        json,
        keys: const ['event_id', 'id'],
        fallback: 'event_${now.millisecondsSinceEpoch}',
      ),
      title: _readString(
        json,
        keys: const ['title'],
        fallback: '돌봄 알림 발생',
      ),
      body: _readString(
        json,
        keys: const ['body', 'message', 'description'],
        fallback: '사용자 상태 확인이 필요합니다.',
      ),
      location: _readString(
        json,
        keys: const ['location', 'room', 'space'],
        fallback: '위치 정보 없음',
      ),
      deviceId: _readString(
        json,
        keys: const ['device_id', 'deviceId'],
        fallback: 'unknown_device',
      ),
      eventType: _readString(
        json,
        keys: const ['event_type', 'eventType', 'type'],
        fallback: 'help_request',
      ),
      severity: _readString(
        json,
        keys: const ['severity', 'level'],
        fallback: 'warning',
      ),
      userId: _readNullableString(json, const ['user_id', 'userId']),
      bodyPart: _readNullableString(json, const ['body_part', 'bodyPart']),
      posture: _readNullableString(json, const ['posture', 'pose']),
      confidence: _readDouble(json, const ['confidence', 'score']),
      imageUrl: _readNullableString(json, const ['image_url', 'imageUrl']),
      streamUrl: _readNullableString(json, const ['stream_url', 'streamUrl']),
      occurredAt: _parseDateTime(
            _readNullableString(
              json,
              const ['occurred_at', 'pressed_at', 'created_at', 'timestamp'],
            ),
          ) ??
          now,
      acknowledged: _readBool(
        json,
        keys: const ['acknowledged', 'is_acknowledged', 'checked'],
        fallback: false,
      ),
    );
  }

  AlertEvent copyWith({
    String? id,
    String? title,
    String? body,
    String? location,
    String? deviceId,
    String? eventType,
    String? severity,
    String? userId,
    String? bodyPart,
    String? posture,
    double? confidence,
    String? imageUrl,
    String? streamUrl,
    DateTime? occurredAt,
    bool? acknowledged,
  }) {
    return AlertEvent(
      id: id ?? this.id,
      title: title ?? this.title,
      body: body ?? this.body,
      location: location ?? this.location,
      deviceId: deviceId ?? this.deviceId,
      eventType: eventType ?? this.eventType,
      severity: severity ?? this.severity,
      userId: userId ?? this.userId,
      bodyPart: bodyPart ?? this.bodyPart,
      posture: posture ?? this.posture,
      confidence: confidence ?? this.confidence,
      imageUrl: imageUrl ?? this.imageUrl,
      streamUrl: streamUrl ?? this.streamUrl,
      occurredAt: occurredAt ?? this.occurredAt,
      acknowledged: acknowledged ?? this.acknowledged,
    );
  }

  bool get isImpactEvent {
    return eventType == 'impact_detected' ||
        eventType == 'fall_suspected' ||
        eventType == 'fall_detected';
  }

  bool get isCritical {
    return severity == 'critical' || isImpactEvent;
  }

  String get typeLabel {
    switch (eventType) {
      case 'help_request':
        return '도움 요청';
      case 'impact_detected':
        return '충격 감지';
      case 'fall_suspected':
        return '낙상 의심';
      case 'location_updated':
        return '위치 갱신';
      case 'posture_updated':
        return '행동 상태';
      case 'abnormal_state':
        return '이상 상태';
      default:
        return eventType;
    }
  }

  String get severityLabel {
    switch (severity) {
      case 'critical':
        return '긴급';
      case 'warning':
        return '주의';
      case 'info':
        return '정보';
      case 'normal':
        return '정상';
      default:
        return severity;
    }
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

  static double? _readDouble(
    Map<String, dynamic> data,
    List<String> keys,
  ) {
    for (final key in keys) {
      final value = data[key];
      if (value is num) return value.toDouble();
      if (value is String) return double.tryParse(value);
    }
    return null;
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
