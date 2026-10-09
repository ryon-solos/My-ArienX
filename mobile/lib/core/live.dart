import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:uuid/uuid.dart';
import 'cloud.dart';

abstract class LiveAudio {
  Stream<dynamic> get events;
  Future<void> start({bool capture = true});
  Future<void> play(Uint8List bytes);
  Future<void> clear();
  Future<void> mute(bool value);
  Future<void> speaker(bool value);
  Future<void> stop();
}

class AndroidLiveAudio implements LiveAudio {
  static const channel = MethodChannel('app.arienx/live-audio');
  static const microphone = EventChannel('app.arienx/live-audio/events');
  @override
  Stream<dynamic> get events => microphone.receiveBroadcastStream();
  @override
  Future<void> start({bool capture = true}) async =>
      channel.invokeMethod('start', {'capture': capture});
  @override
  Future<void> play(Uint8List bytes) async =>
      channel.invokeMethod('play', bytes);
  @override
  Future<void> clear() async => channel.invokeMethod('clear');
  @override
  Future<void> mute(bool value) async => channel.invokeMethod('mute', value);
  @override
  Future<void> speaker(bool value) async =>
      channel.invokeMethod('speaker', value);
  @override
  Future<void> stop() async => channel.invokeMethod('stop');
}

abstract class LiveWire {
  Stream<dynamic> get messages;
  void send(Map<String, dynamic> message);
  Future<void> close();
}

class SocketLiveWire implements LiveWire {
  final WebSocket socket;
  SocketLiveWire(this.socket);
  static Future<LiveWire> connect(String endpoint, String token) async {
    if (endpoint != liveEndpoint || !token.startsWith('auth_tokens/')) {
      throw CloudFailure('Invalid Live connection');
    }
    return SocketLiveWire(
      await WebSocket.connect(
        endpoint,
        headers: {'Authorization': 'Token $token'},
      ).timeout(const Duration(seconds: 20)),
    );
  }

  @override
  Stream<dynamic> get messages => socket;
  @override
  void send(Map<String, dynamic> message) => socket.add(jsonEncode(message));
  @override
  Future<void> close() async => socket.close();
}

const liveEndpoint =
    'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained';

enum CallPhase { connecting, listening, speaking, paused, ended, failed }

class LiveCall extends ChangeNotifier {
  final CloudRepository repo;
  final String id;
  final LiveAudio audio;
  final Future<LiveWire> Function(String, String) connect;
  Future<bool> Function(String, Map<String, dynamic>)? confirmDesktop;
  LiveCall({
    required this.repo,
    required this.id,
    required this.audio,
    Future<LiveWire> Function(String, String)? connect,
  }) : connect = connect ?? SocketLiveWire.connect {
    repo.addListener(accountChanged);
  }
  CallPhase phase = CallPhase.ended;
  String error = '', voice = 'Charon';
  bool muted = false, loudspeaker = true, playing = false, closed = true;
  double level = 0;
  int run = 0, account = 0;
  bool disposed = false, readOnly = false, readDone = false, ready = false;
  String userWords = '', assistantWords = '', resumeHandle = '';
  Map<String, dynamic>? chat;
  LiveWire? wire;
  StreamSubscription<dynamic>? microphone, incoming;
  Timer? flushTimer, reconnectTimer;
  Future<void> eventTail = Future.value(), saveTail = Future.value();
  Completer<void>? setupReady;
  Completer<void> completion = Completer<void>();
  Future<void> get finished => completion.future;
  DateTime meterAt = DateTime.fromMillisecondsSinceEpoch(0);
  final handledTools = <String>{};
  void changed() {
    if (!disposed) notifyListeners();
  }

  Future<void> accountChanged() async {
    if (!closed && account != repo.epoch) await end();
  }

