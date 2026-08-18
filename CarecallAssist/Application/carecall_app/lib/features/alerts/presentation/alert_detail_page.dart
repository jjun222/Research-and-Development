import 'package:flutter/material.dart';

import '../../../store/alert_store.dart';

class AlertDetailPage extends StatefulWidget {
  final String? eventId;

  const AlertDetailPage({
    super.key,
    required this.eventId,
  });

  @override
  State<AlertDetailPage> createState() => _AlertDetailPageState();
}

class _AlertDetailPageState extends State<AlertDetailPage> {
  bool _submitting = false;

  String _formatTime(DateTime time) {
    return '${time.year}-${time.month.toString().padLeft(2, '0')}-${time.day.toString().padLeft(2, '0')} '
        '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}:${time.second.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final store = AlertStore.instance;

    return Scaffold(
      appBar: AppBar(
        title: const Text('알림 상세'),
      ),
      body: AnimatedBuilder(
        animation: store,
        builder: (context, _) {
          final alert = widget.eventId == null
              ? null
              : store.findById(widget.eventId!);

          if (alert == null) {
            return const Center(
              child: Text('알림 정보를 찾을 수 없습니다.'),
            );
          }

          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Card(
                color: alert.acknowledged
                    ? Colors.green.shade50
                    : alert.isCritical
                        ? Colors.red.shade50
                        : Colors.orange.shade50,
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
                      _DetailRow(label: '이벤트 종류', value: alert.typeLabel),
                      _DetailRow(label: '위험도', value: alert.severityLabel),
                      _DetailRow(label: '위치', value: alert.location),
                      if (alert.bodyPart != null)
                        _DetailRow(label: '충격 부위', value: alert.bodyPart!),
                      if (alert.posture != null)
                        _DetailRow(label: '행동 상태', value: alert.posture!),
                      if (alert.confidence != null)
                        _DetailRow(
                          label: 'AI 신뢰도',
                          value: alert.confidence!.toStringAsFixed(2),
                        ),
                      _DetailRow(
                        label: '발생 시간',
                        value: _formatTime(alert.occurredAt),
                      ),
                      _DetailRow(label: '장치 ID', value: alert.deviceId),
                      _DetailRow(label: '이벤트 ID', value: alert.id),
                      _DetailRow(
                        label: '확인 상태',
                        value: alert.acknowledged ? '확인 완료' : '미확인',
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
              if (alert.imageUrl != null)
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Image.network(
                      alert.imageUrl!,
                      errorBuilder: (context, error, stackTrace) {
                        return const Padding(
                          padding: EdgeInsets.all(20),
                          child: Text('이미지를 불러올 수 없습니다.'),
                        );
                      },
                    ),
                  ),
                ),
              if (alert.streamUrl != null) ...[
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: () {
                    Navigator.pushNamed(
                      context,
                      '/monitoring-with-url',
                      arguments: alert.streamUrl,
                    );
                  },
                  icon: const Icon(Icons.videocam),
                  label: const Text('카메라 화면 확인'),
                ),
              ],
              const SizedBox(height: 20),
              ElevatedButton.icon(
                onPressed: alert.acknowledged || _submitting
                    ? null
                    : () async {
                        setState(() {
                          _submitting = true;
                        });

                        final acknowledged =
                            await store.acknowledge(alert.id);

                        if (!context.mounted) return;

                        if (acknowledged) {
                          Navigator.pop(context, true);
                          return;
                        }

                        setState(() {
                          _submitting = false;
                        });

                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(
                            content: Text('알림 확인 처리에 실패했습니다.'),
                          ),
                        );
                      },
                icon: _submitting
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.check),
                label: Text(_submitting ? '처리 중...' : '확인했습니다'),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  final String label;
  final String value;

  const _DetailRow({
    required this.label,
    required this.value,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 95,
            child: Text(
              label,
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
          ),
          Expanded(
            child: Text(value),
          ),
        ],
      ),
    );
  }
}
