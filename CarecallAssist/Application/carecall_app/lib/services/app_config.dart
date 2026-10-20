class AppConfig {
  AppConfig._();

  static String apiBaseUrl = const String.fromEnvironment(
    'CARECALL_API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8000/api/v1',
  );

  static String avatarBaseUrl = const String.fromEnvironment(
    'CARECALL_AVATAR_BASE_URL',
    defaultValue: 'https://3.132.163.179',
  );

  static String deviceId = const String.fromEnvironment(
    'CARECALL_DEVICE_ID',
    defaultValue: 'jetson_01',
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

  static String get simulationStreamUrl {
    return _buildAvatarHttpUri('stream.mjpg').toString();
  }

  static String get simulationHealthUrl {
    return _buildAvatarHttpUri('health').toString();
  }

  static String get simulationSnapshotUrl {
    return _buildAvatarHttpUri('snapshot.jpg').toString();
  }

  static String get simulationControlUrl {
    final httpUri = _buildAvatarHttpUri('control');

    final webSocketScheme = httpUri.scheme == 'https' ? 'wss' : 'ws';

    return httpUri.replace(scheme: webSocketScheme).toString();
  }

  static Uri _buildAvatarHttpUri(String endpoint) {
    final normalizedBaseUrl = normalizeBaseUrl(avatarBaseUrl);
    final normalizedDeviceId = deviceId.trim();

    if (normalizedBaseUrl.isEmpty) {
      throw StateError('CARECALL_AVATAR_BASE_URL이 설정되지 않았습니다.');
    }

    if (normalizedDeviceId.isEmpty) {
      throw StateError('CARECALL_DEVICE_ID가 설정되지 않았습니다.');
    }

    final baseUri = Uri.tryParse(normalizedBaseUrl);

    if (baseUri == null ||
        !baseUri.hasScheme ||
        baseUri.host.isEmpty ||
        (baseUri.scheme != 'http' && baseUri.scheme != 'https')) {
      throw StateError('CARECALL_AVATAR_BASE_URL은 올바른 HTTP 또는 HTTPS 주소여야 합니다.');
    }

    return Uri(
      scheme: baseUri.scheme,
      userInfo: baseUri.userInfo,
      host: baseUri.host,
      port: baseUri.hasPort ? baseUri.port : null,
      pathSegments: [
        ...baseUri.pathSegments.where((segment) => segment.isNotEmpty),
        'avatar',
        normalizedDeviceId,
        endpoint,
      ],
    );
  }

  static String normalizeBaseUrl(String value) {
    var normalized = value.trim();

    while (normalized.endsWith('/')) {
      normalized = normalized.substring(0, normalized.length - 1);
    }

    return normalized;
  }
}
