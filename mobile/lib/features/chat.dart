import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import '../core/live.dart';
import 'package:speech_to_text/speech_to_text.dart';
import 'package:go_router/go_router.dart';
import '../core/cloud.dart';
import 'panels.dart' show RemotePanel;

class ChatList extends StatefulWidget {
  final CloudRepository repo;
  const ChatList({super.key, required this.repo});
  @override
  State<ChatList> createState() => _ChatListState();
}

class _ChatListState extends State<ChatList> {
  String query = '';
  bool creating = false;
  @override
  Widget build(BuildContext context) {
    final chats =
        widget.repo.chats
            .where(
              (c) =>
                  '${c['title']}'.toLowerCase().contains(query.toLowerCase()),
            )
            .toList()
          ..sort(
            (a, b) => (b['pinned'] == true ? 1 : 0).compareTo(
              a['pinned'] == true ? 1 : 0,
            ),
          );
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'Conversations',
                style: Theme.of(context).textTheme.headlineSmall,
              ),
            ),
            IconButton(
              tooltip: 'New conversation',
              onPressed: creating
                  ? null
                  : () async {
                      setState(() => creating = true);
                      try {
                        final id = await widget.repo.createChat();
                        if (context.mounted) context.push('/chat/$id');
                      } catch (e) {
                        if (context.mounted) {
                          ScaffoldMessenger.of(
                            context,
                          ).showSnackBar(SnackBar(content: Text('$e')));
                        }
                      } finally {
                        if (mounted) setState(() => creating = false);
                      }
                    },
              icon: const Icon(Icons.add_comment_outlined),
            ),
          ],
        ),
        const Text(
          'Shared cloud conversations · saved drafts stay on this phone',
        ),
        const SizedBox(height: 16),
        TextField(
          onChanged: (s) => setState(() => query = s),
          decoration: const InputDecoration(
            prefixIcon: Icon(Icons.search),
            hintText: 'Search conversations',
          ),
        ),
        if (chats.isEmpty)
          const Padding(
            padding: EdgeInsets.all(32),
            child: Text(
              'Start a conversation with ArienX. It stays available while your desktop is off.',
            ),
          ),
        for (final c in chats)
          Card(
            child: ListTile(
              title: Text('${c['title']}'),
              subtitle: Text('${c['updated'] ?? ''}'),
              onTap: () => context.push('/chat/${c['id']}'),
              trailing: IconButton(
                tooltip: 'Pin conversation',
                icon: Icon(
                  c['pinned'] == true
                      ? Icons.push_pin
                      : Icons.push_pin_outlined,
                ),
                onPressed: () async {
                  try {
                    await widget.repo.pin(Map<String, dynamic>.from(c));
                  } catch (e) {
                    if (context.mounted) {
                      ScaffoldMessenger.of(
                        context,
                      ).showSnackBar(SnackBar(content: Text('$e')));
                    }
                  }
                },
              ),
            ),
          ),
      ],
    );
  }
}

class ChatScreen extends StatefulWidget {
  final CloudRepository repo;
  final String id;
  final bool live;
  const ChatScreen({
    super.key,
    required this.repo,
    required this.id,
    this.live = false,
  });
  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> with WidgetsBindingObserver {
  final input = TextEditingController(), scroll = ScrollController();
  final speech = SpeechToText();
  LiveCall? playback;
  Future<void> stopPlayback() async {
    await playback?.end();
  }

  Map<String, dynamic>? chat, savedCall;
  String error = '', answer = '', pending = '';
  bool sending = false,
      listening = false,
      continuous = false,
      readAloud = false;
  Timer? saveTimer, recognitionRetry;
  bool speaking = false, commanding = false;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    load();
    input.addListener(() => saveTimer?.cancel());
    input.addListener(
      () => saveTimer = Timer(
        const Duration(milliseconds: 300),
        () => widget.repo.draft(widget.id, input.text),
      ),
    );
  }

