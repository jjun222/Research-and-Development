import 'package:carecall_app/app/app.dart';
import 'package:carecall_app/models/alert_event.dart';
import 'package:carecall_app/store/alert_store.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('CareCall home screen renders', (WidgetTester tester) async {
    await tester.pumpWidget(const CareCallApp());

    expect(find.text('CareCall 보호자 앱'), findsOneWidget);
    expect(find.text('현재 상태 확인'), findsOneWidget);
    expect(find.text('상태 갱신'), findsOneWidget);
  });

  testWidgets('opens alert history from the home screen', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(const CareCallApp());

    final historyButton = find.text('알림 이력');
    await tester.ensureVisible(historyButton);
    await tester.tap(historyButton);
    await tester.pumpAndSettle();

    expect(find.text('알림 이력'), findsOneWidget);
    expect(find.text('알림 이력이 없습니다.'), findsOneWidget);
  });

  testWidgets('opens the selected alert detail from the home screen', (
    WidgetTester tester,
  ) async {
    const eventId = 'widget_test_event';

    AlertStore.instance.addAlert(
      AlertEvent(
        id: eventId,
        title: '테스트 알림',
        body: '상세 화면 이동 테스트입니다.',
        location: '거실',
        deviceId: 'test_device',
        eventType: 'fall_suspected',
        severity: 'critical',
        occurredAt: DateTime(2026, 8, 18, 16),
        acknowledged: false,
      ),
    );

    await tester.pumpWidget(const CareCallApp());

    final detailButton = find.text('상세 보기');
    await tester.ensureVisible(detailButton);
    await tester.tap(detailButton);
    await tester.pumpAndSettle();

    expect(find.text('알림 상세'), findsOneWidget);
    expect(find.text(eventId), findsOneWidget);
    expect(find.text('테스트 알림'), findsOneWidget);
  });
}
