class AppConfig {
  AppConfig._();

  static String apiBaseUrl = const String.fromEnvironment(
    'CARECALL_API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8000/api/v1',
  );

  static String userId = const String.fromEnvironment(
    'CARECALL_USER_ID',
    defaultValue: 'user_01',
  );

  static String guardianId = const String.fromEnvironment(
    'CARECALL_GUARDIAN_ID',
    defaultValue: 'guardian_01',
  );

  static const String fallbackCameraUrl = '';

  static String normalizeBaseUrl(String value) {
    final trimmed = value.trim();
    if (trimmed.endsWith('/')) {
      return trimmed.substring(0, trimmed.length - 1);
    }
    return trimmed;
  }
}
