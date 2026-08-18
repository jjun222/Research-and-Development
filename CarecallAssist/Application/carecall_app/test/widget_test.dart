import 'package:carecall_app/app/app.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('CareCall home screen renders', (WidgetTester tester) async {
    await tester.pumpWidget(const CareCallApp());

    expect(find.text('CareCall 보호자 앱'), findsOneWidget);
    expect(find.text('현재 상태 확인'), findsOneWidget);
    expect(find.text('상태 갱신'), findsOneWidget);
  });
}
