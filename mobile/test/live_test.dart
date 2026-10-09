import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:arienx_mobile/core/cloud.dart';
import 'package:arienx_mobile/core/live.dart';
import 'package:arienx_mobile/features/live_screen.dart';
import 'cloud_test.dart' show MemoryVault;

class FakeAudio implements LiveAudio {
  final input = StreamController<dynamic>.broadcast(sync: true);
  final played = <Uint8List>[];
  final muting = <bool>[];
  bool capture = false;
  int cleared = 0, stopped = 0;
  @override
  Stream<dynamic> get events => input.stream;
  @override
  Future<void> start({bool capture = true}) async {
    this.capture = capture;
  }

  @override
  Future<void> play(Uint8List bytes) async {
    played.add(bytes);
  }

  @override
  Future<void> clear() async {
    cleared++;
  }

  @override
  Future<void> mute(bool value) async {
    muting.add(value);
  }

  @override
  Future<void> speaker(bool value) async {}
  @override
  Future<void> stop() async {
    stopped++;
    capture = false;
  }
}

class FakeWire implements LiveWire {
  final stream = StreamController<dynamic>.broadcast(sync: true);
  final sent = <Map<String, dynamic>>[];
  @override
  Stream<dynamic> get messages => stream.stream;
  void receive(Map<String, dynamic> value) => stream.add(jsonEncode(value));
  @override
  void send(Map<String, dynamic> message) {
    sent.add(message);
    if (message.containsKey('setup')) receive({'setupComplete': {}});
  }

  @override
  Future<void> close() async {}
}

class Fixture {
  final audio = FakeAudio(), wire = FakeWire(), vault = MemoryVault();
  late final CloudRepository repo;
  late final LiveCall call;
  final id = 'c' * 32;
  Map<String, dynamic> chat = {'id': 'c' * 32, 'revision': 0, 'messages': []};
  Fixture() {
    repo =
        CloudRepository(
            vault,
            client: MockClient((request) async {
              expect(request.headers['x-arienx-session'], 'test-session');
              if (request.url.path == '/api/mobile/live') {
                expect(jsonDecode(request.body), {
                  'id': id,
                  'revision': chat['revision'],
                });
                return http.Response(
                  jsonEncode({
                    'token': 'auth_tokens/test',
                    'voice': 'Charon',
                    'websocket': liveEndpoint,
                    'setup': {'model': 'models/test'},
                  }),
                  200,
                );
              }
              if (request.url.path == '/api/conversations' &&
                  request.method == 'PATCH') {
                final body = jsonDecode(request.body);
                expect(body['revision'], chat['revision']);
                chat = {
                  ...chat,
                  'messages': body['messages'],
                  'revision': chat['revision'] + 1,
                };
              }
              return http.Response(
                jsonEncode(
                  request.url.path == '/api/conversations' &&
                          request.url.hasQuery
                      ? chat
                      : request.url.path == '/api/conversations' &&
                            request.method == 'PATCH'
                      ? chat
                      : {},
                ),
                200,
              );
            }),
          )
          ..token = 'test-session'
          ..online = true;
    call = LiveCall(
      repo: repo,
      id: id,
      audio: audio,
      connect: (endpoint, token) async {
        expect(endpoint, liveEndpoint);
        expect(token, 'auth_tokens/test');
        return wire;
      },
    );
  }
  Future<void> close() async {
    await call.end();
    await call.saveTail;
    call.dispose();
    repo.dispose();
    await audio.input.close();
    await wire.stream.close();
  }
}

