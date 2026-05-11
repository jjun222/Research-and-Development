import 'package:flutter/material.dart';

import '../features/alerts/presentation/alert_detail_page.dart';
import '../features/alerts/presentation/alert_history_page.dart';
import '../features/home/presentation/home_page.dart';
import '../features/settings/presentation/settings_page.dart';
import '../services/navigation_service.dart';

class CareCallApp extends StatelessWidget {
  const CareCallApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'CareCall',
      debugShowCheckedModeBanner: false,
      navigatorKey: NavigationService.navigatorKey,
      theme: ThemeData(
        colorSchemeSeed: Colors.red,
        useMaterial3: true,
      ),
      home: const HomePage(),
      routes: {
        '/history': (_) => const AlertHistoryPage(),
        '/settings': (_) => const SettingsPage(),
      },
      onGenerateRoute: (settings) {
        if (settings.name == '/alert-detail') {
          final eventId = settings.arguments as String?;

          return MaterialPageRoute(
            builder: (_) => AlertDetailPage(eventId: eventId),
          );
        }

        return null;
      },
    );
  }
}
