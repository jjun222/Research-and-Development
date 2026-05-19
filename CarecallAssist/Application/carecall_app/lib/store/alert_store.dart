import 'package:flutter/foundation.dart';

import '../models/alert_event.dart';
import '../models/latest_status.dart';
import '../services/care_api_service.dart';

class AlertStore extends ChangeNotifier {
  AlertStore._internal();

  static final AlertStore instance = AlertStore._internal();

  final List<AlertEvent> _alerts = [];

  bool _initialized = false;
  bool _loading = false;
  LatestStatus _latestStatus = LatestStatus.initial();

  List<AlertEvent> get alerts => List.unmodifiable(_alerts);

  bool get isLoading => _loading;

  LatestStatus get latestStatus => _latestStatus;

  AlertEvent? get latestAlert {
    if (_alerts.isEmpty) return null;
    return _alerts.first;
  }

  int get unacknowledgedCount {
    return _alerts.where((alert) => !alert.acknowledged).length;
  }

  Future<void> initialize() async {
    if (_initialized) return;
    _initialized = true;

    await refreshFromServer();
  }

  Future<void> refreshFromServer() async {
    _loading = true;
    notifyListeners();

    final fetchedStatus = await CareApiService.instance.fetchLatestStatus();
    final fetchedEvents = await CareApiService.instance.fetchEvents();

    if (fetchedStatus != null) {
      _latestStatus = fetchedStatus;
    }

    if (fetchedEvents.isNotEmpty) {
      _alerts
        ..clear()
        ..addAll(fetchedEvents);
      _sortAlerts();
    }

    _loading = false;
    notifyListeners();
  }

  void addAlert(AlertEvent alert) {
    final existingIndex = _alerts.indexWhere((item) => item.id == alert.id);

    if (existingIndex >= 0) {
      _alerts[existingIndex] = alert;
    } else {
      _alerts.insert(0, alert);
    }

    _sortAlerts();
    _updateLatestStatusFromAlert(alert);

    notifyListeners();
  }

  AlertEvent? findById(String id) {
    try {
      return _alerts.firstWhere((alert) => alert.id == id);
    } catch (_) {
      return null;
    }
  }

  Future<void> acknowledge(String id) async {
    final index = _alerts.indexWhere((alert) => alert.id == id);
    if (index < 0) return;

    _alerts[index] = _alerts[index].copyWith(acknowledged: true);
    notifyListeners();

    await CareApiService.instance.acknowledgeEvent(id);
  }

  void _sortAlerts() {
    _alerts.sort((a, b) => b.occurredAt.compareTo(a.occurredAt));
  }

  void _updateLatestStatusFromAlert(AlertEvent alert) {
    final hasValidLocation = alert.location.trim().isNotEmpty &&
        alert.location != '위치 정보 없음';

    _latestStatus = _latestStatus.copyWith(
      userId: alert.userId ?? _latestStatus.userId,
      room: hasValidLocation ? alert.location : _latestStatus.room,
      posture: alert.posture ?? _latestStatus.posture,
      lastEventType: alert.eventType,
      bodyPart: alert.bodyPart ?? _latestStatus.bodyPart,
      lastImpactAt: alert.isImpactEvent ? alert.occurredAt : _latestStatus.lastImpactAt,
      updatedAt: DateTime.now(),
      cameraStreamUrl: alert.streamUrl ?? _latestStatus.cameraStreamUrl,
      online: true,
    );
  }
}
