import 'dart:convert';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:timezone/data/latest.dart' as zones;
import 'package:timezone/timezone.dart' as tz;
import 'notifications.dart';

DateTime nextClockTime(DateTime now, int hour, int minute) {
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    throw const FormatException('Invalid reminder time');
  }
  final at = DateTime(now.year, now.month, now.day, hour, minute);
  return at.isAfter(now)
      ? at
      : DateTime(now.year, now.month, now.day + 1, hour, minute);
}

class PhoneReminders {
  final PhoneNotifications notifications = PhoneNotifications();
  String? lastPlan;
  bool initialized = false;
  Future<void> initialize() async {
    if (initialized) return;
    zones.initializeTimeZones();
    await notifications.initialize();
    initialized = true;
  }

  Future<bool> enable() => notifications.enable();
  Future<void> schedule(
    int id,
    String title,
    String body,
    DateTime at, {
    bool exact = false,
  }) async {
    if (!at.isAfter(DateTime.now())) return;
    await initialize();
    final android = notifications.plugin
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >();
    final precise =
        exact && await android?.canScheduleExactNotifications() == true;
    await notifications.plugin.zonedSchedule(
      id,
      title,
      body,
      tz.TZDateTime.from(at, tz.UTC),
      NotificationDetails(
        android: AndroidNotificationDetails(
          exact ? 'arienx_alarm' : 'arienx_checkins',
          exact ? 'ArienX clock' : 'ArienX check-ins',
          importance: exact ? Importance.high : Importance.defaultImportance,
          priority: exact ? Priority.high : Priority.defaultPriority,
          visibility: NotificationVisibility.private,
          category: exact
              ? AndroidNotificationCategory.alarm
              : AndroidNotificationCategory.reminder,
        ),
      ),
      androidScheduleMode: precise
          ? AndroidScheduleMode.exactAllowWhileIdle
          : AndroidScheduleMode.inexactAllowWhileIdle,
    );
  }

  Future<void> cancel(int id) => notifications.plugin.cancel(id);
  Future<void> exactPermission() async {
    await initialize();
    await notifications.plugin
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >()
        ?.requestExactAlarmsPermission();
  }

  Future<void> syncMemory(
    Map<String, dynamic> memory,
    Map<String, dynamic> preferences,
  ) async {
    final now = DateTime.now();
    final plan = jsonEncode([
      memory,
      preferences['checkins'],
      preferences['checkinTime'],
      now.year,
      now.month,
      now.day,
      now.timeZoneOffset.inMinutes,
    ]);
    if (lastPlan == plan) return;
    await initialize();
    for (var id = 10000; id < 10056; id++) {
      await cancel(id);
    }
    if (preferences['checkins'] != true) {
      lastPlan = plan;
      return;
    }
    final facts = (memory['facts'] as Map? ?? {}).values
        .whereType<Map>()
        .toList();
    final name = facts
        .where((f) => f['category'] == 'identity' && f['key'] == 'name')
        .firstOrNull?['value'];
    final goal = facts
        .where((f) => ['projects', 'wishes', 'goals'].contains(f['category']))
        .firstOrNull?['value'];
    final time = '${preferences['checkinTime'] ?? '19:00'}'.split(':');
    final start = nextClockTime(now, int.parse(time[0]), int.parse(time[1]));
    var id = 10000;
    for (var day = 0; day < 7; day++) {
      final at = DateTime(
        start.year,
        start.month,
        start.day + day,
        start.hour,
        start.minute,
      );
      await schedule(
        id++,
        'ArienX check-in',
        '${name == null ? '' : '$name, '}how is your day going?${goal == null ? '' : ' A moment for your goal: $goal'}',
        at,
      );
    }
    // Only explicit times in semantic memory become reminders. Never infer
    // appointments from arbitrary conversation or local files.
    for (final fact
        in facts
            .where((f) => ['notes', 'wishes', 'goals'].contains(f['category']))
            .take(40)) {
      final value = '${fact['value'] ?? ''}';
      final match = RegExp(r'\b([01]?\d|2[0-3]):([0-5]\d)\b').firstMatch(value);
      if (match == null ||
          !RegExp(
            r'\b(daily|every day)\b',
            caseSensitive: false,
          ).hasMatch(value) ||
          id + 7 > 10056) {
        continue;
      }
      final first = nextClockTime(
        now,
        int.parse(match[1]!),
        int.parse(match[2]!),
      );
      for (var day = 0; day < 7; day++) {
        await schedule(
          id++,
          'ArienX reminder',
          value,
          DateTime(
            first.year,
            first.month,
            first.day + day,
            first.hour,
            first.minute,
          ),
        );
      }
    }
    lastPlan = plan;
  }
}
