import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../models/rviz_camera_command.dart';

enum RvizControlConnectionState {
  disconnected,
  connecting,
  connectedWaitingReady,
  ready,
  reconnecting,
  error,
}

typedef RvizControlHeadersProvider = FutureOr<Map<String, dynamic>> Function();

class RvizControlClient {
  RvizControlClient({
    required String url,
    this.headersProvider,
    this.connectionTimeout = const Duration(seconds: 8),
    List<Duration>? reconnectDelays,
  }) : endpoint = _parseEndpoint(url),
       reconnectDelays = _normalizeReconnectDelays(reconnectDelays);

  final Uri endpoint;
  final Duration connectionTimeout;
  final List<Duration> reconnectDelays;
  final RvizControlHeadersProvider? headersProvider;

  final StreamController<RvizControlConnectionState>
  _connectionStateController =
      StreamController<RvizControlConnectionState>.broadcast();

  final StreamController<String> _errorController =
      StreamController<String>.broadcast();

  final StreamController<Object?> _messageController =
      StreamController<Object?>.broadcast();

  WebSocket? _socket;
  StreamSubscription<dynamic>? _socketSubscription;

  Timer? _reconnectTimer;
  Timer? _stableConnectionTimer;

  RvizControlConnectionState _connectionState =
      RvizControlConnectionState.disconnected;

  int _connectionGeneration = 0;
  int _nextReconnectIndex = 0;

  bool _shouldStayConnected = false;
  bool _isOpeningConnection = false;
  bool _disposed = false;

  RvizControlConnectionState get connectionState => _connectionState;

  Stream<RvizControlConnectionState> get connectionStateStream {
    return _connectionStateController.stream;
  }

  Stream<String> get errorStream => _errorController.stream;

  Stream<Object?> get messageStream => _messageController.stream;

  bool get isConnected {
    return _connectionState == RvizControlConnectionState.connected &&
        _socket != null;
  }

  Future<void> connect() async {
    _throwIfDisposed();

    _shouldStayConnected = true;
    _nextReconnectIndex = 0;

    _reconnectTimer?.cancel();
    _reconnectTimer = null;

    if (isConnected || _isOpeningConnection) {
      return;
    }

    await _openConnection(isReconnect: false);
  }

  Future<void> disconnect() async {
    if (_disposed) return;

    _shouldStayConnected = false;
    _connectionGeneration += 1;
    _isOpeningConnection = false;
    _nextReconnectIndex = 0;

    _reconnectTimer?.cancel();
    _reconnectTimer = null;

    _stableConnectionTimer?.cancel();
    _stableConnectionTimer = null;

    final subscription = _socketSubscription;
    final socket = _socket;

    _socketSubscription = null;
    _socket = null;

    await _closeResources(subscription, socket);

    _setConnectionState(RvizControlConnectionState.disconnected);
  }

  bool sendCameraDelta(RvizCameraCommand command) {
    if (_disposed || !isConnected || !command.hasEffect) {
      return false;
    }

    final socket = _socket;

    if (socket == null) {
      return false;
    }

    try {
      socket.add(jsonEncode(command.toJson()));
      return true;
    } catch (error) {
      _reportError('제어 명령 전송에 실패했습니다: $error');
      _handleConnectionLost(_connectionGeneration);
      return false;
    }
  }

  Future<void> dispose() async {
    if (_disposed) return;

    await disconnect();

    _disposed = true;

    await _connectionStateController.close();
    await _errorController.close();
    await _messageController.close();
  }

  Future<void> _openConnection({required bool isReconnect}) async {
    if (_disposed ||
        !_shouldStayConnected ||
        _isOpeningConnection ||
        isConnected) {
      return;
    }

    _isOpeningConnection = true;

    final generation = _connectionGeneration + 1;
    _connectionGeneration = generation;

    _setConnectionState(
      isReconnect
          ? RvizControlConnectionState.reconnecting
          : RvizControlConnectionState.connecting,
    );

    try {
      final headers = await _loadHeaders();

      if (!_isCurrentConnection(generation)) {
        return;
      }

      final socket = await WebSocket.connect(
        endpoint.toString(),
        headers: headers.isEmpty ? null : headers,
      ).timeout(connectionTimeout);

      if (!_isCurrentConnection(generation)) {
        await socket.close(
          WebSocketStatus.normalClosure,
          'Connection no longer required',
        );
        return;
      }

      _socket = socket;

      _socketSubscription = socket.listen(
        _handleServerMessage,
        onError: (Object error, StackTrace stackTrace) {
          _reportError('제어 연결 오류가 발생했습니다: $error');
          _handleConnectionLost(generation);
        },
        onDone: () {
          if (_isCurrentConnection(generation)) {
            final closeCode = socket.closeCode;
            final closeReason = socket.closeReason;

            if (closeCode != WebSocketStatus.normalClosure) {
              _reportError(
                '제어 연결이 종료되었습니다.'
                '${closeCode == null ? '' : ' code=$closeCode'}'
                '${closeReason == null || closeReason.isEmpty ? '' : ' reason=$closeReason'}',
              );
            }
          }

          _handleConnectionLost(generation);
        },
        cancelOnError: true,
      );

      _setConnectionState(RvizControlConnectionState.connected);
      _startStableConnectionTimer(generation);
    } on TimeoutException {
      if (_isCurrentConnection(generation)) {
        _reportError('제어 서버 연결 시간이 초과되었습니다.');
        _handleConnectionLost(generation);
      }
    } on SocketException catch (error) {
      if (_isCurrentConnection(generation)) {
        _reportError('제어 서버에 연결할 수 없습니다: ${error.message}');
        _handleConnectionLost(generation);
      }
    } on WebSocketException catch (error) {
      if (_isCurrentConnection(generation)) {
        _reportError('WebSocket 연결에 실패했습니다: ${error.message}');
        _handleConnectionLost(generation);
      }
    } catch (error) {
      if (_isCurrentConnection(generation)) {
        _reportError('제어 연결을 만들 수 없습니다: $error');
        _handleConnectionLost(generation);
      }
    } finally {
      if (generation == _connectionGeneration) {
        _isOpeningConnection = false;
      }
    }
  }

