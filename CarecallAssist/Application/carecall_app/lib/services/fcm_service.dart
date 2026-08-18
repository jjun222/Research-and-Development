import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';

import '../models/alert_event.dart';
import '../store/alert_store.dart';
import 'care_api_service.dart';
import 'navigation_service.dart';

class FcmService {
  FcmService._internal();

  static final FcmService instance = FcmService._internal();

  final FirebaseMessaging _messaging = FirebaseMessaging.instance;

  bool _initialized = false;
  String? currentToken;

  Future<void> initialize() async {
    if (_initialized) return;
    _initialized = true;

    final settings = await _messaging.requestPermission(
      alert: true,
      badge: true,
      sound: true,
      provisional: false,
    );

    debugPrint('알림 권한 상태: ${settings.authorizationStatus}');

    currentToken = await _messaging.getToken();
    debugPrint(
      'FCM 토큰 발급 여부: ${currentToken != null && currentToken!.isNotEmpty}',
    );

    await registerCurrentToken();

    _messaging.onTokenRefresh.listen((newToken) async {
      currentToken = newToken;
      debugPrint('FCM 토큰 갱신 감지');
      await registerCurrentToken();
    });

    FirebaseMessaging.onMessage.listen((RemoteMessage message) {
      debugPrint('포그라운드 메시지 수신');
      debugPrint('제목: ${message.notification?.title}');
      debugPrint('내용: ${message.notification?.body}');
      debugPrint('데이터: ${message.data}');

      _handleIncomingMessage(
        message,
        openDetailPage: false,
      );
    });

    FirebaseMessaging.onMessageOpenedApp.listen((RemoteMessage message) {
      debugPrint('알림 클릭으로 앱 열림');
      debugPrint('데이터: ${message.data}');

      _handleIncomingMessage(
        message,
        openDetailPage: true,
      );
    });

    final initialMessage = await _messaging.getInitialMessage();

    if (initialMessage != null) {
      debugPrint('종료 상태에서 알림 클릭으로 앱 실행');

      _handleIncomingMessage(
        initialMessage,
        openDetailPage: true,
      );
    }
  }

  Future<String?> refreshToken() async {
    currentToken = await _messaging.getToken();
    debugPrint(
      'FCM 토큰 재조회 성공 여부: '
      '${currentToken != null && currentToken!.isNotEmpty}',
    );
    return currentToken;
  }

  Future<bool> registerCurrentToken() async {
    final token = currentToken ?? await refreshToken();

    if (token == null || token.isEmpty) {
      debugPrint('등록할 FCM 토큰이 없습니다.');
      return false;
    }

    final platform = defaultTargetPlatform.name;

    return CareApiService.instance.registerFcmToken(
      token: token,
      platform: platform,
    );
  }

  void _handleIncomingMessage(
    RemoteMessage message, {
    required bool openDetailPage,
  }) {
    final alert = AlertEvent.fromRemoteMessage(message);
    AlertStore.instance.addAlert(alert);
    AlertStore.instance.refreshFromServer();

    if (openDetailPage) {
      Future.delayed(const Duration(milliseconds: 300), () {
        NavigationService.navigatorKey.currentState?.pushNamed(
          '/alert-detail',
          arguments: alert.id,
        );
      });
    }
  }
}