  Future<void> stopping = Future.value();
  Future<void> start({String? readText, bool initialMuted = false}) async {
    if (!closed || disposed) return;
    await stopping;
    await saveTail;
    if (!closed || disposed) return;
    final generation = ++run;
    account = repo.epoch;
    closed = false;
    ready = false;
    readOnly = readText != null;
    readDone = false;
    muted = readOnly || initialMuted;
    playing = false;
    error = '';
    phase = CallPhase.connecting;
    completion = Completer<void>();
    setupReady = Completer<void>();
    changed();
    try {
      chat = await repo.conversation(id);
      if (closed || generation != run) return;
      // Recover a previously interrupted save only when its revision still
      // matches. Never overwrite a conversation changed by another device.
      final draft = await repo.vault.read('live-pending:$id');
      if (closed || generation != run || account != repo.epoch) return;
      if (!readOnly && draft != null && draft.isNotEmpty) {
        final pending = Map<String, dynamic>.from(jsonDecode(draft));
        if (pending['revision'] != chat!['revision']) {
          throw CloudFailure(
            'A saved call needs review in chat before starting another call.',
          );
        }
        chat = await repo.request(
          '/api/conversations',
          method: 'PATCH',
          body: pending,
        );
        await repo.vault.write('live-pending:$id', '');
      }
      final session = await repo.request(
        '/api/mobile/live',
        method: 'POST',
        body: {'id': id, 'revision': chat!['revision']},
      );
      if (closed || generation != run || account != repo.epoch) return;
      voice = '${session['voice']}';
      final connected = await connect(
        '${session['websocket']}',
        '${session['token']}',
      );
      if (closed || generation != run || account != repo.epoch) {
        await connected.close();
        return;
      }
      wire = connected;
      microphone = audio.events.listen(
        (event) => audioEvent(event, generation),
        onError: (_) => fail('Phone audio disconnected. Retry the call.'),
      );
      incoming = wire!.messages.listen(
        (message) {
          eventTail = eventTail
              .then((_) async {
                if (!closed && generation == run) {
                  await receive(message, generation, readText);
                }
              })
              .catchError((Object _) {
                if (!closed && generation == run) {
                  fail('Live connection interrupted. Retry the call.');
                }
              });
        },
        onError: (_) => fail('Live connection interrupted. Retry the call.'),
        onDone: () {
          if (!closed) {
            fail('Call disconnected. Your completed turns remain saved.');
          }
        },
      );
      final setup = Map<String, dynamic>.from(session['setup']);
      setup['sessionResumption'] = resumeHandle.isNotEmpty && !readOnly
          ? {'handle': resumeHandle}
          : {};
      wire!.send({'setup': setup});
      await setupReady!.future.timeout(const Duration(seconds: 45));
      if (closed || generation != run) return;
      reconnectTimer = Timer(const Duration(minutes: 9), () => reconnect());
    } catch (e) {
      if (!closed && generation == run) {
        final message = e is CloudFailure
            ? e.message
            : e is PlatformException
            ? (e.message ?? 'Microphone unavailable')
            : 'Could not connect Live Talk. Check your connection and retry.';
        await fail(message);
      }
    }
  }

  void audioEvent(dynamic event, int generation) {
    if (closed || generation != run || account != repo.epoch || event is! Map) {
      return;
    }
    if (event['type'] == 'error') {
      unawaited(fail('Phone audio disconnected. Retry the call.'));
      return;
    }
    if (event['type'] == 'mic' && ready && !muted) {
      final bytes = event['data'];
      if (bytes is Uint8List && bytes.isNotEmpty) {
        wire?.send({
          'realtimeInput': {
            'audio': {
              'mimeType': 'audio/pcm;rate=16000',
              'data': base64Encode(bytes),
            },
          },
        });
      }
    }
    if (event['type'] == 'speaker') {
      playing = event['playing'] == true;
      if (readOnly && readDone && !playing) {
        unawaited(end());
        return;
      }
    }
    if (DateTime.now().difference(meterAt).inMilliseconds >= 70) {
      meterAt = DateTime.now();
      level = ((event['level'] as num?)?.toDouble() ?? 0).clamp(0, 1);
      if (ready) {
        phase = playing
            ? CallPhase.speaking
            : muted
            ? CallPhase.paused
            : CallPhase.listening;
      }
      changed();
    }
  }

