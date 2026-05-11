import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../../services/fcm_service.dart';

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  String? _token;

  @override
  void initState() {
    super.initState();
    _token = FcmService.instance.currentToken;
  }

  Future<void> _refreshToken() async {
    final token = await FcmService.instance.refreshToken();

    if (!mounted) return;

    setState(() {
      _token = token;
    });
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
              subtitle: Text('도움 요청 발생 시 보호자에게 푸시 알림을 표시합니다.'),
            ),
          ),
          const SizedBox(height: 12),
          const Card(
            child: ListTile(
              leading: Icon(Icons.router),
              title: Text('서버 연결'),
              subtitle: Text('Raspberry Pi 서버 연동은 다음 단계에서 추가합니다.'),
            ),
          ),
          const SizedBox(height: 12),
          Card(
            child: ExpansionTile(
              leading: const Icon(Icons.developer_mode),
              title: const Text('개발자용 FCM 토큰'),
              subtitle: const Text('테스트 메시지 전송 확인용'),
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
                  child: Row(
                    children: [
                      Expanded(
                        child: OutlinedButton(
                          onPressed: _refreshToken,
                          child: const Text('토큰 새로고침'),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: ElevatedButton(
                          onPressed: _copyToken,
                          child: const Text('토큰 복사'),
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
