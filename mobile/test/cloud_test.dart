import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:arienx_mobile/core/cloud.dart';

class MemoryVault implements Vault {
  final data = <String, String>{};
  @override
  Future<String?> read(String key) async => data[key];
  @override
  Future<void> write(String key, String value) async {
    data[key] = value;
  }

  @override
  Future<void> clear() async {
    data.clear();
  }
}

void main() {
  test('pairing validates HTTPS, scheme, token and embedded credentials', () {
    final code = 'a' * 64;
    final qr = Uri(
      scheme: 'arienx',
      host: 'pair',
      queryParameters: {'cloud': 'https://cloud.example', 'code': code},
    ).toString();
    expect(pairingCode(qr).cloud.host, 'cloud.example');
    for (final value in [
      'http://cloud.example',
      'https://user:pass@cloud.example',
      'https://cloud.example/api',
      'file:///tmp',
    ]) {
      expect(() => cloudOrigin(value), throwsFormatException);
    }
    expect(() => pairingCode('https://cloud.example'), throwsFormatException);
    expect(
      () => pairingCode('arienx://pair?cloud=https://cloud.example&code=bad'),
      throwsFormatException,
    );
  });
  test('draft persists while cloud is unavailable', () async {
    final vault = MemoryVault();
    final repo = CloudRepository(
      vault,
      client: MockClient((_) async => throw Exception('offline')),
    );
    await repo.draft('chat', 'unsent');
    expect(await repo.readDraft('chat'), 'unsent');
    await expectLater(
      repo.request('/api/memory'),
      throwsA(isA<CloudFailure>()),
    );
    expect(await repo.readDraft('chat'), 'unsent');
    repo.dispose();
  });
  test('signup uses the Cloud Core and waits for email confirmation', () async {
    final repo = CloudRepository(
      MemoryVault(),
      client: MockClient((request) async {
        expect(request.url.path, '/api/auth/signup');
        expect(jsonDecode(request.body), {
          'email': 'new@example.test',
          'password': 'long-enough-password',
        });
        return http.Response(
          '{"email":"new@example.test","confirmation_required":true}',
          200,
        );
      }),
    );
    expect(
      await repo.signup(
        'https://cloud.example',
        'new@example.test',
        'long-enough-password',
        'My phone',
      ),
      'Check your email to confirm this account, then sign in.',
    );
    repo.dispose();
  });
  test(
    'streamed reply updates encrypted cache and clears draft only on completion',
    () async {
      final vault = MemoryVault();
      final complete = {
        'id': 'a' * 32,
        'revision': 1,
        'messages': [
          {'role': 'assistant', 'text': 'hello'},
        ],
      };
      final repo = CloudRepository(
        vault,
        client: MockClient((r) async {
          expect(r.headers['x-arienx-session'], 'credential');
          return http.Response(
            'data: {"delta":"hel"}\n\ndata: {"delta":"lo"}\n\ndata: ${jsonEncode({'done': true, 'conversation': complete})}\n\n',
            200,
          );
        }),
      );
      repo.token = 'credential';
      final events = await repo.send({
        'id': 'a' * 32,
        'revision': 0,
      }, 'hello').toList();
      expect(events.length, 3);
      expect(await repo.readDraft('a' * 32), '');
      expect(jsonDecode(vault.data['chat:${'a' * 32}']!)['revision'], 1);
      repo.dispose();
    },
  );
  test('interrupted SSE retains the unsent draft', () async {
    final vault = MemoryVault();
    final repo = CloudRepository(
      vault,
      client: MockClient(
        (_) async => http.Response('data: {"delta":"partial"}\n\n', 200),
      ),
    );
    await expectLater(
      repo.send({'id': 'a' * 32, 'revision': 0}, 'keep me').toList(),
      throwsA(isA<CloudFailure>()),
    );
    expect(await repo.readDraft('a' * 32), 'keep me');
    repo.dispose();
  });
  test(
    'memory conflict is surfaced without replacing the local revision',
    () async {
      final repo = CloudRepository(
        MemoryVault(),
        client: MockClient(
          (_) async => http.Response('{"error":"sync_conflict"}', 409),
        ),
      );
      repo.memory = {'revision': 7, 'facts': {}};
      await expectLater(
        repo.saveFact('notes', 'project', 'test'),
        throwsA(isA<CloudFailure>().having((e) => e.status, 'status', 409)),
      );
      expect(repo.memory['revision'], 7);
      repo.dispose();
    },
  );
  test(
    'logout revokes before clearing session and preserves credential on network failure',
    () async {
      final vault = MemoryVault();
      await vault.write('session', 'private');
      final bad = CloudRepository(
        vault,
        client: MockClient((_) async => throw Exception('offline')),
      )..token = 'credential';
      await expectLater(bad.logout(), throwsA(isA<CloudFailure>()));
      expect(bad.token, 'credential');
      expect(vault.data.isNotEmpty, true);
      bad.dispose();
      final good = CloudRepository(
        vault,
        client: MockClient((r) async {
          expect(r.url.path, '/api/mobile/logout');
          return http.Response('{"revoked":true}', 200);
        }),
      )..token = 'credential';
      await good.logout();
      expect(good.authenticated, false);
      expect(vault.data, isEmpty);
      good.dispose();
    },
  );
  test(
    'read-only monitor refresh does not send worker control commands',
    () async {
      final methods = <String>[];
      final repo = CloudRepository(
        MemoryVault(),
        client: MockClient((r) async {
          methods.add(r.method);
          return http.Response(
            jsonEncode(switch (r.url.path) {
              '/api/bridge/devices' => {'devices': []},
              '/api/conversations' => {'conversations': []},
              '/api/memory' => {'revision': 0, 'facts': {}},
              _ => {'sessions': []},
            }),
            200,
          );
        }),
      )..token = 'credential';
      await repo.refresh();
      expect(methods, everyElement('GET'));
      expect(repo.online, true);
      repo.dispose();
    },
  );
}
