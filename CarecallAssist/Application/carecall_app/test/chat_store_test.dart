import 'dart:async';

import 'package:carecall_app/features/chat/presentation/chat_page.dart';
import 'package:carecall_app/store/chat_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('stores one user question and one assistant answer', () async {
    final store = ChatStore.forTesting(
      questionSender: (question) async => '서버 답변: $question',
    );

    expect(store.messages, hasLength(1));
    expect(store.messages.single.isUser, isFalse);

    final accepted = await store.sendMessage('  현재 어디에 있어?  ');

    expect(accepted, isTrue);
    expect(store.isSending, isFalse);
    expect(store.messages, hasLength(3));
    expect(store.messages[1].text, '현재 어디에 있어?');
    expect(store.messages[1].isUser, isTrue);
    expect(store.messages[2].text, '서버 답변: 현재 어디에 있어?');
    expect(store.messages[2].isUser, isFalse);

    store.dispose();
  });

  test('rejects another question while an answer is pending', () async {
    final answerCompleter = Completer<String>();
    final store = ChatStore.forTesting(
      questionSender: (_) => answerCompleter.future,
    );

    final firstRequest = store.sendMessage('첫 번째 질문');
    final secondAccepted = await store.sendMessage('두 번째 질문');

    expect(secondAccepted, isFalse);
    expect(store.isSending, isTrue);
    expect(store.messages.where((message) => message.isUser), hasLength(1));

    answerCompleter.complete('첫 번째 답변');
    expect(await firstRequest, isTrue);
    expect(store.isSending, isFalse);
    expect(store.messages.last.text, '첫 번째 답변');

    store.dispose();
  });

  testWidgets('keeps messages after leaving and reopening the chat page', (
    WidgetTester tester,
  ) async {
    final store = ChatStore.forTesting(
      questionSender: (_) async => '거실에 있습니다.',
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) {
            return Scaffold(
              body: Center(
                child: ElevatedButton(
                  onPressed: () {
                    Navigator.push(
                      context,
                      MaterialPageRoute<void>(
                        builder: (_) => ChatPage(chatStore: store),
                      ),
                    );
                  },
                  child: const Text('챗봇 열기'),
                ),
              ),
            );
          },
        ),
      ),
    );

    await tester.tap(find.text('챗봇 열기'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), '현재 어디에 있어?');
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(find.text('현재 어디에 있어?'), findsOneWidget);
    expect(find.text('거실에 있습니다.'), findsOneWidget);

    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.tap(find.text('챗봇 열기'));
    await tester.pumpAndSettle();

    expect(find.text('현재 어디에 있어?'), findsOneWidget);
    expect(find.text('거실에 있습니다.'), findsOneWidget);

    await tester.pageBack();
    await tester.pumpAndSettle();
    store.dispose();
  });
}
