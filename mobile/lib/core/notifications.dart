import 'package:flutter_local_notifications/flutter_local_notifications.dart';

class PhoneNotifications {
  final plugin = FlutterLocalNotificationsPlugin();
  Future<void> initialize() async {
    await plugin.initialize(
      const InitializationSettings(
        android: AndroidInitializationSettings('@drawable/ic_arienx'),
      ),
    );
  }

  Future<bool> enable() async =>
      await plugin
          .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin
          >()
          ?.requestNotificationsPermission() ??
      false;
  Future<void> show(String title, String body) async {
    await plugin.show(
      1,
      title,
      body,
      const NotificationDetails(
        android: AndroidNotificationDetails(
          'arienx_tasks',
          'ArienX task updates',
          channelDescription: 'Updates from your connected desktop',
          importance: Importance.defaultImportance,
          priority: Priority.defaultPriority,
        ),
      ),
    );
  }
}
