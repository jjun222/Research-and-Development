import 'package:flutter/material.dart';

import '../../../models/alert_event.dart';
import '../../../store/alert_store.dart';
import '../../../utils/korea_time_formatter.dart';

class AlertHistoryPage extends StatelessWidget {
  const AlertHistoryPage({super.key});

  IconData _iconFor(AlertEvent alert) {
    if (alert.eventType == 'help_request') return Icons.touch_app;
    if (alert.isImpactEvent) return Icons.personal_injury;
    if (alert.eventType == 'posture_updated') return Icons.accessibility_new;
    if (alert.eventType == 'location_updated') return Icons.location_on;
    return Icons.notifications;
  }

  Color _colorFor(AlertEvent alert) {
    if (alert.acknowledged) return Colors.green;
    if (alert.isCritical) return Colors.red;
    return Colors.orange;
  }

  @override
  Widget build(BuildContext context) {
    final store = AlertStore.instance;

    return Scaffold(
      appBar: AppBar(
        title: const Text('알림 이력'),
      ),
      body: AnimatedBuilder(
        animation: store,
        builder: (context, _) {
          final alerts = store.alerts;

          if (alerts.isEmpty) {
            return const Center(
              child: Text('알림 이력이 없습니다.'),
            );
          }

          return RefreshIndicator(
            onRefresh: store.refreshFromServer,
            child: ListView.separated(
              padding: const EdgeInsets.all(16),
              itemCount: alerts.length,
              separatorBuilder: (_, _) => const SizedBox(height: 8),
              itemBuilder: (context, index) {
                final alert = alerts[index];
                final occurredAt = KoreaTimeFormatter.formatDateTime(
                  alert.occurredAt,
                  includeYear: false,
                );

                return Card(
                  child: ListTile(
                    leading: Icon(
                      _iconFor(alert),
                      color: _colorFor(alert),
                    ),
                    title: Text('${alert.typeLabel} · ${alert.location}'),
                    subtitle: Text(
                      '$occurredAt · '
                      '${alert.severityLabel} · '
                      '${alert.acknowledged ? '확인 완료' : '미확인'}',
                    ),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () async {
                      final changed = await Navigator.pushNamed(
                        context,
                        '/alert-detail',
                        arguments: alert.id,
                      );

                      if (!context.mounted || changed != true) return;

                      await store.refreshFromServer();

                      if (!context.mounted) return;

                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(
                          content: Text('알림을 확인 처리했습니다.'),
                        ),
                      );
                    },
                  ),
                );
              },
            ),
          );
        },
      ),
    );
  }
}
