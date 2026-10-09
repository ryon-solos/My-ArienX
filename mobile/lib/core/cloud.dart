import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:hive_flutter/hive_flutter.dart';
import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';

Uri cloudOrigin(String input) {
  final u = Uri.tryParse(input.trim());
  if (u == null ||
      u.scheme != 'https' ||
      u.host.isEmpty ||
      u.userInfo.isNotEmpty ||
      u.hasQuery ||
      u.hasFragment ||
      (u.path.isNotEmpty && u.path != '/')) {
    throw const FormatException('Enter an HTTPS cloud address without a path.');
  }
  return Uri.parse(u.origin);
}

({Uri cloud, String code}) pairingCode(String raw) {
  final u = Uri.tryParse(raw);
  if (u == null || u.scheme != 'arienx' || u.host != 'pair') {
    throw const FormatException('This is not an ArienX pairing code.');
  }
  final code = u.queryParameters['code'] ?? '';
  if (!RegExp(r'^[a-f0-9]{64}$').hasMatch(code)) {
    throw const FormatException('Invalid pairing code.');
  }
  return (cloud: cloudOrigin(u.queryParameters['cloud'] ?? ''), code: code);
}

class CloudFailure implements Exception {
  final String message;
  final int status;
  CloudFailure(this.message, [this.status = 0]);
  @override
  String toString() => message;
}

abstract interface class Vault {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> clear();
}

class EncryptedVault implements Vault {
  final Box<String> box;
  EncryptedVault(this.box);
  static Future<EncryptedVault> open() async {
    const secure = FlutterSecureStorage();
    await Hive.initFlutter();
    var encoded = await secure.read(key: 'arienx-cache-key');
    if (encoded == null) {
      encoded = base64UrlEncode(Hive.generateSecureKey());
      await secure.write(key: 'arienx-cache-key', value: encoded);
    }
    return EncryptedVault(
      await Hive.openBox<String>(
        'arienx',
        encryptionCipher: HiveAesCipher(base64Url.decode(encoded)),
      ),
    );
  }

  @override
  Future<String?> read(String key) async => box.get(key);
  @override
  Future<void> write(String key, String value) => box.put(key, value);
  @override
  Future<void> clear() async {
    await box.clear();
  }
}

class CloudRepository extends ChangeNotifier {
  final Vault vault;
  final http.Client client;
  CloudRepository(this.vault, {http.Client? client})
    : client = client ?? http.Client();
  String origin = 'https://myarienx.netlify.app',
      token = '',
      email = '',
      error = '';
  int epoch = 0;
  bool online = false, refreshing = false;
  int failures = 0;
  DateTime? lastSync;
  Map<String, dynamic> memory = {'revision': 0, 'facts': {}}, telemetry = {};
  List<dynamic> devices = [], chats = [], tasks = [], sessions = [];
  Map<String, dynamic> preferences = {};
  Future<void> Function(String, String)? notify;
  Future<void> Function(Map<String, dynamic>, Map<String, dynamic>)?
  syncReminders;
  String selectedDevice = '';
  Timer? timer;
  bool active = true;
  bool checkingIn = false;
  bool get authenticated => token.isNotEmpty;
  Map<String, String> get headers => {
    'Content-Type': 'application/json',
    'X-ArienX-Session': token,
  };
  Future<void> restore() async {
    preferences = Map<String, dynamic>.from(
      jsonDecode(await vault.read('preferences') ?? '{}'),
    );
    final saved = await vault.read('session');
    if (saved != null) {
      final s = jsonDecode(saved);
      origin = cloudOrigin(s['origin']).toString();
      token = s['token'];
      email = s['email'];
    }
    final cache = await vault.read('snapshot');
    if (cache != null) {
      final c = jsonDecode(cache);
      devices = c['devices'] ?? [];
      chats = c['chats'] ?? [];
      memory = Map<String, dynamic>.from(c['memory'] ?? memory);
      selectedDevice = c['selected'] ?? '';
      telemetry = Map<String, dynamic>.from(c['telemetry'] ?? {});
    }
    notifyListeners();
    if (authenticated) unawaited(refresh());
  }

