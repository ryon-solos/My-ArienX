import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:arienx_mobile/core/cloud.dart';
import 'package:arienx_mobile/core/updates.dart';
import 'package:arienx_mobile/core/reminders.dart';
import 'package:arienx_mobile/main.dart';
import 'cloud_test.dart' show MemoryVault;

void main() {
  Map<String, dynamic> manifest(String url, {int code = 5}) => {
    'versionCode': code,
    'version': '1.2.0',
    'url': url,
    'sha256': 'a' * 64,
  };
  test('updates reject foreign origins and malformed hashes', () {
    for (final url in [
      'http://myarienx.netlify.app/download/app.apk',
      'https://evil.test/download/app.apk',
      'https://myarienx.netlify.app/elsewhere/app.apk',
      'https://myarienx.netlify.app/download/app.apk?redirect=1',
    ]) {
      expect(() => MobileRelease.parse(manifest(url)), throwsFormatException);
    }
    final invalid = manifest('$releaseOrigin/download/app.apk')
      ..['sha256'] = 'bad';
    expect(() => MobileRelease.parse(invalid), throwsFormatException);
  });
  test('update check only offers newer versions', () async {
    for (final code in [3, 4, 5]) {
      final client = MockClient(
        (_) async => http.Response(
          jsonEncode(manifest('$releaseOrigin/download/app.apk', code: code)),
          200,
        ),
      );
      expect(
        await AppUpdates.check(client: client),
        code > mobileBuild ? isA<MobileRelease>() : isNull,
      );
      client.close();
    }
  });
  test(
    'automatic updates configure and check without an app confirmation',
    () async {
      final calls = <MethodCall>[];
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(AppUpdates.channel, (call) async {
            calls.add(call);
            return null;
          });
      addTearDown(
        () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
            .setMockMethodCallHandler(AppUpdates.channel, null),
      );
      await AppUpdates.configureAutomatic(true);
      expect(calls.map((c) => c.method), [
        'configureAutoUpdates',
        'autoCheckUpdates',
      ]);
      expect(calls.first.arguments, true);
      calls.clear();
      await AppUpdates.configureAutomatic(false);
      expect(calls.map((c) => c.method), ['configureAutoUpdates']);
      expect(calls.single.arguments, false);
      calls.clear();
      await AppUpdates.configureAutomatic(true, checkNow: false);
      expect(calls.map((c) => c.method), ['configureAutoUpdates']);
    },
  );
  test(
    'installation delegates the verified release to Android directly',
    () async {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(AppUpdates.channel, (call) async {
            expect(call.method, 'installUpdate');
            expect(call.arguments, {
              'url': '$releaseOrigin/download/app.apk',
              'sha256': 'a' * 64,
              'versionCode': mobileBuild + 1,
            });
            return 'Update sent to Android';
          });
      addTearDown(
        () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
            .setMockMethodCallHandler(AppUpdates.channel, null),
      );
      final release = MobileRelease.parse(
        manifest('$releaseOrigin/download/app.apk', code: mobileBuild + 1),
      );
      expect(await AppUpdates.install(release), 'Update sent to Android');
    },
  );
  testWidgets(
    'missing installation permission offers a one-time setup button',
    (tester) async {
      final calls = <String>[];
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(AppUpdates.channel, (call) async {
            calls.add(call.method);
            return call.method == 'updateStatus'
                ? {
                    'installAllowed': false,
                    'approvalRequired': false,
                    'message': 'Allow updates once',
                  }
                : null;
          });
      addTearDown(
        () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
            .setMockMethodCallHandler(AppUpdates.channel, null),
      );
      await tester.pumpWidget(
        const MaterialApp(home: Scaffold(body: UpdatePanel())),
      );
      await tester.pumpAndSettle();
      expect(find.text('Allow updates'), findsOneWidget);
      await tester.tap(find.text('Allow updates'));
      await tester.pumpAndSettle();
      expect(calls, contains('allowUpdates'));
      expect(calls, isNot(contains('installUpdate')));
    },
  );
  testWidgets(
    'Android-required approval is surfaced without another APK download',
    (tester) async {
      final calls = <String>[];
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(AppUpdates.channel, (call) async {
            calls.add(call.method);
            return call.method == 'updateStatus'
                ? {
                    'installAllowed': true,
                    'approvalRequired': true,
                    'message': 'Android approval required',
                  }
                : null;
          });
      addTearDown(
        () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
            .setMockMethodCallHandler(AppUpdates.channel, null),
      );
      await tester.pumpWidget(
        const MaterialApp(home: Scaffold(body: UpdatePanel())),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Finish update'));
      await tester.pumpAndSettle();
      expect(calls, contains('approveUpdate'));
      expect(calls, isNot(contains('installUpdate')));
    },
  );
  test('alarm times roll into tomorrow after the selected time', () {
    final now = DateTime(2026, 10, 6, 19, 0);
    expect(nextClockTime(now, 18, 30), DateTime(2026, 10, 7, 18, 30));
    expect(nextClockTime(now, 20, 30), DateTime(2026, 10, 6, 20, 30));
    expect(() => nextClockTime(now, 25, 0), throwsFormatException);
  });
  test(
    'automatic response requires opt-in and commits only once per day',
    () async {
      var generated = 0, notices = 0;
      final vault = MemoryVault();
      final id = 'b' * 32;
      final chat = {'id': id, 'revision': 0, 'messages': []};
      late CloudRepository repo;
      repo =
          CloudRepository(
              vault,
              client: MockClient((r) async {
                expect(r.headers['x-arienx-session'], 'test-session');
                if (r.url.path == '/api/conversations') {
                  return http.Response(jsonEncode(chat), 200);
                }
                expect(r.url.path, '/api/mobile/chat');
                generated++;
                return http.Response(
                  'data: ${jsonEncode({
                    'done': true,
                    'conversation': {
                      ...chat,
                      'revision': 1,
                      'messages': [
                        {'role': 'assistant', 'text': 'Check-in'},
                      ],
                    },
                  })}\n\n',
                  200,
                );
              }),
            )
            ..token = 'test-session'
            ..online = true;
      repo.notify = (_, _) async {
        notices++;
        repo.active = false;
      };
      await repo.autoCheckIn();
      expect(generated, 0);
      repo.preferences = {'autoRespond': true, 'checkinTime': '00:00'};
      await repo.autoCheckIn();
      expect(generated, 1);
      expect(notices, 1);
      expect(jsonDecode(vault.data['chat:$id']!)['revision'], 1);
      repo.active = true;
      await repo.autoCheckIn();
      expect(generated, 1);
      repo.dispose();
    },
  );
  test(
    'Android reminder failure cannot interrupt successful memory sync',
    () async {
      final vault = MemoryVault();
      final repo = CloudRepository(
        vault,
        client: MockClient((r) async {
          return http.Response(
            jsonEncode(
              r.url.path == '/api/memory' ? {'revision': 8, 'facts': {}} : {},
            ),
            200,
          );
        }),
      )..token = 'test-session';
      repo.syncReminders = (_, _) async {
        throw StateError('Android unavailable');
      };
      await repo.refresh();
      expect(repo.online, true);
      expect(repo.memory['revision'], 8);
      expect(jsonDecode(vault.data['snapshot']!)['memory']['revision'], 8);
      repo.dispose();
    },
  );
  testWidgets(
    'clean shell exposes profile settings explore clock without Home or Remote tabs',
    (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final repo =
          CloudRepository(
              MemoryVault(),
              client: MockClient((_) async => http.Response('{}', 200)),
            )
            ..online = true
            ..preferences = {'autoUpdates': false};
      addTearDown(repo.dispose);
      await tester.pumpWidget(MaterialApp(home: HomeShell(repo: repo)));
      expect(find.text('How can I help?'), findsOneWidget);
      expect(find.text('Home'), findsNothing);
      expect(find.text('Remote'), findsNothing);
      await tester.tap(find.byTooltip('ArienX menu'));
      await tester.pumpAndSettle();
      for (final title in ['Profile', 'Explore', 'Settings', 'Clock']) {
        expect(find.text(title), findsOneWidget);
      }
      expect(tester.takeException(), isNull);
    },
  );
}
