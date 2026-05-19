class ChatMessage {
  final String id;
  final String text;
  final bool isUser;
  final DateTime createdAt;

  const ChatMessage({
    required this.id,
    required this.text,
    required this.isUser,
    required this.createdAt,
  });

  factory ChatMessage.user(String text) {
    final now = DateTime.now();

    return ChatMessage(
      id: 'user_${now.microsecondsSinceEpoch}',
      text: text,
      isUser: true,
      createdAt: now,
    );
  }

  factory ChatMessage.assistant(String text) {
    final now = DateTime.now();

    return ChatMessage(
      id: 'assistant_${now.microsecondsSinceEpoch}',
      text: text,
      isUser: false,
      createdAt: now,
    );
  }
}
