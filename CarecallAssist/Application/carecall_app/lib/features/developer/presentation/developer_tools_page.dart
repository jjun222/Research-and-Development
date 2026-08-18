import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../../models/alert_event.dart';
import '../../../services/app_config.dart';
import '../../../services/care_api_service.dart';
import '../../../services/fcm_service.dart';
import '../../../store/alert_store.dart';

class DeveloperToolsPage extends StatefulWidget {
  const DeveloperToolsPage({super.key});

  @override
  State<DeveloperToolsPage> createState() => _DeveloperToolsPageState();
}

class _DeveloperToolsPageState extends State<DeveloperToolsPage> {
  late final TextEditingController _baseUrlController;
  String _result = '아직 실행한 테스트가 없습니다.';
  bool _running = false;
  String? _token;

  @override
  void initState() {
    super.initState();
    _baseUrlController = TextEditingController(text: AppConfig.apiBaseUrl);
    _token = FcmService.instance.currentToken;
  }

  @override
  void dispose() {
    _baseUrlController.dispose();
    super.dispose();
  }

  Future<void> _run(
    String title,
    Future<String> Function() task,
  ) async {
    if (_running) return;

    setState(() {
      _running = true;
      _result = '$title 실행 중...';
    });

    try {
      final message = await task();
      if (!mounted) return;
      setState(() {
        _result = message;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _result = '$title 실패: $error';
      });
    } finally {
      if (mounted) {
        setState(() {
          _running = false;
        });
      }
    }
  }

  Future<String> _applyBaseUrl() async {
    final value = _baseUrlController.text.trim();

    if (!value.startsWith('http://') && !value.startsWith('https://')) {
      return 'API Base URL은 http:// 또는 https://로 시작해야 합니다.';
    }

    AppConfig.apiBaseUrl = AppConfig.normalizeBaseUrl(value);
    await AlertStore.instance.refreshFromServer();

    return '서버 주소를 적용했습니다.\n현재 주소: ${AppConfig.apiBaseUrl}';
  }

  Future<String> _healthCheck() async {
    final ok = await CareApiService.instance.healthCheck();

    if (ok) {
      return '서버 연결 성공: /health 응답 확인';
    }

    return '서버 연결 실패: API 주소, FastAPI 실행 상태, 방화벽을 확인하세요.';
  }

  Future<String> _testLatestStatus() async {
    final status = await CareApiService.instance.fetchLatestStatus();

    if (status == null) {
      return '/status/latest 응답을 받지 못했습니다.';
    }

    return '/status/latest 성공\n'
        'userId: ${status.userId}\n'
        'room: ${status.room}\n'
        'posture: ${status.posture}\n'
        'lastEventType: ${status.lastEventType}\n'
        'online: ${status.online}\n'
        'updatedAt: ${status.updatedAt}';
  }

  Future<String> _testEvents() async {
    final events = await CareApiService.instance.fetchEvents();

    return '/events 성공\n'
        '이벤트 개수: ${events.length}\n'
        '${events.isEmpty ? '이벤트 없음' : '최근 이벤트: ${events.first.title} / ${events.first.typeLabel}'}';
  }

  Future<String> _testChat() async {
    final answer = await CareApiService.instance.sendChatQuestion('현재 어디에 있어?');

    return '/chat 성공\n질문: 현재 어디에 있어?\n답변: $answer';
  }

  Future<String> _refreshToken() async {
    final token = await FcmService.instance.refreshToken();

    _token = token;

    if (token == null || token.isEmpty) {
      return 'FCM 토큰을 가져오지 못했습니다.';
    }

    return 'FCM 토큰 조회 성공\n$token';
  }

  Future<String> _copyToken() async {
    final token = _token ?? FcmService.instance.currentToken;

    if (token == null || token.isEmpty) {
      return '복사할 FCM 토큰이 없습니다. 먼저 토큰 새로고침을 실행하세요.';
    }

    await Clipboard.setData(ClipboardData(text: token));

    return 'FCM 토큰을 클립보드에 복사했습니다.';
  }

  Future<String> _registerToken() async {
    final token = _token ?? await FcmService.instance.refreshToken();

    if (token == null || token.isEmpty) {
      return '등록할 FCM 토큰이 없습니다.';
    }

    final success = await FcmService.instance.registerCurrentToken();

    return success ? 'FCM 토큰 서버 등록 성공' : 'FCM 토큰 서버 등록 실패';
  }

