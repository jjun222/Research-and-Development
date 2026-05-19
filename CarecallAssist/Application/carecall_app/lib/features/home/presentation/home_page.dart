import 'package:flutter/material.dart';

import '../../../models/alert_event.dart';
import '../../../models/latest_status.dart';
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

          return RefreshIndicator(
            onRefresh: store.refreshFromServer,
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                if (store.isLoading)
                  const LinearProgressIndicator(),
                if (store.isLoading)
                  const SizedBox(height: 12),
                _StatusCard(
                  status: store.latestStatus,
                  unacknowledgedCount: store.unacknowledgedCount,
                  formatTime: _formatTime,
                ),
                const SizedBox(height: 16),
                _QuickActionGrid(
                  onHistory: () => Navigator.pushNamed(context, '/history'),
                  onMonitoring: () => Navigator.pushNamed(context, '/monitoring'),
                  onChat: () => Navigator.pushNamed(context, '/chat'),
                  onRefresh: store.refreshFromServer,
                ),
                const SizedBox(height: 16),
                _LatestAlertCard(
                  alert: latestAlert,
                  formatTime: _formatTime,
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
  final LatestStatus status;
  final int unacknowledgedCount;
  final String Function(DateTime time) formatTime;

  const _StatusCard({
    required this.status,
    required this.unacknowledgedCount,
    required this.formatTime,
  });

  @override
  Widget build(BuildContext context) {
    final hasAlert = unacknowledgedCount > 0;

    return Card(
      color: hasAlert ? Colors.red.shade50 : Colors.green.shade50,
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  hasAlert ? Icons.warning_amber_rounded : Icons.check_circle,
                  color: hasAlert ? Colors.red : Colors.green,
                  size: 36,
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Text(
                    hasAlert ? '확인 필요한 알림 있음' : '현재 상태 확인',
                    style: const TextStyle(
                      fontSize: 21,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            _StatusRow(
              label: '현재 위치',
              value: status.room,
            ),
            _StatusRow(
              label: '행동 상태',
              value: status.posture,
            ),
            _StatusRow(
              label: '최근 이벤트',
              value: status.lastEventType,
            ),
            _StatusRow(
              label: '연결 상태',
              value: status.online ? '온라인' : '서버 연결 전',
            ),
            _StatusRow(
              label: '갱신 시간',
              value: formatTime(status.updatedAt),
            ),
            if (status.lastImpactAt != null)
              _StatusRow(
                label: '최근 충격',
                value: formatTime(status.lastImpactAt!),
              ),
            if (unacknowledgedCount > 0) ...[
              const SizedBox(height: 10),
              Text(
                '미확인 알림 $unacknowledgedCount건이 있습니다.',
                style: const TextStyle(
                  color: Colors.red,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _StatusRow extends StatelessWidget {
  final String label;
  final String value;

  const _StatusRow({
    required this.label,
    required this.value,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 90,
            child: Text(
              label,
              style: const TextStyle(fontWeight: FontWeight.w600),
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

class _QuickActionGrid extends StatelessWidget {
  final VoidCallback onHistory;
  final VoidCallback onMonitoring;
  final VoidCallback onChat;
  final Future<void> Function() onRefresh;

  const _QuickActionGrid({
    required this.onHistory,
    required this.onMonitoring,
    required this.onChat,
    required this.onRefresh,
  });

  @override
  Widget build(BuildContext context) {
    return GridView.count(
      shrinkWrap: true,
      crossAxisCount: 2,
      mainAxisSpacing: 10,
      crossAxisSpacing: 10,
      physics: const NeverScrollableScrollPhysics(),
      childAspectRatio: 1.65,
      children: [
        _QuickActionButton(
          icon: Icons.history,
          title: '알림 이력',
          onTap: onHistory,
        ),
        _QuickActionButton(
          icon: Icons.videocam,
          title: '실시간 확인',
          onTap: onMonitoring,
        ),
        _QuickActionButton(
          icon: Icons.chat_bubble_outline,
          title: '상태 챗봇',
          onTap: onChat,
        ),
        _QuickActionButton(
          icon: Icons.refresh,
          title: '상태 갱신',
          onTap: () {
            onRefresh();
          },
        ),
      ],
    );
  }
}

class _QuickActionButton extends StatelessWidget {
  final IconData icon;
  final String title;
  final VoidCallback onTap;

  const _QuickActionButton({
    required this.icon,
    required this.title,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: onTap,
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: 30),
            const SizedBox(height: 8),
            Text(
              title,
              style: const TextStyle(fontWeight: FontWeight.w600),
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
                '최근 알림',
                style: TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                ),
              ),
              SizedBox(height: 10),
              Text('아직 수신된 알림이 없습니다.'),
            ],
          ),
        ),
      );
    }

    final event = alert!;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              '최근 알림',
              style: TextStyle(
                fontSize: 18,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 12),
            Text(
              event.title,
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            Text('종류: ${event.typeLabel}'),
            Text('위치: ${event.location}'),
            if (event.bodyPart != null) Text('부위: ${event.bodyPart}'),
            if (event.posture != null) Text('행동 상태: ${event.posture}'),
            Text('시간: ${formatTime(event.occurredAt)}'),
            Text('확인 상태: ${event.acknowledged ? '확인 완료' : '미확인'}'),
            const SizedBox(height: 12),
            OutlinedButton(
              onPressed: () {
                Navigator.pushNamed(
                  context,
                  '/alert-detail',
                  arguments: event.id,
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
