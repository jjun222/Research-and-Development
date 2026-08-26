import 'package:carecall_app/utils/korea_time_formatter.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('formats an offset timestamp as Korea Standard Time', () {
    final time = DateTime.parse('2026-08-26T12:38:45.620919+09:00');

    expect(
      KoreaTimeFormatter.formatDateTime(time),
      '2026-08-26 12:38',
    );
    expect(
      KoreaTimeFormatter.formatDateTime(time, includeSeconds: true),
      '2026-08-26 12:38:45',
    );
  });

  test('formats a UTC timestamp as Korea Standard Time', () {
    final time = DateTime.parse('2026-08-26T03:38:45Z');

    expect(
      KoreaTimeFormatter.formatDateTime(time),
      '2026-08-26 12:38',
    );
  });

  test('handles a Korea Standard Time date rollover', () {
    final time = DateTime.parse('2026-12-31T16:15:00Z');

    expect(
      KoreaTimeFormatter.formatDateTime(time, includeSeconds: true),
      '2027-01-01 01:15:00',
    );
  });

  test('can omit the year for alert history rows', () {
    final time = DateTime.parse('2026-08-26T03:38:45Z');

    expect(
      KoreaTimeFormatter.formatDateTime(time, includeYear: false),
      '08-26 12:38',
    );
  });
}
