import 'package:flutter/foundation.dart';

import '../models/alert_event.dart';
import '../models/latest_status.dart';
import 'api_client.dart';
import 'app_config.dart';

class CareApiService {
  CareApiService._internal();

  static final CareApiService instance = CareApiService._internal();

  ApiClient get _client => ApiClient(baseUrl: AppConfig.apiBaseUrl);

  Future<bool> healthCheck() async {
    try {
      await _client.getJson('/health');
      return true;
    } catch (error) {
      debugPrint('서버 연결 확인 실패: $error');
      return false;
    }
  }

  Future<LatestStatus?> fetchLatestStatus() async {
    try {
      final data = await _client.getJson('/status/latest');

      if (data is Map && data['status'] is Map) {
        return LatestStatus.fromJson(data['status'] as Map);
      }

      if (data is Map) {
        return LatestStatus.fromJson(data);
      }
    } catch (error) {
      debugPrint('최신 상태 조회 실패: $error');
    }

    return null;
  }

  Future<List<AlertEvent>> fetchEvents() async {
    try {
      final data = await _client.getJson('/events');

      if (data is List) {
        return data
            .whereType<Map>()
            .map(AlertEvent.fromJson)
            .toList();
      }

      if (data is Map && data['events'] is List) {
        return (data['events'] as List)
            .whereType<Map>()
            .map(AlertEvent.fromJson)
            .toList();
      }
    } catch (error) {
      debugPrint('이벤트 이력 조회 실패: $error');
    }

    return const [];
  }

  Future<bool> acknowledgeEvent(String eventId) async {
    try {
      await _client.patchJson(
        '/events/$eventId/ack',
        {
          'acknowledged': true,
          'guardian_id': AppConfig.guardianId,
        },
      );

      return true;
    } catch (error) {
      debugPrint('이벤트 확인 처리 서버 전송 실패: $error');
      return false;
    }
  }

  Future<bool> registerFcmToken({
    required String token,
    required String platform,
  }) async {
    try {
      await _client.postJson(
        '/devices/fcm-token',
        {
          'guardian_id': AppConfig.guardianId,
          'user_id': AppConfig.userId,
          'platform': platform,
          'fcm_token': token,
        },
      );

      return true;
    } catch (error) {
      debugPrint('FCM 토큰 서버 등록 실패: $error');
      return false;
    }
  }

  Future<String> sendChatQuestion(String question) async {
    try {
      final data = await _client.postJson(
        '/chat',
        {
          'guardian_id': AppConfig.guardianId,
          'user_id': AppConfig.userId,
          'question': question,
        },
      );

      if (data is Map) {
        final answer = data['answer'] ?? data['message'] ?? data['response'];

        if (answer != null && answer.toString().trim().isNotEmpty) {
          return answer.toString();
        }
      }
    } catch (error) {
      debugPrint('챗봇 요청 실패: $error');
    }

    return '현재 클라우드 서버 또는 LLM Agent에 연결되지 않았습니다. '
        '서버 API가 준비되면 사용자의 위치, 행동 상태, 충격 발생 여부, 호출 이력을 기반으로 답변할 수 있습니다.';
  }
}
