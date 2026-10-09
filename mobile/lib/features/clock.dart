import 'dart:async';
import 'package:flutter/material.dart';
import '../core/cloud.dart';
import '../core/reminders.dart';
import 'panels.dart' show perform;

class ClockPanel extends StatefulWidget {
  final CloudRepository repo;
  const ClockPanel({super.key, required this.repo});
  @override
  State<ClockPanel> createState() => _ClockPanelState();
}

class _ClockPanelState extends State<ClockPanel> {
  final reminders = PhoneReminders();
  Timer? tick;
  DateTime? end;
  String error = '';
  @override
  void initState() {
    super.initState();
    end = DateTime.tryParse('${widget.repo.preferences['focusEnd'] ?? ''}');
    tick = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  Future<void> focus(int minutes) async {
    await reminders.initialize();
    if (!await reminders.enable()) {
      throw CloudFailure('Allow notifications to receive the timer alert.');
    }
    await reminders.exactPermission();
    final at = DateTime.now().add(Duration(minutes: minutes));
    await reminders.schedule(
      9001,
      'Focus complete',
      'Your $minutes-minute session is complete. Take a break.',
      at,
      exact: true,
    );
    await widget.repo.setPreference('focusEnd', at.toIso8601String());
    if (mounted) setState(() => end = at);
  }

  Future<void> alarm() async {
    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.now(),
    );
    if (time == null || !mounted) return;
    await perform(context, () async {
      await reminders.initialize();
      if (!await reminders.enable()) {
        throw CloudFailure('Allow notifications for alarms.');
      }
      await reminders.exactPermission();
      final at = nextClockTime(DateTime.now(), time.hour, time.minute);
      await reminders.schedule(
        9002,
        'ArienX alarm',
        'It is time. Open ArienX when you are ready.',
        at,
        exact: true,
      );
      await widget.repo.setPreference('alarmAt', at.toIso8601String());
    });
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    tick?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final left = end?.difference(DateTime.now()).inSeconds ?? 0;
    final remaining = left < 0 ? 0 : left;
    return ListView(
      padding: const EdgeInsets.all(24),
      children: [
        Text('Clock', style: Theme.of(context).textTheme.headlineMedium),
        const SizedBox(height: 24),
        Text(
          TimeOfDay.now().format(context),
          style: Theme.of(context).textTheme.displayMedium,
        ),
        const SizedBox(height: 32),
        const Text('Focus & Pomodoro'),
        Text(
          '${(remaining ~/ 60).toString().padLeft(2, '0')}:${(remaining % 60).toString().padLeft(2, '0')}',
          style: Theme.of(context).textTheme.displaySmall,
        ),
        Wrap(
          spacing: 8,
          children: [
            for (final minutes in [25, 5, 15])
              FilledButton.tonal(
                onPressed: () => perform(context, () => focus(minutes)),
                child: Text('$minutes min'),
              ),
          ],
        ),
        TextButton(
          onPressed: () => perform(context, () async {
            await reminders.cancel(9001);
            await widget.repo.setPreference('focusEnd', '');
            if (mounted) setState(() => end = null);
          }),
          child: const Text('Cancel timer'),
        ),
        const Divider(),
        ListTile(
          title: const Text('Alarm'),
          subtitle: Text(
            '${widget.repo.preferences['alarmAt'] ?? 'No alarm set'}',
          ),
          trailing: IconButton(
            onPressed: alarm,
            icon: const Icon(Icons.add_alarm),
          ),
        ),
        TextButton(
          onPressed: () => perform(context, () async {
            await reminders.cancel(9002);
            await widget.repo.setPreference('alarmAt', '');
            if (mounted) setState(() {});
          }),
          child: const Text('Cancel alarm'),
        ),
        const Text(
          'Alerts work while the app is closed. Allow exact alarms for precise timing; otherwise Android may delay the notification. Alarms are notification alerts, not a full-screen ringing clock.',
        ),
      ],
    );
  }
}
