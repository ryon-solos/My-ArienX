import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:http/http.dart' as http;
import 'package:arienx_mobile/core/cloud.dart';
import 'package:arienx_mobile/features/connect.dart';
import 'package:arienx_mobile/features/panels.dart';
import 'cloud_test.dart' show MemoryVault;

void main() {
  testWidgets(
    'connection screen offers QR and shared account login at phone size',
    (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final repo = CloudRepository(
        MemoryVault(),
        client: MockClient((_) async => http.Response('{}', 200)),
      );
      addTearDown(repo.dispose);
      await tester.pumpWidget(MaterialApp(home: ConnectScreen(repo: repo)));
      expect(find.text('Scan desktop QR'), findsOneWidget);
      expect(find.text('Sign in or create an account'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets(
    'dashboard reports missing telemetry honestly and shows offline desktop',
    (tester) async {
      final repo = CloudRepository(
        MemoryVault(),
        client: MockClient((_) async => http.Response('{}', 200)),
      );
      addTearDown(repo.dispose);
      repo.devices = [
        {
          'device_id': 'one',
          'label': 'Desktop',
          'paired': true,
          'online': false,
        },
      ];
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: Dashboard(repo: repo)),
        ),
      );
      expect(find.text('Not reported'), findsWidgets);
      expect(find.textContaining('online'), findsWidgets);
      expect(tester.takeException(), isNull);
    },
  );
}
