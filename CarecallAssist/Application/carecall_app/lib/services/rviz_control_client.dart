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

class RvizCameraDeltaAck {
  const RvizCameraDeltaAck({
    required this.requestId,
    required this.status,
    required this.publishedSequence,
    required this.matchedRequest,
  });

  final String requestId;
  final String status;
  final int? publishedSequence;
  final bool matchedRequest;
}

class RvizControlClient {
  RvizControlClient({
    required String url,
    this.headersProvider,
    this.connectionTimeout = const Duration(seconds: 8),
    this.readyTimeout = const Duration(seconds: 5),
    List<Duration>? reconnectDelays,
  }) : endpoint = _parseEndpoint(url),
       reconnectDelays = _normalizeReconnectDelays(reconnectDelays);

  static const int _maximumTrackedRequestIds = 200;

  final Uri endpoint;
  final Duration connectionTimeout;
  final Duration readyTimeout;
  final List<Duration> reconnectDelays;
  final RvizControlHeadersProvider? headersProvider;

  final StreamController<RvizControlConnectionState>
  _connectionStateController =
      StreamController<RvizControlConnectionState>.broadcast();
  final StreamController<String> _errorController =
      StreamController<String>.broadcast();
  final StreamController<Object?> _messageController =
      StreamController<Object?>.broadcast();
  final StreamController<RvizCameraDeltaAck> _ackController =
      StreamController<RvizCameraDeltaAck>.broadcast();

  final LinkedHashSet<String> _pendingRequestIds = LinkedHashSet<String>();

  WebSocket? _socket;
  StreamSubscription<dynamic>? _socketSubscription;

  Timer? _reconnectTimer;
  Timer? _readyTimer;

  RvizControlConnectionState _connectionState =
      RvizControlConnectionState.disconnected;

  int _connectionGeneration = 0;
  int _nextReconnectIndex = 0;

  bool _shouldStayConnected = false;
  bool _isOpeningConnection = false;
  bool _disposed = false;

  RvizControlConnectionState get connectionState => _connectionState;

  Stream<RvizControlConnectionState> get connectionStateStream =>
      _connectionStateController.stream;

  Stream<String> get errorStream => _errorController.stream;

  Stream<Object?> get messageStream => _messageController.stream;

  Stream<RvizCameraDeltaAck> get ackStream => _ackController.stream;

  bool get isReady {
    return _connectionState == RvizControlConnectionState.ready &&
        _socket != null;
  }

  bool get isConnected => isReady;

