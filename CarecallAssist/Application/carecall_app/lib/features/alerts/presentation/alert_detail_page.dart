import 'package:flutter/material.dart';

import '../../../store/alert_store.dart';

class AlertDetailPage extends StatelessWidget {
  final String? eventId;

  const AlertDetailPage({
    super.key,
    required this.eventId,
  });

  String _formatTime(DateTime time) {
    return '${time.year}-${time.month.toString().padLeft(2, '0')}-${time.day.toString().padLeft(2, '0')} '
        '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}:${time.second.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final store = AlertStore.instance;

    return Scaffold(
      appBar: AppBar(
        title: const Text('도움 요청 상세'),
      ),
      body: AnimatedBuilder(
        animation: store,
        builder: (context, _) {
          final alert =
              eventId == null ? null : store.findById(eventId!);

          if (alert == null) {
            return const Center(
              child: Text('도움 요청 정보를 찾을 수 없습니다.'),
            );
          }

          return Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Card(
                  color: alert.acknowledged
                      ? Colors.green.shade50
                      : Colors.red.shade50,
                  child: Padding(
                    padding: const EdgeInsets.all(20),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          alert.title,
                          style: const TextStyle(
                            fontSize: 24,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(height: 12),
                        Text(alert.body),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(18),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('위치: ${alert.location}'),
                        const SizedBox(height: 8),
                        Text('발생 시간: ${_formatTime(alert.occurredAt)}'),
                        const SizedBox(height: 8),
                        Text('장치 ID: ${alert.deviceId}'),
                        const SizedBox(height: 8),
                        Text('이벤트 ID: ${alert.id}'),
                        const SizedBox(height: 8),
                        Text(
                          '상태: ${alert.acknowledged ? '확인 완료' : '미확인'}',
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 20),
                ElevatedButton.icon(
                  onPressed: alert.acknowledged
                      ? null
                      : () {
                          store.acknowledge(alert.id);

                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(
                              content: Text('도움 요청을 확인 처리했습니다.'),
                            ),
                          );

                          // TODO: 나중에 Raspberry Pi 서버로 ACK 전송
                        },
                  icon: const Icon(Icons.check),
                  label: const Text('확인했습니다'),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}
