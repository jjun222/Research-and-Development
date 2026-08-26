class KoreaTimeFormatter {
  KoreaTimeFormatter._();

  static const Duration _koreaUtcOffset = Duration(hours: 9);

  static String formatDateTime(
    DateTime time, {
    bool includeYear = true,
    bool includeSeconds = false,
  }) {
    final koreaTime = time.toUtc().add(_koreaUtcOffset);
    final buffer = StringBuffer();

    if (includeYear) {
      buffer
        ..write(koreaTime.year)
        ..write('-');
    }

    buffer
      ..write(_twoDigits(koreaTime.month))
      ..write('-')
      ..write(_twoDigits(koreaTime.day))
      ..write(' ')
      ..write(_twoDigits(koreaTime.hour))
      ..write(':')
      ..write(_twoDigits(koreaTime.minute));

    if (includeSeconds) {
      buffer
        ..write(':')
        ..write(_twoDigits(koreaTime.second));
    }

    return buffer.toString();
  }

  static String _twoDigits(int value) => value.toString().padLeft(2, '0');
}