  Future<String> _addLocalTestAlert() async {
    final now = DateTime.now();

    final event = AlertEvent(
      id: 'local_test_${now.millisecondsSinceEpoch}',
      title: '로컬 테스트 알림',
      body: '개발자 화면에서 생성한 테스트 알림입니다.',
      location: '거실',
      deviceId: 'developer_tool',
      eventType: 'help_request',
      severity: 'warning',
      occurredAt: now,
      acknowledged: false,
      userId: AppConfig.userId,
    );

    AlertStore.instance.addAlert(event);

    return '로컬 테스트 알림을 추가했습니다.\n알림 이력 화면에서 확인하세요.';
  }

  Future<String> _refreshStore() async {
    await AlertStore.instance.refreshFromServer();

    return 'AlertStore 서버 갱신 완료\n'
        '최신 위치: ${AlertStore.instance.latestStatus.room}\n'
        '이벤트 개수: ${AlertStore.instance.alerts.length}';
  }

  Widget _buildButton({
    required IconData icon,
    required String label,
    required Future<String> Function() onRun,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: OutlinedButton.icon(
        onPressed: _running
            ? null
            : () {
                _run(label, onRun);
              },
        icon: Icon(icon),
        label: Text(label),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final token = _token ?? FcmService.instance.currentToken;

    return Scaffold(
      appBar: AppBar(
        title: const Text('개발자 도구'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Card(
            child: ListTile(
              leading: Icon(Icons.warning_amber_rounded),
              title: Text('개발자 전용 화면'),
              subtitle: Text(
                'API 연결, FCM 토큰, Mock 서버 연동, 테스트 알림 생성을 위한 화면입니다. 실제 배포 시에는 사용자에게 노출하지 않는 것이 좋습니다.',
              ),
            ),
          ),
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Row(
                    children: [
                      Icon(Icons.cloud),
                      SizedBox(width: 8),
                      Text(
                        'API Base URL',
                        style: TextStyle(
                          fontSize: 17,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _baseUrlController,
                    decoration: const InputDecoration(
                      labelText: 'API Base URL',
                      hintText: 'http://10.0.2.2:8000/api/v1',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '현재 적용 주소: ${AppConfig.apiBaseUrl}',
                    style: const TextStyle(fontSize: 12),
                  ),
                  const SizedBox(height: 12),
                  ElevatedButton.icon(
                    onPressed: _running
                        ? null
                        : () {
                            _run('서버 주소 적용', _applyBaseUrl);
                          },
                    icon: const Icon(Icons.save),
                    label: const Text('서버 주소 적용'),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Row(
                    children: [
                      Icon(Icons.science),
                      SizedBox(width: 8),
                      Text(
                        'API 테스트',
                        style: TextStyle(
                          fontSize: 17,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  _buildButton(
                    icon: Icons.check_circle_outline,
                    label: '서버 연결 확인: GET /health',
                    onRun: _healthCheck,
                  ),
                  _buildButton(
                    icon: Icons.home_outlined,
                    label: '홈 상태 확인: GET /status/latest',
                    onRun: _testLatestStatus,
                  ),
                  _buildButton(
                    icon: Icons.history,
                    label: '알림 이력 확인: GET /events',
                    onRun: _testEvents,
                  ),
                  _buildButton(
                    icon: Icons.chat_bubble_outline,
                    label: '챗봇 응답 확인: POST /chat',
                    onRun: _testChat,
                  ),
                  _buildButton(
                    icon: Icons.refresh,
                    label: 'AlertStore 전체 갱신',
                    onRun: _refreshStore,
                  ),
                  _buildButton(
                    icon: Icons.add_alert,
                    label: '로컬 테스트 알림 추가',
                    onRun: _addLocalTestAlert,
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          Card(
            child: ExpansionTile(
              leading: const Icon(Icons.developer_mode),
              title: const Text('FCM 토큰 테스트'),
              subtitle: const Text('토큰 조회, 복사, 서버 등록'),
              children: [
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: SelectableText(
                    token ?? '토큰 없음',
                    style: const TextStyle(fontSize: 12),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      _buildButton(
                        icon: Icons.refresh,
                        label: 'FCM 토큰 새로고침',
                        onRun: _refreshToken,
                      ),
                      _buildButton(
                        icon: Icons.copy,
                        label: 'FCM 토큰 복사',
                        onRun: _copyToken,
                      ),
                      _buildButton(
                        icon: Icons.cloud_upload,
                        label: 'FCM 토큰 서버 등록',
                        onRun: _registerToken,
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          Card(
            color: Colors.black87,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Text(
                _result,
                style: const TextStyle(
                  color: Colors.white,
                  fontFamily: 'monospace',
                ),
              ),
            ),
          ),
          if (_running) ...[
            const SizedBox(height: 12),
            const LinearProgressIndicator(),
          ],
        ],
      ),
    );
  }
}