  Future<Map<String, dynamic>> request(
    String path, {
    String method = 'GET',
    Map<String, dynamic>? body,
  }) async {
    final req = http.Request(method, Uri.parse('$origin$path'))
      ..headers.addAll(headers);
    if (body != null) req.body = jsonEncode(body);
    try {
      final streamed = await client
          .send(req)
          .timeout(const Duration(seconds: 20));
      final response = await http.Response.fromStream(
        streamed,
      ).timeout(const Duration(seconds: 20));
      final value = jsonDecode(response.body);
      if (response.statusCode >= 400) {
        throw CloudFailure(
          value is Map
              ? '${value['error'] ?? 'Request failed'}'
              : 'Request failed',
          response.statusCode,
        );
      }
      return Map<String, dynamic>.from(value as Map);
    } on CloudFailure {
      rethrow;
    } catch (_) {
      throw CloudFailure('Cloud is unreachable. Your saved drafts are safe.');
    }
  }

  Future<void> pair(String raw, String label) async {
    final p = pairingCode(raw);
    final oldOrigin = origin;
    origin = p.cloud.toString();
    try {
      final s = await request(
        '/api/mobile/redeem',
        method: 'POST',
        body: {'code': p.code, 'label': label},
      );
      await _session(s);
    } catch (_) {
      origin = oldOrigin;
      rethrow;
    }
  }

