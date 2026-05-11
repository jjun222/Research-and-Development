import 'package:flutter/material.dart';

import '../../../store/alert_store.dart';

class AlertHistoryPage extends StatelessWidget {
  const AlertHistoryPage({super.key});

  String _formatTime(DateTime time) {
    return '${time.month.toString().padLeft(2, '0')}-${time.day.toString().padLeft(2, '0')} '
        '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final store = AlertStore.instance;

    return Scaffold(
      appBar: AppBar(
        title: const Text('호출 이력'),
      ),
      body: AnimatedBuilder(
        animation: store,
        builder: (context, _) {
          final alerts = store.alerts;

          if (alerts.isEmpty) {
            return const Center(
              child: Text('호출 이력이 없습니다.'),
            );
          }

          return ListView.separated(
            padding: const EdgeInsets.all(16),
            itemCount: alerts.length,
            separatorBuilder: (_, __) => const SizedBox(height: 8),
            itemBuilder: (context, index) {
              final alert = alerts[index];

              return Card(
                child: ListTile(
                  leading: Icon(
                    alert.acknowledged
                        ? Icons.check_circle
                        : Icons.warning_amber_rounded,
                    color: alert.acknowledged ? Colors.green : Colors.red,
                  ),
                  title: Text(alert.location),
                  subtitle: Text(
                    '${_formatTime(alert.occurredAt)} · '
                    '${alert.acknowledged ? '확인 완료' : '미확인'}',
                  ),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () {
                    Navigator.pushNamed(
                      context,
                      '/alert-detail',
                      arguments: alert.id,
                    );
                  },
                ),
              );
            },
          );
        },
      ),
    );
  }
}
