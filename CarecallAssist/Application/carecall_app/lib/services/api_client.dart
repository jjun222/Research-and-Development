import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'app_config.dart';

class ApiException implements Exception {
  final int? statusCode;
  final String message;

  const ApiException({
    required this.message,
    this.statusCode,
  });

  @override
  String toString() {
    if (statusCode == null) return 'ApiException: $message';
    return 'ApiException($statusCode): $message';
  }
}

class ApiClient {
  ApiClient({
    String? baseUrl,
    Duration? timeout,
  })  : baseUrl = AppConfig.normalizeBaseUrl(baseUrl ?? AppConfig.apiBaseUrl),
        timeout = timeout ?? const Duration(seconds: 5);

  final String baseUrl;
  final Duration timeout;

  Uri _buildUri(String path) {
    final normalizedPath = path.startsWith('/') ? path : '/$path';
    return Uri.parse('$baseUrl$normalizedPath');
  }

  Future<dynamic> getJson(String path) async {
    final client = HttpClient();
    try {
      final request = await client.getUrl(_buildUri(path)).timeout(timeout);
      request.headers.set(HttpHeaders.acceptHeader, 'application/json');

      final response = await request.close().timeout(timeout);
      return _decodeResponse(response);
    } on TimeoutException {
      throw const ApiException(message: '서버 응답 시간이 초과되었습니다.');
    } on SocketException {
      throw const ApiException(message: '서버에 연결할 수 없습니다.');
    } finally {
      client.close(force: true);
    }
  }

  Future<dynamic> postJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    return _sendJson(
      method: 'POST',
      path: path,
      body: body,
    );
  }

  Future<dynamic> patchJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    return _sendJson(
      method: 'PATCH',
      path: path,
      body: body,
    );
  }

  Future<dynamic> _sendJson({
    required String method,
    required String path,
    required Map<String, dynamic> body,
  }) async {
    final client = HttpClient();

    try {
      final uri = _buildUri(path);
      final request = await client.openUrl(method, uri).timeout(timeout);
      final encodedBody = utf8.encode(jsonEncode(body));

      request.headers.set(HttpHeaders.acceptHeader, 'application/json');
      request.headers.set(HttpHeaders.contentTypeHeader, 'application/json');
      request.contentLength = encodedBody.length;
      request.add(encodedBody);

      final response = await request.close().timeout(timeout);
      return _decodeResponse(response);
    } on TimeoutException {
      throw const ApiException(message: '서버 응답 시간이 초과되었습니다.');
    } on SocketException {
      throw const ApiException(message: '서버에 연결할 수 없습니다.');
    } finally {
      client.close(force: true);
    }
  }

  Future<dynamic> _decodeResponse(HttpClientResponse response) async {
    final responseBody = await utf8.decoder.bind(response).join();

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(
        statusCode: response.statusCode,
        message: responseBody.isEmpty ? 'API 요청 실패' : responseBody,
      );
    }

    if (responseBody.trim().isEmpty) {
      return null;
    }

    return jsonDecode(responseBody);
  }
}
