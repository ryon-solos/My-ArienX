import 'dart:async';
import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../core/cloud.dart';
import '../core/live.dart';

class LiveScreen extends StatefulWidget {
  final CloudRepository repo;
  final String id;
  final LiveCall? call;
  final bool autoStart;
  const LiveScreen({
    super.key,
    required this.repo,
    required this.id,
    this.call,
    this.autoStart = true,
  });
  @override
  State<LiveScreen> createState() => _LiveScreenState();
}

class _LiveScreenState extends State<LiveScreen>
    with SingleTickerProviderStateMixin, WidgetsBindingObserver {
  late final LiveCall call;
  late final AnimationController animation;
  DateTime? started;
  Timer? clock;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    call =
        widget.call ??
        LiveCall(repo: widget.repo, id: widget.id, audio: AndroidLiveAudio());
    call.confirmDesktop = confirm;
    call.addListener(changed);
    animation = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 4),
    )..repeat();
    clock = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && widget.autoStart) call.start();
    });
  }

  void changed() {
    if (mounted) {
      if (started == null && call.phase == CallPhase.listening) {
        started = DateTime.now();
      }
      setState(() {});
    }
  }

  Future<bool> confirm(String action, Map<String, dynamic> args) async =>
      await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Confirm desktop command'),
          content: Text(
            '$action\n${args.entries.map((e) => '${e.key}: ${e.value}').join('\n')}\n\nThis queues a request; an offline desktop will run it after reconnecting.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(c, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(c, true),
              child: const Text('Confirm'),
            ),
          ],
        ),
      ) ??
      false;
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden ||
        state == AppLifecycleState.detached) {
      unawaited(call.end());
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    clock?.cancel();
    call.removeListener(changed);
    if (widget.call == null) {
      call.dispose();
    } else {
      unawaited(call.end());
    }
    animation.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final status = switch (call.phase) {
      CallPhase.connecting => 'Connecting…',
      CallPhase.listening => 'Listening',
      CallPhase.speaking => 'ArienX is speaking',
      CallPhase.paused => 'Microphone muted',
      CallPhase.ended => 'Call ended',
      CallPhase.failed => 'Unable to connect',
    };
    final elapsed = started == null
        ? 0
        : DateTime.now().difference(started!).inSeconds;
    return PopScope(
      onPopInvokedWithResult: (didPop, _) {
        if (didPop) unawaited(call.end());
      },
      child: Scaffold(
        backgroundColor: const Color(0xff070c14),
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          title: const Text('ArienX Live'),
          actions: [
            IconButton(
              tooltip: 'Close call',
              onPressed: () async {
                await call.end();
                if (context.mounted) Navigator.pop(context);
              },
              icon: const Icon(Icons.close),
            ),
          ],
        ),
        body: SafeArea(
          child: Column(
            children: [
              const SizedBox(height: 24),
              Text(
                '${elapsed ~/ 60}:${(elapsed % 60).toString().padLeft(2, '0')}',
                style: const TextStyle(color: Colors.white54),
              ),
              Expanded(
                child: Center(
                  child: AnimatedBuilder(
                    animation: animation,
                    builder: (context, _) => Semantics(
                      label: status,
                      child: CustomPaint(
                        size: const Size(280, 280),
                        painter: CallOrb(
                          animation.value,
                          call.level,
                          call.phase,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
              Text(status, style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 12),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 28),
                child: Text(
                  call.error.isNotEmpty
                      ? call.error
                      : call.closed
                      ? 'Start a call when you’re ready.'
                      : 'Talk naturally. You can interrupt me.',
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.white60),
                ),
              ),
              const SizedBox(height: 12),
              Text(
                'Voice · ${call.voice}',
                style: const TextStyle(color: Colors.white38),
              ),
              const SizedBox(height: 28),
              if (call.closed)
                FilledButton.icon(
                  onPressed: () {
                    started = null;
                    call.start();
                  },
                  icon: const Icon(Icons.call),
                  label: const Text('Start call'),
                )
              else
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                    IconButton.filledTonal(
                      tooltip: call.muted
                          ? 'Unmute microphone'
                          : 'Mute microphone',
                      onPressed: call.phase == CallPhase.connecting
                          ? null
                          : () => call.setMuted(!call.muted),
                      icon: Icon(call.muted ? Icons.mic_off : Icons.mic),
                    ),
                    IconButton.filled(
                      style: IconButton.styleFrom(
                        backgroundColor: Colors.redAccent,
                        foregroundColor: Colors.white,
                        padding: const EdgeInsets.all(24),
                      ),
                      tooltip: 'End call',
                      onPressed: call.end,
                      icon: const Icon(Icons.call_end),
                    ),
                    IconButton.filledTonal(
                      tooltip: call.loudspeaker
                          ? 'Use earpiece'
                          : 'Use speaker',
                      onPressed: call.phase == CallPhase.connecting
                          ? null
                          : () => call.setSpeaker(!call.loudspeaker),
                      icon: Icon(
                        call.loudspeaker ? Icons.volume_up : Icons.hearing,
                      ),
                    ),
                  ],
                ),
              const SizedBox(height: 36),
            ],
          ),
        ),
      ),
    );
  }
}

class CallOrb extends CustomPainter {
  final double time, level;
  final CallPhase phase;
  CallOrb(this.time, this.level, this.phase);
  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final active = [
      CallPhase.listening,
      CallPhase.speaking,
      CallPhase.connecting,
    ].contains(phase);
    final color = phase == CallPhase.speaking
        ? const Color(0xff84a7ff)
        : const Color(0xff42dcff);
    final radius = 76 + math.sin(time * math.pi * 2) * 5 + level * 55;
    canvas.drawCircle(
      center,
      radius + 25,
      Paint()
        ..shader = RadialGradient(
          colors: [
            color.withValues(alpha: active ? .28 : .08),
            Colors.transparent,
          ],
        ).createShader(Rect.fromCircle(center: center, radius: radius + 45)),
    );
    for (var ring = 0; ring < 4; ring++) {
      final path = Path();
      for (var n = 0; n <= 180; n++) {
        final angle = n / 180 * math.pi * 2;
        final wave =
            math.sin(angle * 3 + time * math.pi * 2 + ring) *
            (.06 + level * .32) *
            radius;
        final r = radius + ring * 9 + wave;
        final p = center + Offset(math.cos(angle) * r, math.sin(angle) * r);
        if (n == 0) {
          path.moveTo(p.dx, p.dy);
        } else {
          path.lineTo(p.dx, p.dy);
        }
      }
      path.close();
      canvas.drawPath(
        path,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.5 + level * 3
          ..color = color.withValues(alpha: active ? .65 - ring * .12 : .18),
      );
    }
    canvas.drawCircle(
      center,
      radius * .65,
      Paint()
        ..shader = RadialGradient(
          colors: [color.withValues(alpha: .22), color.withValues(alpha: .02)],
        ).createShader(Rect.fromCircle(center: center, radius: radius)),
    );
  }

  @override
  bool shouldRepaint(CallOrb old) => true;
}
