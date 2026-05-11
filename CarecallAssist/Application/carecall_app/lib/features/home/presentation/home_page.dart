import 'package:flutter/material.dart';

import '../../../models/alert_event.dart';
import '../../../store/alert_store.dart';

class HomePage extends StatelessWidget {
  const HomePage({super.key});

  String _formatTime(DateTime time) {
    return '${time.year}-${time.month.toString().padLeft(2, '0')}-${time.day.toString().padLeft(2, '0')} '
        '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final store = AlertStore.instance;

    return Scaffold(
      appBar: AppBar(
        title: const Text('CareCall 보호자 앱'),
        actions: [
          IconButton(
            onPressed: () {
              Navigator.pushNamed(context, '/settings');
            },
            icon: const Icon(Icons.settings),
          ),
        ],
      ),
      body: AnimatedBuilder(
        animation: store,
        builder: (context, _) {
          final latestAlert = store.latestAlert;

          return Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _StatusCard(
                  unacknowledgedCount: store.unacknowledgedCount,
                ),
                const SizedBox(height: 16),
                _LatestAlertCard(
                  alert: latestAlert,
                  formatTime: _formatTime,
                ),
                const SizedBox(height: 16),
                ElevatedButton.icon(
                  onPressed: () {
                    Navigator.pushNamed(context, '/history');
                  },
                  icon: const Icon(Icons.history),
                  label: const Text('호출 이력 보기'),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  final int unacknowledgedCount;

  const _StatusCard({
    required this.unacknowledgedCount,
  });

  @override
  Widget build(BuildContext context) {
    final hasAlert = unacknowledgedCount > 0;

    return Card(
      color: hasAlert ? Colors.red.shade50 : Colors.green.shade50,
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Row(
          children: [
            Icon(
              hasAlert ? Icons.warning_amber_rounded : Icons.check_circle,
              color: hasAlert ? Colors.red : Colors.green,
              size: 36,
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    hasAlert ? '미확인 도움 요청 있음' : '현재 상태 정상',
                    style: const TextStyle(
                      fontSize: 20,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    hasAlert
                        ? '확인하지 않은 요청 $unacknowledgedCount건이 있습니다.'
                        : '현재 확인이 필요한 도움 요청이 없습니다.',
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _LatestAlertCard extends StatelessWidget {
  final AlertEvent? alert;
  final String Function(DateTime time) formatTime;

  const _LatestAlertCard({
    required this.alert,
    required this.formatTime,
  });

  @override
  Widget build(BuildContext context) {
    if (alert == null) {
      return const Card(
        child: Padding(
          padding: EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '최근 도움 요청',
                style: TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                ),
              ),
              SizedBox(height: 10),
              Text('아직 수신된 도움 요청이 없습니다.'),
            ],
          ),
        ),
      );
    }

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              '최근 도움 요청',
              style: TextStyle(
                fontSize: 18,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 12),
            Text('위치: ${alert!.location}'),
            Text('시간: ${formatTime(alert!.occurredAt)}'),
            Text('상태: ${alert!.acknowledged ? '확인 완료' : '미확인'}'),
            const SizedBox(height: 12),
            OutlinedButton(
              onPressed: () {
                Navigator.pushNamed(
                  context,
                  '/alert-detail',
                  arguments: alert!.id,
                );
              },
              child: const Text('상세 보기'),
            ),
          ],
        ),
      ),
    );
  }
}
