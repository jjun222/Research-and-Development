import 'package:flutter/material.dart';

import '../../../models/chat_message.dart';
import '../../../store/chat_store.dart';

class ChatPage extends StatefulWidget {
  final ChatStore? chatStore;

  const ChatPage({
    super.key,
    this.chatStore,
  });

  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> {
  final TextEditingController _controller = TextEditingController();

  ChatStore get _store => widget.chatStore ?? ChatStore.instance;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final text = _controller.text.trim();
    final store = _store;

    if (text.isEmpty || store.isSending) return;

    _controller.clear();
    await store.sendMessage(text);
  }

  @override
  Widget build(BuildContext context) {
    final store = _store;

    return Scaffold(
      appBar: AppBar(
        title: const Text('상태 확인 챗봇'),
      ),
      body: AnimatedBuilder(
        animation: store,
        builder: (context, _) {
          final messages = store.messages;

          return Column(
            children: [
              Expanded(
                child: ListView.separated(
                  reverse: true,
                  padding: const EdgeInsets.all(16),
                  itemCount: messages.length,
                  separatorBuilder: (_, _) => const SizedBox(height: 10),
                  itemBuilder: (context, index) {
                    final message = messages[messages.length - 1 - index];

                    return Align(
                      alignment: message.isUser
                          ? Alignment.centerRight
                          : Alignment.centerLeft,
                      child: _ChatBubble(message: message),
                    );
                  },
                ),
              ),
              if (store.isSending) const LinearProgressIndicator(),
              SafeArea(
                top: false,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
                  child: Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: _controller,
                          minLines: 1,
                          maxLines: 3,
                          textInputAction: TextInputAction.send,
                          onSubmitted: (_) => _send(),
                          decoration: const InputDecoration(
                            hintText: '보호 대상자 상태를 질문하세요.',
                            border: OutlineInputBorder(),
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      IconButton.filled(
                        onPressed: store.isSending ? null : _send,
                        icon: const Icon(Icons.send),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _ChatBubble extends StatelessWidget {
  final ChatMessage message;

  const _ChatBubble({
    required this.message,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      constraints: const BoxConstraints(maxWidth: 310),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: message.isUser
            ? Theme.of(context).colorScheme.primaryContainer
            : Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Text(message.text),
    );
  }
}
