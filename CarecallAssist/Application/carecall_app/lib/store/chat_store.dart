import 'dart:collection';

import 'package:flutter/foundation.dart';

import '../models/chat_message.dart';
import '../services/care_api_service.dart';

typedef ChatQuestionSender = Future<String> Function(String question);

class ChatStore extends ChangeNotifier {
  ChatStore._internal({
    ChatQuestionSender? questionSender,
  }) : _questionSender =
            questionSender ?? CareApiService.instance.sendChatQuestion {
    _messages.add(ChatMessage.assistant(_welcomeMessage));
  }

  @visibleForTesting
  factory ChatStore.forTesting({
    required ChatQuestionSender questionSender,
  }) {
    return ChatStore._internal(questionSender: questionSender);
  }

  static final ChatStore instance = ChatStore._internal();

  static const String _welcomeMessage =
      '안녕하세요. 보호자용 상태 확인 챗봇입니다. '
      '예: "지금 어디에 있어?", "최근 충격이 있었어?", '
      '"현재 상태 알려줘"처럼 질문할 수 있습니다.';

  static const String _unexpectedErrorMessage =
      '챗봇 응답을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.';

  final ChatQuestionSender _questionSender;
  final List<ChatMessage> _messages = <ChatMessage>[];

  bool _sending = false;

  UnmodifiableListView<ChatMessage> get messages =>
      UnmodifiableListView<ChatMessage>(_messages);

  bool get isSending => _sending;

  Future<bool> sendMessage(String text) async {
    final question = text.trim();

    if (question.isEmpty || _sending) return false;

    _messages.add(ChatMessage.user(question));
    _sending = true;
    notifyListeners();

    try {
      final answer = (await _questionSender(question)).trim();

      _messages.add(
        ChatMessage.assistant(
          answer.isEmpty ? _unexpectedErrorMessage : answer,
        ),
      );
    } catch (error, stackTrace) {
      debugPrint('챗봇 저장소 요청 처리 실패: $error');
      debugPrintStack(stackTrace: stackTrace);
      _messages.add(ChatMessage.assistant(_unexpectedErrorMessage));
    } finally {
      _sending = false;
      notifyListeners();
    }

    return true;
  }
}