  Future<void> load() async {
    try {
      final c = await widget.repo.conversation(widget.id),
          draft = await widget.repo.readDraft(widget.id);
      final callDraft = await widget.repo.vault.read(
        'live-pending:${widget.id}',
      );
      if (mounted) {
        setState(() {
          chat = c;
          savedCall = callDraft == null || callDraft.isEmpty
              ? null
              : Map<String, dynamic>.from(jsonDecode(callDraft));
          input.text = draft;
          error = '';
        });
        if (widget.live) {
          continuous = true;
          readAloud = true;
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (mounted) listen();
          });
        }
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> reviewSavedCall() async {
    final draft = savedCall;
    if (draft == null) return;
    final owner = widget.repo.epoch;
    final recover = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Unsynced call transcript'),
        content: SingleChildScrollView(
          child: SelectableText(
            (draft['messages'] as List)
                .map((m) => '${m['role']}: ${m['text']}')
                .join('\n\n'),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Close'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Save a separate conversation'),
          ),
        ],
      ),
    );
    if (recover != true || owner != widget.repo.epoch) return;
    try {
      final id = await widget.repo.createChat();
      if (owner != widget.repo.epoch) return;
      final created = await widget.repo.conversation(id);
      await widget.repo.request(
        '/api/conversations',
        method: 'PATCH',
        body: {
          'id': id,
          'revision': created['revision'],
          'messages': draft['messages'],
        },
      );
      if (owner != widget.repo.epoch) return;
      await widget.repo.vault.write('live-pending:${widget.id}', '');
      if (mounted) {
        setState(() => savedCall = null);
        context.push('/chat/$id');
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> listen() async {
    await stopPlayback();
    speaking = false;
    if (speech.isListening) {
      continuous = false;
      await speech.stop();
      if (mounted) setState(() => listening = false);
      return;
    }
    final available = await speech.initialize(
      onStatus: (s) {
        if (mounted) setState(() => listening = s == 'listening');
        recognitionRetry?.cancel();
        if (s == 'done' || s == 'notListening') {
          recognitionRetry = Timer(const Duration(milliseconds: 800), () {
            if (mounted &&
                continuous &&
                !sending &&
                !speaking &&
                !commanding &&
                !speech.isListening &&
                error.isEmpty) {
              listen();
            }
          });
        }
      },
      onError: (e) {
        if (continuous &&
            ['error_no_match', 'error_speech_timeout'].contains(e.errorMsg)) {
          recognitionRetry?.cancel();
          recognitionRetry = Timer(const Duration(seconds: 1), () {
            if (mounted &&
                continuous &&
                !sending &&
                !speaking &&
                !commanding &&
                widget.repo.active) {
              listen();
            }
          });
          return;
        }
        if (mounted) {
          setState(
            () => error = 'Speech recognition is unavailable: ${e.errorMsg}',
          );
        }
      },
    );
    if (!available) {
      if (mounted) {
        setState(
          () => error =
              'Microphone or speech recognition unavailable. You can type your message.',
        );
      }
      return;
    }
    await speech.listen(
      onResult: (r) {
        if (!mounted) return;
        input.text = r.recognizedWords;
        if (r.finalResult &&
            continuous &&
            input.text.trim().isNotEmpty &&
            !sending) {
          unawaited(send());
        }
      },
      listenOptions: SpeechListenOptions(
        listenFor: const Duration(seconds: 45),
        pauseFor: const Duration(seconds: 3),
      ),
    );
  }

  Future<void> send() async {
    final text = input.text.trim();
    if (text.isEmpty || chat == null || sending || commanding) return;
    commanding = true;
    bool handled;
    try {
      await speech.stop();
      await stopPlayback();
      handled = await desktopCommand(text);
    } finally {
      commanding = false;
    }
    if (handled) {
      if (mounted && continuous && error.isEmpty && widget.repo.active) {
        unawaited(listen());
      }
      return;
    }
    await speech.stop();
    await stopPlayback();
    if (!mounted) return;
    setState(() {
      sending = true;
      answer = '';
      pending = text;
      error = '';
    });
    try {
      await for (final event in widget.repo.send(chat!, text)) {
        if (!mounted) return;
        setState(() {
          if (event['delta'] != null) answer += '${event['delta']}';
          if (event['done'] == true) {
            chat = Map<String, dynamic>.from(event['conversation']);
            input.clear();
            pending = '';
            answer = '';
          }
        });
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (scroll.hasClients) {
            scroll.animateTo(
              scroll.position.maxScrollExtent,
              duration: const Duration(milliseconds: 150),
              curve: Curves.easeOut,
            );
          }
        });
      }
      unawaited(widget.repo.refresh());
      if (readAloud && chat != null && widget.repo.active) {
        speaking = true;
        playback = LiveCall(
          repo: widget.repo,
          id: widget.id,
          audio: AndroidLiveAudio(),
        );
        try {
          await playback!.start(readText: '${chat!['messages'].last['text']}');
          await playback!.finished.timeout(const Duration(seconds: 90));
          if (playback!.error.isNotEmpty) throw CloudFailure(playback!.error);
        } finally {
          playback?.dispose();
          playback = null;
          speaking = false;
        }
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) {
        setState(() => sending = false);
        if (continuous && error.isEmpty) unawaited(listen());
      }
    }
  }

  Future<bool> desktopCommand(String text) async {
    final open = RegExp(
      r'^open (.+?) (?:on (?:my |the )?(?:desktop|computer|laptop))$',
      caseSensitive: false,
    ).firstMatch(text);
    final power = RegExp(
      r'^(lock|restart|shutdown|shut down) (?:my |the )?(desktop|computer|laptop)$',
      caseSensitive: false,
    ).firstMatch(text);
    final status = RegExp(
      r'^(?:get |show |check )?(?:my |the )?(?:desktop|computer|laptop) (?:system )?status$',
      caseSensitive: false,
    ).hasMatch(text);
    if (open == null && power == null && !status) return false;
    await speech.stop();
    await stopPlayback();
    if (!mounted) return true;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Send desktop command?'),
        content: Text(
          '$text\n\nIf your desktop is offline, this remains queued for up to 24 hours. Desktop permission checks still apply.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Queue command'),
          ),
        ],
      ),
    );
    if (confirmed != true) return true;
    try {
      if (open != null) {
        final target = open[1]!, url = Uri.tryParse(target);
        if (url != null && ['http', 'https'].contains(url.scheme)) {
          await widget.repo.queue('browser_control', {
            'action': 'navigate',
            'url': target,
          });
        } else {
          await widget.repo.queue('open_app', {'app_name': target});
        }
      } else if (power != null) {
        final verb = power[1]!.toLowerCase();
        await widget.repo.queue('computer_settings', {
          'action': verb == 'lock'
              ? 'lock_screen'
              : verb == 'restart'
              ? 'restart'
              : 'shutdown',
        });
      } else {
        await widget.repo.queue('system_status', {});
      }
      if (mounted) {
        input.clear();
        setState(() => error = '');
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'Desktop command queued. See Profile for its result.',
            ),
          ),
        );
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
    return true;
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden ||
        state == AppLifecycleState.detached) {
      continuous = false;
      speech.cancel();
      stopPlayback();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    saveTimer?.cancel();
    recognitionRetry?.cancel();
    unawaited(widget.repo.draft(widget.id, input.text));
    speech.cancel();
    stopPlayback();
    input.dispose();
    scroll.dispose();
    super.dispose();
  }

  Widget bubble(String role, String text) => Align(
    alignment: role == 'user' ? Alignment.centerRight : Alignment.centerLeft,
    child: Container(
      constraints: const BoxConstraints(maxWidth: 640),
      margin: const EdgeInsets.symmetric(vertical: 8),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: role == 'user'
            ? const Color(0xff003044)
            : const Color(0xff010f18),
        border: Border.all(color: const Color(0xff0d3347)),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            role == 'user' ? 'YOU' : 'ARIENX',
            style: const TextStyle(
              fontSize: 10,
              letterSpacing: 2,
              color: Color(0xff00d4ff),
            ),
          ),
          const SizedBox(height: 8),
          MarkdownBody(data: text, selectable: true),
        ],
      ),
    ),
  );
  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: Text(
        widget.live ? 'Live Talk' : '${chat?['title'] ?? 'Conversation'}',
      ),
      actions: [
        if (!widget.live)
          IconButton(
            tooltip: 'Live Talk in this conversation',
            icon: const Icon(Icons.graphic_eq),
            onPressed: sending
                ? null
                : () => context.pushReplacement('/live/${widget.id}'),
          ),
        IconButton(
          tooltip: 'Desktop commands',
          icon: const Icon(Icons.computer_outlined),
          onPressed: () => showModalBottomSheet(
            context: context,
            isScrollControlled: true,
            builder: (_) => SizedBox(
              height: MediaQuery.sizeOf(context).height * .75,
              child: RemotePanel(repo: widget.repo),
            ),
          ),
        ),
        IconButton(
          tooltip: 'Refresh conversation',
          onPressed: sending ? null : load,
          icon: const Icon(Icons.sync),
        ),
      ],
    ),
    body: SafeArea(
      child: Column(
        children: [
          if (widget.live)
            Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                children: [
                  Icon(
                    listening ? Icons.mic : Icons.graphic_eq,
                    size: 56,
                    color: const Color(0xff00d4ff),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    sending
                        ? 'Thinking…'
                        : listening
                        ? 'Listening…'
                        : 'Tap the microphone to speak',
                  ),
                  const Text(
                    'Voice turns · shared chat & memory',
                    style: TextStyle(fontSize: 12),
                  ),
                ],
              ),
            ),
          if (savedCall != null)
            MaterialBanner(
              content: const Text(
                'A call transcript is safely stored on this phone but has not synced.',
              ),
              actions: [
                TextButton(
                  onPressed: reviewSavedCall,
                  child: const Text('Review saved call'),
                ),
              ],
            ),
          if (error.isNotEmpty)
            MaterialBanner(
              content: Text(error),
              actions: [
                TextButton(
                  onPressed: sending ? null : load,
                  child: const Text('Refresh'),
                ),
              ],
            ),
          Expanded(
            child: chat == null
                ? Center(
                    child: error.isEmpty
                        ? const CircularProgressIndicator()
                        : const Text('Conversation unavailable'),
                  )
                : ListView(
                    controller: scroll,
                    padding: const EdgeInsets.all(16),
                    children: [
                      for (final m in chat!['messages'] ?? [])
                        bubble('${m['role']}', '${m['text']}'),
                      if (pending.isNotEmpty) bubble('user', pending),
                      if (answer.isNotEmpty) bubble('assistant', answer),
                      if (sending) const LinearProgressIndicator(),
                    ],
                  ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Row(
              children: [
                FilterChip(
                  label: const Text('Read aloud'),
                  selected: readAloud,
                  onSelected: (v) {
                    setState(() => readAloud = v);
                    if (!v) stopPlayback();
                  },
                ),
                const SizedBox(width: 8),
                FilterChip(
                  label: const Text('Live Talk'),
                  selected: false,
                  onSelected: (_) =>
                      context.pushReplacement('/live/${widget.id}'),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                IconButton(
                  tooltip: listening
                      ? 'Stop listening'
                      : 'Dictate a chat message',
                  onPressed: listen,
                  icon: Icon(
                    listening ? Icons.mic : Icons.mic_none,
                    color: listening ? Colors.orange : null,
                  ),
                ),
                Expanded(
                  child: TextField(
                    controller: input,
                    minLines: 1,
                    maxLines: 5,
                    decoration: const InputDecoration(
                      hintText: 'Message ArienX · draft saved',
                    ),
                    enabled: !sending,
                  ),
                ),
                IconButton(
                  tooltip: 'Send',
                  onPressed: sending ? null : send,
                  icon: const Icon(Icons.send),
                ),
              ],
            ),
          ),
        ],
      ),
    ),
  );
}
