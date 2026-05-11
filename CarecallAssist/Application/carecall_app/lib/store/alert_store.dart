import 'package:flutter/foundation.dart';

import '../models/alert_event.dart';

class AlertStore extends ChangeNotifier {
  AlertStore._internal();

  static final AlertStore instance = AlertStore._internal();

  final List<AlertEvent> _alerts = [];

  List<AlertEvent> get alerts => List.unmodifiable(_alerts);

  AlertEvent? get latestAlert {
    if (_alerts.isEmpty) return null;
    return _alerts.first;
  }

  int get unacknowledgedCount {
    return _alerts.where((alert) => !alert.acknowledged).length;
  }

  void addAlert(AlertEvent alert) {
    final existingIndex = _alerts.indexWhere((item) => item.id == alert.id);

    if (existingIndex >= 0) {
      _alerts[existingIndex] = alert;
    } else {
      _alerts.insert(0, alert);
    }

    notifyListeners();
  }

  AlertEvent? findById(String id) {
    try {
      return _alerts.firstWhere((alert) => alert.id == id);
    } catch (_) {
      return null;
    }
  }

  void acknowledge(String id) {
    final index = _alerts.indexWhere((alert) => alert.id == id);
    if (index < 0) return;

    _alerts[index] = _alerts[index].copyWith(acknowledged: true);
    notifyListeners();
  }
}
