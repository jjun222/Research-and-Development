import 'package:flutter/material.dart';

import '../../../services/app_config.dart';

class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key});

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
              subtitle: Text(
                '도움 요청, 충격 감지, 낙상 의심, 이상 상태 발생 시 보호자 앱으로 알림을 수신합니다.',
              ),
            ),
          ),
          const SizedBox(height: 12),
          Card(
            child: ListTile(
              leading: const Icon(Icons.person),
              title: const Text('보호 대상자/보호자 정보'),
              subtitle: Text(
                '보호 대상자 ID: ${AppConfig.userId}\n'
                '보호자 ID: ${AppConfig.guardianId}',
              ),
            ),
          ),
          const SizedBox(height: 12),
          const Card(
            child: ListTile(
              leading: Icon(Icons.info_outline),
              title: Text('앱 안내'),
              subtitle: Text(
                'CareCall은 보호 대상자의 위치, 행동 상태, 도움 요청, 충격 발생 여부를 보호자가 확인할 수 있도록 지원하는 보호자용 앱입니다.',
              ),
            ),
          ),
          const SizedBox(height: 12),
          const Card(
            child: ListTile(
              leading: Icon(Icons.security),
              title: Text('개인정보 안내'),
              subtitle: Text(
                '카메라 화면, 위치 정보, 행동 상태 정보는 보호자 확인 목적에 맞게 제한적으로 사용해야 합니다.',
              ),
            ),
          ),
        ],
      ),
    );
  }
}