  Future<void> connect() async {
    _throwIfDisposed();

    _shouldStayConnected = true;
    _nextReconnectIndex = 0;

    _reconnectTimer?.cancel();
    _reconnectTimer = null;

    if (_socket != null || _isOpeningConnection) {
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

    _readyTimer?.cancel();
    _readyTimer = null;

    _pendingRequestIds.clear();

    final subscription = _socketSubscription;
    final socket = _socket;

    _socketSubscription = null;
    _socket = null;

    _setConnectionState(RvizControlConnectionState.disconnected);

    await _closeResources(subscription, socket);
  }

  bool sendCameraDelta(RvizCameraCommand command) {
    if (_disposed || !isReady || !command.hasEffect) {
      return false;
    }

    final socket = _socket;

    if (socket == null) {
      return false;
    }

    try {
      final encodedMessage = jsonEncode(command.toJson());

      socket.add(encodedMessage);
      _trackPendingRequest(command.requestId);
      _debugLog('SEND $encodedMessage');

      return true;
    } catch (error) {
      _reportError('제어 명령 전송에 실패했습니다: $error');
      _setConnectionState(RvizControlConnectionState.error);
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
    await _ackController.close();
  }

  Future<void> _openConnection({required bool isReconnect}) async {
    if (_disposed ||
        !_shouldStayConnected ||
        _isOpeningConnection ||
        _socket != null) {
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
      _setConnectionState(
        RvizControlConnectionState.connectedWaitingReady,
      );
      _startReadyTimer(generation);

      _socketSubscription = socket.listen(
        (dynamic data) => _handleServerMessage(data, generation),
        onError: (Object error, StackTrace stackTrace) {
          if (_isCurrentConnection(generation)) {
            _reportError('제어 연결 오류가 발생했습니다: $error');
            _setConnectionState(RvizControlConnectionState.error);
          }
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
              _setConnectionState(RvizControlConnectionState.error);
            }
          }

          _handleConnectionLost(generation);
        },
        cancelOnError: true,
      );
    } on TimeoutException {
      if (_isCurrentConnection(generation)) {
        _reportError('제어 서버 연결 시간이 초과되었습니다.');
        _setConnectionState(RvizControlConnectionState.error);
        _handleConnectionLost(generation);
      }
    } on SocketException catch (error) {
      if (_isCurrentConnection(generation)) {
        _reportError('제어 서버에 연결할 수 없습니다: ${error.message}');
        _setConnectionState(RvizControlConnectionState.error);
        _handleConnectionLost(generation);
      }
    } on WebSocketException catch (error) {
      if (_isCurrentConnection(generation)) {
        _reportError('WebSocket 연결에 실패했습니다: ${error.message}');
        _setConnectionState(RvizControlConnectionState.error);
        _handleConnectionLost(generation);
      }
    } catch (error) {
      if (_isCurrentConnection(generation)) {
        _reportError('제어 연결을 만들 수 없습니다: $error');
        _setConnectionState(RvizControlConnectionState.error);
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

  void _handleServerMessage(dynamic data, int generation) {
    if (!_isCurrentConnection(generation)) {
      return;
    }

    final Object? decodedMessage;

    try {
      if (data is String) {
        decodedMessage = jsonDecode(data);
      } else if (data is List<int>) {
        decodedMessage = jsonDecode(utf8.decode(data));
      } else {
        _debugLog('IGNORE unsupported message: ${data.runtimeType}');
        return;
      }
    } on FormatException catch (error) {
      _debugLog('IGNORE invalid JSON: $error');
      return;
    }

    if (!_messageController.isClosed) {
      _messageController.add(decodedMessage);
    }

    if (decodedMessage is! Map) {
      _debugLog('IGNORE non-object JSON message');
      return;
    }

    final Map<String, dynamic> message;

    try {
      message = Map<String, dynamic>.from(decodedMessage);
    } catch (error) {
      _debugLog('IGNORE invalid JSON object: $error');
      return;
    }

    final type = message['type'];

    if (type is! String || type.isEmpty) {
      _debugLog('IGNORE message without type');
      return;
    }

    switch (type) {
      case 'ready':
        _handleReadyMessage(message, generation);
      case 'camera_delta_ack':
        _handleCameraDeltaAck(message);
      case 'error':
        _handleServerError(message);
      default:
        _debugLog('IGNORE unknown type=$type');
    }
  }

  void _handleReadyMessage(Map<String, dynamic> message, int generation) {
    if (!_isCurrentConnection(generation)) {
      return;
    }

    if (_connectionState !=
            RvizControlConnectionState.connectedWaitingReady &&
        _connectionState != RvizControlConnectionState.ready) {
      _debugLog('IGNORE ready in state=$_connectionState');
      return;
    }

    _readyTimer?.cancel();
    _readyTimer = null;
    _nextReconnectIndex = 0;

    _setConnectionState(RvizControlConnectionState.ready);
    _debugLog('READY ${jsonEncode(message)}');
  }

  void _handleCameraDeltaAck(Map<String, dynamic> message) {
    final requestIdValue = message['request_id'];
    final statusValue = message['status'];

    if (requestIdValue is! String ||
        requestIdValue.isEmpty ||
        statusValue is! String ||
        statusValue.isEmpty) {
      _debugLog('IGNORE malformed camera_delta_ack');
      return;
    }

    final publishedSequenceValue = message['published_sequence'];
    final publishedSequence =
        publishedSequenceValue is num
            ? publishedSequenceValue.toInt()
            : null;

    final matchedRequest = _pendingRequestIds.remove(requestIdValue);

    final ack = RvizCameraDeltaAck(
      requestId: requestIdValue,
      status: statusValue,
      publishedSequence: publishedSequence,
      matchedRequest: matchedRequest,
    );

    if (!_ackController.isClosed) {
      _ackController.add(ack);
    }

    _debugLog(
      'ACK request_id=${ack.requestId} '
      'status=${ack.status} '
      'published_sequence=${ack.publishedSequence} '
      'matched=${ack.matchedRequest}',
    );

    if (statusValue != 'published') {
      _reportError(
        '제어 명령이 발행되지 않았습니다: '
        'request_id=$requestIdValue status=$statusValue',
      );
    }
  }

  void _handleServerError(Map<String, dynamic> message) {
    final requestId = message['request_id'];
    final code = message['code'];
    final serverMessage = message['message'];

    if (requestId is String && requestId.isNotEmpty) {
      _pendingRequestIds.remove(requestId);
    }

    final details = <String>[
      if (code is String && code.isNotEmpty) 'code=$code',
      if (requestId is String && requestId.isNotEmpty)
        'request_id=$requestId',
      if (serverMessage is String && serverMessage.isNotEmpty) serverMessage,
    ];

    _reportError(
      details.isEmpty
          ? '제어 서버에서 오류를 반환했습니다.'
          : '제어 서버 오류: ${details.join(' ')}',
    );
  }

  void _startReadyTimer(int generation) {
    _readyTimer?.cancel();

    _readyTimer = Timer(readyTimeout, () {
      if (!_isCurrentConnection(generation) ||
          _connectionState !=
              RvizControlConnectionState.connectedWaitingReady) {
        return;
      }

      _reportError('제어 서버의 ready 메시지 수신 시간이 초과되었습니다.');
      _setConnectionState(RvizControlConnectionState.error);
      _handleConnectionLost(generation);
    });
  }

  void _trackPendingRequest(String requestId) {
    _pendingRequestIds.add(requestId);

    while (_pendingRequestIds.length > _maximumTrackedRequestIds) {
      _pendingRequestIds.remove(_pendingRequestIds.first);
    }
  }

  void _handleConnectionLost(int generation) {
    if (generation != _connectionGeneration) {
      return;
    }

    _connectionGeneration += 1;
    _isOpeningConnection = false;

    _readyTimer?.cancel();
    _readyTimer = null;

    _pendingRequestIds.clear();

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

    final delayIndex =
        _nextReconnectIndex < reconnectDelays.length
            ? _nextReconnectIndex
            : reconnectDelays.length - 1;
    final delay = reconnectDelays[delayIndex];

    if (_nextReconnectIndex < reconnectDelays.length - 1) {
      _nextReconnectIndex += 1;
    }

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

    _debugLog('ERROR $message');
  }

  void _debugLog(String message) {
    if (kDebugMode) {
      debugPrint('[RVIZ_CONTROL] $message');
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
        // 이미 종료된 구독은 추가로 처리하지 않습니다.
      }
    }

    if (socket != null) {
      try {
        await socket.close(WebSocketStatus.normalClosure, 'Client disconnect');
      } catch (_) {
        // 이미 종료된 소켓은 추가로 처리하지 않습니다.
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