  Future<void> receive(dynamic frame, int generation, String? readText) async {
    if (account != repo.epoch) {
      await end();
      return;
    }
    final data = Map<String, dynamic>.from(
      jsonDecode(frame is String ? frame : utf8.decode(frame as List<int>)),
    );
    if (data['error'] != null) {
      await fail('Live audio service rejected the call. Retry shortly.');
      return;
    }
    if (data.containsKey('setupComplete')) {
      await audio.start(capture: !muted);
      if (closed || generation != run) {
        await audio.stop();
        return;
      }
      ready = true;
      phase = muted ? CallPhase.paused : CallPhase.listening;
      if (readText != null) {
        wire?.send({
          'clientContent': {
            'turns': [
              {
                'role': 'user',
                'parts': [
                  {
                    'text':
                        'Read this answer aloud verbatim, naturally, without introducing or adding commentary: $readText',
                  },
                ],
              },
            ],
            'turnComplete': true,
          },
        });
      }
      if (!setupReady!.isCompleted) setupReady!.complete();
      changed();
    }
    final resumption = data['sessionResumptionUpdate'];
    if (resumption is Map &&
        resumption['resumable'] == true &&
        resumption['newHandle'] is String) {
      resumeHandle = resumption['newHandle'];
    }
    if (data['goAway'] != null && !readOnly) {
      unawaited(reconnect());
      return;
    }
    final content = data['serverContent'];
    if (content is Map) {
      if (content['interrupted'] == true) {
        await audio.clear();
        playing = false;
        phase = muted ? CallPhase.paused : CallPhase.listening;
        changed();
      }
      userWords += '${(content['inputTranscription'] as Map?)?['text'] ?? ''}';
      assistantWords +=
          '${(content['outputTranscription'] as Map?)?['text'] ?? ''}';
      for (final part in (content['modelTurn'] as Map?)?['parts'] ?? []) {
        final blob = part['inlineData'];
        if (blob is Map &&
            '${blob['mimeType']}'.startsWith('audio/pcm') &&
            blob['data'] is String) {
          playing = true;
          phase = CallPhase.speaking;
          changed();
          await audio.play(base64Decode(blob['data']));
        }
      }
      if (content['turnComplete'] == true) {
        if (readOnly) {
          readDone = true;
          if (!playing) await end();
        } else {
          flushTimer?.cancel();
          flushTimer = Timer(
            const Duration(milliseconds: 350),
            () => enqueueSave(),
          );
        }
      }
    }
    final calls = (data['toolCall'] as Map?)?['functionCalls'];
    if (calls is List && !readOnly) {
      for (final raw in calls) {
        if (closed || generation != run) return;
        final call = Map<String, dynamic>.from(raw), toolId = '${raw['id']}';
        if (!handledTools.add(toolId)) continue;
        final args = Map<String, dynamic>.from(call['args'] ?? {});
        final action = '${args['action']}';
        final values = Map<String, dynamic>.from(args['args'] ?? {});
        var result = <String, dynamic>{
          'queued': false,
          'reason': 'Not confirmed',
        };
        if (call['name'] == 'request_desktop_command' &&
            [
              'system_status',
              'open_app',
              'browser_control',
              'computer_settings',
            ].contains(action)) {
          final wasMuted = muted;
          await setMuted(true);
          await audio.clear();
          final approved = await confirmDesktop?.call(action, values) ?? false;
          if (closed || generation != run) return;
          if (approved) {
            try {
              if (repo.selectedDevice.isEmpty) {
                throw CloudFailure('Pair a desktop first');
              }
              await repo.request(
                '/api/bridge/tasks',
                method: 'POST',
                body: {
                  'device_id': repo.selectedDevice,
                  'action': action,
                  'args': values,
                  'request_id': const Uuid().v5(Namespace.url.value, toolId),
                },
              );
              result = {'queued': true, 'executed': false};
              unawaited(repo.refresh());
            } catch (_) {
              result = {
                'queued': false,
                'reason': 'Device Bridge did not accept the command',
              };
            }
          }
          await setMuted(wasMuted);
        }
        wire?.send({
          'toolResponse': {
            'functionResponses': [
              {'id': toolId, 'name': call['name'], 'response': result},
            ],
          },
        });
      }
    }
  }