Future<void> tick() => Future<void>.delayed(const Duration(milliseconds: 20));
void main() {
  test(
    'live sends PCM immediately without a transcript or manual Send',
    () async {
      final f = Fixture();
      addTearDown(f.close);
      await f.call.start();
      expect(f.audio.capture, true);
      final bytes = Uint8List.fromList([1, 0, 2, 0]);
      f.audio.input.add({'type': 'mic', 'data': bytes, 'level': .1});
      expect(f.wire.sent.last, {
        'realtimeInput': {
          'audio': {
            'mimeType': 'audio/pcm;rate=16000',
            'data': base64Encode(bytes),
          },
        },
      });
      await f.call.setMuted(true);
      final count = f.wire.sent.length;
      f.audio.input.add({'type': 'mic', 'data': bytes, 'level': .1});
      expect(f.wire.sent.length, count);
      expect(f.audio.muting.last, true);
      await f.call.setMuted(false);
      expect(f.audio.muting.last, false);
    },
  );
  test(
    'native audio plays and interruptions discard the playback queue',
    () async {
      final f = Fixture();
      addTearDown(f.close);
      await f.call.start();
      f.wire.receive({
        'serverContent': {
          'modelTurn': {
            'parts': [
              {
                'inlineData': {
                  'mimeType': 'audio/pcm;rate=24000',
                  'data': base64Encode([1, 0, 2, 0]),
                },
              },
            ],
          },
        },
      });
      await tick();
      expect(f.audio.played.single, [1, 0, 2, 0]);
      expect(f.call.phase, CallPhase.speaking);
      f.wire.receive({
        'serverContent': {'interrupted': true},
      });
      await tick();
      expect(f.audio.cleared, 1);
      expect(f.call.phase, CallPhase.listening);
    },
  );
  test(
    'live transcripts append through revision-protected shared conversations',
    () async {
      final f = Fixture();
      addTearDown(f.close);
      await f.call.start();
      f.wire.receive({
        'serverContent': {
          'inputTranscription': {'text': 'Hello'},
        },
      });
      f.wire.receive({
        'serverContent': {
          'outputTranscription': {'text': 'Hi there'},
          'turnComplete': true,
        },
      });
      await Future<void>.delayed(const Duration(milliseconds: 450));
      expect(f.chat['revision'], 1);
      expect(f.chat['messages'], [
        {'role': 'user', 'text': 'Hello'},
        {'role': 'assistant', 'text': 'Hi there'},
      ]);
      expect(jsonDecode(f.vault.data['chat:${f.id}']!)['revision'], 1);
      expect(f.vault.data['live-pending:${f.id}'], '');
    },
  );
  test(
    'read-aloud uses the same live voice without opening the microphone',
    () async {
      final f = Fixture();
      addTearDown(f.close);
      await f.call.start(readText: 'A saved answer');
      expect(f.audio.capture, false);
      expect(f.call.voice, 'Charon');
      expect(f.wire.sent.last.containsKey('clientContent'), true);
      f.audio.input.add({'type': 'mic', 'data': Uint8List(4), 'level': .1});
      expect(
        f.wire.sent.any((m) => (m['realtimeInput'] as Map?)?['audio'] != null),
        false,
      );
      f.wire.receive({
        'serverContent': {'turnComplete': true},
      });
      await tick();
      expect(f.call.closed, true);
      expect(f.chat['messages'], isEmpty);
    },
  );
  test(
    'hangup stops native capture and drops late microphone frames',
    () async {
      final f = Fixture();
      addTearDown(f.close);
      await f.call.start();
      await f.call.end();
      expect(f.audio.capture, false);
      expect(f.audio.stopped, greaterThan(0));
      final count = f.wire.sent.length;
      f.audio.input.add({'type': 'mic', 'data': Uint8List(4), 'level': .1});
      expect(f.wire.sent.length, count);
    },
  );
  test('muted reconnection never opens the microphone', () async {
    final f = Fixture();
    addTearDown(f.close);
    await f.call.start(initialMuted: true);
    expect(f.audio.capture, false);
    await f.call.reconnect();
    expect(f.audio.capture, false);
    expect(f.call.muted, true);
  });
  test(
    'conflicting unsynced transcript is retained without overwriting cloud history',
    () async {
      final f = Fixture();
      addTearDown(f.close);
      final draft = jsonEncode({
        'id': f.id,
        'revision': 99,
        'messages': [
          {'role': 'user', 'text': 'Saved words'},
        ],
      });
      f.vault.data['live-pending:${f.id}'] = draft;
      await f.call.start();
      expect(f.call.phase, CallPhase.failed);
      expect(f.vault.data['live-pending:${f.id}'], draft);
      expect(f.chat['messages'], isEmpty);
      expect(f.audio.capture, false);
    },
  );
  testWidgets(
    'live call has animated controls and no message editor or Send button',
    (tester) async {
      final f = Fixture();

      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      f.call.phase = CallPhase.listening;
      f.call.closed = false;
      await tester.pumpWidget(
        MaterialApp(
          home: LiveScreen(
            repo: f.repo,
            id: f.id,
            call: f.call,
            autoStart: false,
          ),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(find.byType(TextField), findsNothing);
      expect(find.text('Send'), findsNothing);
      expect(find.byTooltip('End call'), findsOneWidget);
      expect(find.byTooltip('Mute microphone'), findsOneWidget);
      expect(find.text('Voice · Charon'), findsOneWidget);
      expect(find.byType(CustomPaint), findsWidgets);
      expect(tester.takeException(), isNull);
      // This view test never starts IO; the audio lifecycle tests above
      // exercise asynchronous hangup separately outside the fake clock.
      f.call.closed = true;
      await tester.pumpWidget(const SizedBox());
      f.call.dispose();
      f.repo.dispose();
      unawaited(f.audio.input.close());
      unawaited(f.wire.stream.close());
      await tester.pump();
    },
  );
}