  Future<Map<String, dynamic>> _loadHeaders() async {
    final provider = headersProvider;

    if (provider == null) {
      return const <String, dynamic>{};
    }

    final headers = await provider();

    return Map<String, dynamic>.unmodifiable(headers);
  }

  void _handleServerMessage(dynamic data) {
    Object? message = data;

    if (data is String) {
      try {
        message = jsonDecode(data);
      } on FormatException {
        message = data;
      }
    }

    if (!_messageController.isClosed) {
      _messageController.add(message);
    }
  }

  void _handleConnectionLost(int generation) {
    if (generation != _connectionGeneration) {
      return;
    }

    _connectionGeneration += 1;
    _isOpeningConnection = false;

    _stableConnectionTimer?.cancel();
    _stableConnectionTimer = null;

    final subscription = _socketSubscription;
    final socket = _socket;

    _socketSubscription = null;
    _socket = null;

    unawaited(_closeResources(subscription, socket));

    if (_disposed || !_shouldStayConnected) {
      _setConnectionState(RvizControlConnectionState.disconnected);
      return;
    }

    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (_disposed || !_shouldStayConnected) {
      _setConnectionState(RvizControlConnectionState.disconnected);
      return;
    }

    if (_nextReconnectIndex >= reconnectDelays.length) {
      _setConnectionState(RvizControlConnectionState.unavailable);
      return;
    }

    final delay = reconnectDelays[_nextReconnectIndex];
    _nextReconnectIndex += 1;

    _reconnectTimer?.cancel();

    _setConnectionState(RvizControlConnectionState.reconnecting);

    _reconnectTimer = Timer(delay, () {
      _reconnectTimer = null;

      if (_disposed || !_shouldStayConnected) {
        return;
      }

      unawaited(_openConnection(isReconnect: true));
    });
  }

  void _startStableConnectionTimer(int generation) {
    _stableConnectionTimer?.cancel();

    _stableConnectionTimer = Timer(const Duration(seconds: 10), () {
      if (_isCurrentConnection(generation) && isConnected) {
        _nextReconnectIndex = 0;
      }
    });
  }

  bool _isCurrentConnection(int generation) {
    return !_disposed &&
        _shouldStayConnected &&
        generation == _connectionGeneration;
  }

  void _setConnectionState(RvizControlConnectionState state) {
    if (_connectionState == state) {
      return;
    }

    _connectionState = state;

    if (!_connectionStateController.isClosed) {
      _connectionStateController.add(state);
    }
  }

  void _reportError(String message) {
    if (!_errorController.isClosed) {
      _errorController.add(message);
    }
  }

  Future<void> _closeResources(
    StreamSubscription<dynamic>? subscription,
    WebSocket? socket,
  ) async {
    if (subscription != null) {
      try {
        await subscription.cancel();
      } catch (_) {
        // 이미 종료된 구독은 추가로 처리할 필요가 없습니다.
      }
    }

    if (socket != null) {
      try {
        await socket.close(WebSocketStatus.normalClosure, 'Client disconnect');
      } catch (_) {
        // 이미 종료된 소켓은 추가로 처리할 필요가 없습니다.
      }
    }
  }

  void _throwIfDisposed() {
    if (_disposed) {
      throw StateError('이미 dispose된 RvizControlClient입니다.');
    }
  }

  static Uri _parseEndpoint(String value) {
    final normalized = value.trim();
    final uri = Uri.tryParse(normalized);

    if (uri == null ||
        !uri.hasScheme ||
        uri.host.isEmpty ||
        (uri.scheme != 'ws' && uri.scheme != 'wss')) {
      throw ArgumentError.value(value, 'url', '올바른 ws 또는 wss 주소여야 합니다.');
    }

    return uri;
  }

  static List<Duration> _normalizeReconnectDelays(List<Duration>? values) {
    final delays =
        values ??
        const <Duration>[
          Duration(seconds: 1),
          Duration(seconds: 2),
          Duration(seconds: 4),
          Duration(seconds: 8),
        ];

    if (delays.isEmpty) {
      throw ArgumentError('재접속 지연 목록은 비어 있을 수 없습니다.');
    }

    for (final delay in delays) {
      if (delay <= Duration.zero) {
        throw ArgumentError.value(
          delay,
          'reconnectDelays',
          '재접속 지연 시간은 0보다 커야 합니다.',
        );
      }
    }

    return List<Duration>.unmodifiable(delays);
  }
}