  Future<void> setMuted(bool value) async {
    if (closed || !ready) return;
    muted = value;
    if (value) {
      wire?.send({
        'realtimeInput': {'audioStreamEnd': true},
      });
    }
    await audio.mute(value);
    level = 0;
    phase = playing
        ? CallPhase.speaking
        : muted
        ? CallPhase.paused
        : CallPhase.listening;
    changed();
  }

  Future<void> setSpeaker(bool value) async {
    loudspeaker = value;
    await audio.speaker(value);
    changed();
  }

  void enqueueSave() {
    flushTimer?.cancel();
    flushTimer = null;
    if (readOnly || chat == null || account != repo.epoch) return;
    final turns = <Map<String, String>>[
      if (userWords.trim().isNotEmpty)
        {'role': 'user', 'text': userWords.trim()},
      if (assistantWords.trim().isNotEmpty)
        {'role': 'assistant', 'text': assistantWords.trim()},
    ];
    userWords = '';
    assistantWords = '';
    if (turns.isEmpty) return;
    final ownerEpoch = account;
    saveTail = saveTail
        .then((_) async {
          if (ownerEpoch != repo.epoch) return;
          final patch = {
            'id': id,
            'revision': chat!['revision'],
            'messages': [...chat!['messages'], ...turns],
          };
          await repo.vault.write('live-pending:$id', jsonEncode(patch));
          if (ownerEpoch != repo.epoch) return;
          final saved = await repo.request(
            '/api/conversations',
            method: 'PATCH',
            body: patch,
          );
          if (ownerEpoch != repo.epoch) return;
          chat = saved;
          await repo.vault.write('chat:$id', jsonEncode(chat));
          await repo.vault.write('live-pending:$id', '');
        })
        .catchError((Object _) {
          unawaited(
            fail(
              'Call transcript is saved on this phone; cloud sync paused. Reopen after checking the conversation.',
            ),
          );
        });
  }

  Future<void> reconnect() async {
    if (closed || readOnly) return;
    final wasMuted = muted;
    await end();
    await start(initialMuted: wasMuted);
  }

  Future<void> fail(String message) async {
    error = message;
    await end();
    phase = CallPhase.failed;
    changed();
  }

  Future<void> end() {
    if (closed) return stopping;
    closed = true;
    if (setupReady != null && !setupReady!.isCompleted) setupReady!.complete();
    run++;
    ready = false;
    reconnectTimer?.cancel();
    reconnectTimer = null;
    flushTimer?.cancel();
    enqueueSave();
    playing = false;
    level = 0;
    phase = CallPhase.ended;
    changed();
    stopping = shutdown();
    return stopping;
  }

  Future<void> shutdown() async {
    await microphone?.cancel();
    microphone = null;
    try {
      await audio.stop();
    } catch (_) {}
    await incoming?.cancel();
    incoming = null;
    try {
      await wire?.close().timeout(const Duration(seconds: 2));
    } catch (_) {}
    wire = null;
    if (!completion.isCompleted) completion.complete();
  }

  @override
  void dispose() {
    if (disposed) return;
    disposed = true;
    repo.removeListener(accountChanged);
    unawaited(end());
    super.dispose();
  }
}