  Future<void> login(
    String url,
    String user,
    String password,
    String label,
  ) async {
    final base = cloudOrigin(url).toString();
    final response = await client
        .post(
          Uri.parse('$base/.netlify/identity/token?grant_type=password'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'email': user, 'password': password}),
        )
        .timeout(const Duration(seconds: 20));
    if (response.statusCode != 200) {
      throw CloudFailure('Sign-in failed. Check your email and password.');
    }
    final auth = jsonDecode(response.body);
    final paired = await client
        .post(
          Uri.parse('$base/api/mobile/pair'),
          headers: {
            'Content-Type': 'application/json',
            'Cookie':
                'nf_jwt=${auth['access_token']}; nf_refresh=${auth['refresh_token']}',
          },
          body: '{}',
        )
        .timeout(const Duration(seconds: 20));
    if (paired.statusCode != 200) {
      throw CloudFailure('Cloud mobile pairing is not available.');
    }
    await pair(jsonDecode(paired.body)['qr'], label);
  }

  Future<String> signup(
    String url,
    String user,
    String password,
    String label,
  ) async {
    final base = cloudOrigin(url).toString();
    final response = await client
        .post(
          Uri.parse('$base/api/auth/signup'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'email': user, 'password': password}),
        )
        .timeout(const Duration(seconds: 20));
    final body = jsonDecode(response.body);
    if (response.statusCode >= 400) {
      throw CloudFailure(
        body is Map
            ? '${body['error'] ?? 'Account creation failed.'}'
            : 'Account creation failed.',
        response.statusCode,
      );
    }
    if (body is Map && body['confirmation_required'] == true) {
      return 'Check your email to confirm this account, then sign in.';
    }
    await login(base, user, password, label);
    return 'Account created and this phone is connected.';
  }

  Future<void> _session(Map<String, dynamic> s) async {
    if (s['token'] is! String || s['email'] is! String) {
      throw CloudFailure('Invalid cloud session');
    }
    await syncReminders?.call(memory, {'checkins': false});
    epoch++;
    await vault.clear();
    preferences = {};
    token = s['token'];
    email = s['email'];
    devices = [];
    chats = [];
    memory = {'revision': 0, 'facts': {}};
    telemetry = {};
    tasks = [];
    sessions = [];
    selectedDevice = '';
    await vault.write(
      'session',
      jsonEncode({'origin': origin, 'token': token, 'email': email}),
    );
    notifyListeners();
    await refresh();
  }

  Future<void> setPreference(String key, dynamic value) async {
    preferences[key] = value;
    await vault.write('preferences', jsonEncode(preferences));
    if (key == 'checkins' || key == 'checkinTime') {
      await syncReminders?.call(memory, preferences);
    }
    notifyListeners();
    if (key == 'autoRespond' && value == true) unawaited(autoCheckIn());
  }

  Future<void> logout() async {
    // Revoke the current session on the server before discarding its credential.
    try {
      await request('/api/mobile/logout', method: 'POST', body: {});
    } on CloudFailure catch (e) {
      if (e.status != 401) rethrow;
    }
    await syncReminders?.call(memory, {'checkins': false});
    epoch++;
    token = '';
    email = '';
    online = false;
    devices = [];
    chats = [];
    tasks = [];
    sessions = [];
    memory = {'revision': 0, 'facts': {}};
    telemetry = {};
    timer?.cancel();
    await vault.clear();
    notifyListeners();
  }

  void setActive(bool value) {
    active = value;
    timer?.cancel();
    if (value && authenticated) unawaited(refresh());
  }

  Future<void> selectDevice(String id) async {
    selectedDevice = id;
    await refresh();
  }

  Future<void> refresh() async {
    if (!authenticated || refreshing || !active) return;
    refreshing = true;
    timer?.cancel();
    final generation = epoch;
    final previousStatuses = {for (final t in tasks) t['id']: t['status']};
    try {
      final all = await Future.wait([
        request('/api/bridge/devices'),
        request('/api/conversations'),
        request('/api/memory'),
        request('/api/mobile/sessions'),
      ]);
      if (generation != epoch) return;
      devices = all[0]['devices'] ?? [];
      chats = all[1]['conversations'] ?? [];
      memory = all[2];
      sessions = all[3]['sessions'] ?? [];
      if (!devices.any(
        (d) => d['device_id'] == selectedDevice && d['paired'] == true,
      )) {
        selectedDevice =
            devices
                .where((d) => d['paired'] == true)
                .firstOrNull?['device_id'] ??
            '';
      }
      if (selectedDevice.isNotEmpty) {
        final extra = await Future.wait([
          request('/api/bridge/telemetry?device_id=$selectedDevice'),
          request('/api/bridge/tasks?device_id=$selectedDevice'),
        ]);
        if (generation != epoch) return;
        telemetry = extra[0];
        tasks = extra[1]['tasks'] ?? [];
      } else {
        telemetry = {};
        tasks = [];
      }
      online = true;
      error = '';
      failures = 0;
      lastSync = DateTime.now();
      // Optional phone reminders must not turn successful cloud sync into
      // an offline state if Android refuses a notification operation.
      try {
        await syncReminders?.call(memory, preferences);
      } catch (_) {}

      if (preferences['notifications'] == true && notify != null) {
        for (final task in tasks) {
          if (previousStatuses.containsKey(task['id']) &&
              previousStatuses[task['id']] != 'complete' &&
              task['status'] == 'complete') {
            await notify!(
              'Desktop task finished',
              '${task['action']} completed. Open ArienX for details.',
            );
          }
        }
      }
      await vault.write(
        'snapshot',
        jsonEncode({
          'devices': devices,
          'chats': chats,
          'memory': memory,
          'telemetry': telemetry,
          'selected': selectedDevice,
        }),
      );
    } catch (e) {
      if (generation == epoch) {
        online = false;
        error = e.toString();
        failures = (failures + 1).clamp(0, 5);
      }
    } finally {
      refreshing = false;
      notifyListeners();
      if (active && authenticated) {
        timer = Timer(Duration(seconds: 15 * (1 << failures)), refresh);
        if (online) unawaited(autoCheckIn());
      }
    }
  }

  Future<void> autoCheckIn() async {
    if (preferences['autoRespond'] != true ||
        checkingIn ||
        !active ||
        !authenticated ||
        !online) {
      return;
    }
    final now = DateTime.now();
    // Respect the selected local check-in time. Generate at most once a day;
    // this foreground check is intentionally not presented as push delivery.
    final clock = '${preferences['checkinTime'] ?? '19:00'}'.split(':');
    if (now.hour * 60 + now.minute <
        int.parse(clock[0]) * 60 + int.parse(clock[1])) {
      return;
    }
    final today = '${now.year}-${now.month}-${now.day}';
    if (preferences['checkInDay'] == today) return;
    final generation = epoch;
    checkingIn = true;
    try {
      var id = '${preferences['checkInChat'] ?? ''}';
      Map<String, dynamic> chat;
      if (id.isEmpty) {
        chat = await request(
          '/api/conversations',
          method: 'POST',
          body: {'title': 'ArienX check-ins'},
        );
        id = '${chat['id']}';
        if (generation != epoch) return;
        await setPreference('checkInChat', id);
      } else {
        chat = await conversation(id);
      }
      await for (final event in send(
        chat,
        'It is ${now.toIso8601String()} local time. Give me one brief, useful check-in based on my shared memory, goals, preferences and recent conversation. Do not invent tasks, events or facts. Ask one helpful question.',
      )) {
        if (generation != epoch) return;
        if (event['done'] == true) {
          await setPreference('checkInDay', today);
          await notify?.call(
            'ArienX checked in',
            'A personal check-in is ready in your shared conversations.',
          );
          unawaited(refresh());
        }
      }
    } catch (_) {
      // No false notification when cloud generation fails; retry on reconnect.
    } finally {
      checkingIn = false;
    }
  }

  Future<Map<String, dynamic>> conversation(String id) async {
    try {
      final c = await request('/api/conversations?id=$id');
      await vault.write('chat:$id', jsonEncode(c));
      return c;
    } catch (_) {
      final cached = await vault.read('chat:$id');
      if (cached != null) return Map<String, dynamic>.from(jsonDecode(cached));
      rethrow;
    }
  }

  Future<String> createChat() async {
    final c = await request('/api/conversations', method: 'POST', body: {});
    await refresh();
    return c['id'];
  }

  Future<void> pin(Map<String, dynamic> c) async {
    await request(
      '/api/conversations',
      method: 'PATCH',
      body: {
        'id': c['id'],
        'revision': c['revision'],
        'pinned': c['pinned'] != true,
      },
    );
    await refresh();
  }

  Future<void> draft(String id, String text) => vault.write('draft:$id', text);
  Future<String> readDraft(String id) async =>
      await vault.read('draft:$id') ?? '';
  Stream<Map<String, dynamic>> send(
    Map<String, dynamic> chat,
    String message,
  ) async* {
    final generation = epoch;
    await draft(chat['id'], message);
    final req = http.Request('POST', Uri.parse('$origin/api/mobile/chat'))
      ..headers.addAll(headers)
      ..body = jsonEncode({
        'id': chat['id'],
        'revision': chat['revision'],
        'message': message,
        'provider': preferences['provider'] ?? 'gemini',
      });
    final response = await client
        .send(req)
        .timeout(const Duration(seconds: 25));
    if (response.statusCode != 200) {
      final text = await response.stream.bytesToString();
      throw CloudFailure(
        (jsonDecode(text) as Map)['error'] ?? 'Chat failed',
        response.statusCode,
      );
    }
    bool done = false;
    await for (final line
        in response.stream
            .timeout(const Duration(seconds: 60))
            .transform(utf8.decoder)
            .transform(const LineSplitter())) {
      if (generation != epoch) {
        throw CloudFailure('Account changed during generation.');
      }
      if (!line.startsWith('data:')) continue;
      final event = Map<String, dynamic>.from(jsonDecode(line.substring(5)));
      if (event['error'] != null) throw CloudFailure(event['error']);
      if (event['done'] == true) {
        done = true;
        await vault.write(
          'chat:${chat['id']}',
          jsonEncode(event['conversation']),
        );
        await draft(chat['id'], '');
      }
      yield event;
    }
    if (!done) {
      throw CloudFailure(
        'Connection interrupted. Your draft is saved; refresh before retrying.',
      );
    }
  }

  Future<void> queue(String action, Map<String, dynamic> args) async {
    if (selectedDevice.isEmpty) throw CloudFailure('Pair a desktop first.');
    await request(
      '/api/bridge/tasks',
      method: 'POST',
      body: {
        'device_id': selectedDevice,
        'action': action,
        'args': args,
        'request_id': const Uuid().v4(),
      },
    );
    await refresh();
  }

  Future<void> cancel(String id) async {
    await request(
      '/api/bridge/tasks',
      method: 'DELETE',
      body: {'device_id': selectedDevice, 'task_id': id},
    );
    await refresh();
  }

  Future<void> saveFact(String category, String key, String value) async {
    memory = await request(
      '/api/memory',
      method: 'PUT',
      body: {
        'base_revision': memory['revision'],
        'facts': [
          {'category': category, 'key': key, 'value': value},
        ],
      },
    );
    await refresh();
  }

  @override
  void dispose() {
    timer?.cancel();
    client.close();
    super.dispose();
  }
}
