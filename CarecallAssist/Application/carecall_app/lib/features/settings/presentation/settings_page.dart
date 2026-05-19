import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../../services/app_config.dart';
import '../../../services/fcm_service.dart';
import '../../../store/alert_store.dart';

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  late final TextEditingController _baseUrlController;

  String? _token;
  bool _registering = false;

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

  Future<void> _refreshToken() async {
    final token = await FcmService.instance.refreshToken();

    if (!mounted) return;

    setState(() {
      _token = token;
    });
  }

  Future<void> _registerToken() async {
    setState(() {
      _registering = true;
    });

    final success = await FcmService.instance.registerCurrentToken();

    if (!mounted) return;

    setState(() {
      _registering = false;
      _token = FcmService.instance.currentToken;
    });

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          success ? 'FCM 토큰을 서버에 등록했습니다.' : 'FCM 토큰 서버 등록에 실패했습니다.',
        ),
      ),
    );
  }

  Future<void> _copyToken() async {
    final token = _token;

    if (token == null || token.isEmpty) return;

    await Clipboard.setData(ClipboardData(text: token));

    if (!mounted) return;

    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('FCM 토큰이 복사되었습니다.'),
      ),
    );
  }

  Future<void> _saveBaseUrl() async {
    final value = _baseUrlController.text.trim();

    if (!value.startsWith('http://') && !value.startsWith('https://')) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('서버 주소는 http:// 또는 https://로 시작해야 합니다.'),
        ),
      );
      return;
    }

    AppConfig.apiBaseUrl = AppConfig.normalizeBaseUrl(value);

    await AlertStore.instance.refreshFromServer();

    if (!mounted) return;

    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('서버 주소를 적용했습니다.'),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('설정'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Card(
            child: ListTile(
              leading: Icon(Icons.notifications),
              title: Text('알림 기능'),
              subtitle: Text('도움 요청, 충격 감지, 이상 상태 발생 시 FCM 알림을 수신합니다.'),
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
                      SizedBox(width: 10),
                      Text(
                        '클라우드 서버 API',
                        style: TextStyle(
                          fontSize: 17,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  TextField(
                    controller: _baseUrlController,
                    decoration: const InputDecoration(
                      labelText: 'API Base URL',
                      hintText: 'http://10.0.2.2:8000/api/v1',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 10),
                  ElevatedButton.icon(
                    onPressed: _saveBaseUrl,
                    icon: const Icon(Icons.save),
                    label: const Text('서버 주소 적용'),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '현재 적용 주소: ${AppConfig.apiBaseUrl}',
                    style: const TextStyle(fontSize: 12),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          Card(
            child: ListTile(
              leading: const Icon(Icons.person),
              title: const Text('보호 대상자/보호자 ID'),
              subtitle: Text(
                'userId: ${AppConfig.userId}\n'
                'guardianId: ${AppConfig.guardianId}',
              ),
            ),
          ),
          const SizedBox(height: 12),
          Card(
            child: ExpansionTile(
              leading: const Icon(Icons.developer_mode),
              title: const Text('개발자용 FCM 토큰'),
              subtitle: const Text('테스트 메시지 전송 및 서버 등록 확인용'),
              children: [
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: SelectableText(
                    _token ?? '토큰 없음',
                    style: const TextStyle(fontSize: 12),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      OutlinedButton(
                        onPressed: _refreshToken,
                        child: const Text('토큰 새로고침'),
                      ),
                      const SizedBox(height: 8),
                      OutlinedButton(
                        onPressed: _copyToken,
                        child: const Text('토큰 복사'),
                      ),
                      const SizedBox(height: 8),
                      ElevatedButton(
                        onPressed: _registering ? null : _registerToken,
                        child: Text(
                          _registering ? '등록 중...' : 'FCM 토큰 서버 등록',
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
