import 'package:firebase_messaging/firebase_messaging.dart';

class AlertEvent {
  final String id;
  final String title;
  final String body;
  final String location;
  final String deviceId;
  final String eventType;
  final DateTime occurredAt;
  final bool acknowledged;

  const AlertEvent({
    required this.id,
    required this.title,
    required this.body,
    required this.location,
    required this.deviceId,
    required this.eventType,
    required this.occurredAt,
    required this.acknowledged,
  });

  factory AlertEvent.fromRemoteMessage(RemoteMessage message) {
    final data = message.data;

    final now = DateTime.now();

    return AlertEvent(
      id: data['event_id'] ??
          message.messageId ??
          'event_${now.millisecondsSinceEpoch}',
      title: message.notification?.title ??
          data['title'] ??
          '도움 요청 발생',
      body: message.notification?.body ??
          data['body'] ??
          '도움 요청 버튼이 눌렸습니다.',
      location: data['location'] ?? '위치 정보 없음',
      deviceId: data['device_id'] ?? 'unknown_device',
      eventType: data['event_type'] ?? 'help_request',
      occurredAt: _parseDateTime(data['pressed_at']) ?? now,
      acknowledged: false,
    );
  }

  AlertEvent copyWith({
    String? id,
    String? title,
    String? body,
    String? location,
    String? deviceId,
    String? eventType,
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
      occurredAt: occurredAt ?? this.occurredAt,
      acknowledged: acknowledged ?? this.acknowledged,
    );
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
