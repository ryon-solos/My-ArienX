import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;

const releaseOrigin = 'https://myarienx.netlify.app';
const mobileVersion = '1.2.1';
const mobileBuild = 4;

class MobileRelease {
  final int code;
  final String version, url, sha256;
  MobileRelease(this.code, this.version, this.url, this.sha256);
  factory MobileRelease.parse(Map<String, dynamic> json) {
    final uri = Uri.parse('${json['url']}');
    final digest = '${json['sha256']}';
    if (uri.origin != releaseOrigin ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment ||
        !uri.path.startsWith('/download/') ||
        !uri.path.endsWith('.apk') ||
        !RegExp(r'^[a-f0-9]{64}$').hasMatch(digest) ||
        json['versionCode'] is! int ||
        json['versionCode'] < 1) {
      throw const FormatException('Invalid official release metadata.');
    }
    return MobileRelease(
      json['versionCode'],
      '${json['version']}',
      uri.toString(),
      digest,
    );
  }
}

class AppUpdates {
  static const channel = MethodChannel('app.arienx/updates');
  static Future<MobileRelease?> check({http.Client? client}) async {
    final owned = client == null;
    client ??= http.Client();
    try {
      final response = await client
          .get(
            Uri.parse('$releaseOrigin/download/latest.json'),
            headers: {'Cache-Control': 'no-cache'},
          )
          .timeout(const Duration(seconds: 15));
      if (response.statusCode != 200) {
        throw Exception('Update service unavailable.');
      }
      final release = MobileRelease.parse(
        Map<String, dynamic>.from(jsonDecode(response.body)),
      );
      return release.code > mobileBuild ? release : null;
    } finally {
      if (owned) client.close();
    }
  }

  static Future<String> install(MobileRelease release) async {
    return await channel.invokeMethod<String>('installUpdate', {
          'url': release.url,
          'sha256': release.sha256,
          'versionCode': release.code,
        }) ??
        'Update sent to Android.';
  }

  static Future<void> configureAutomatic(
    bool enabled, {
    bool checkNow = true,
  }) async {
    await channel.invokeMethod('configureAutoUpdates', enabled);
    if (enabled && checkNow) await channel.invokeMethod('autoCheckUpdates');
  }

  static Future<Map<String, dynamic>> status() async =>
      Map<String, dynamic>.from(
        await channel.invokeMethod('updateStatus') ?? {},
      );
  static Future<void> allowInstallation() async =>
      channel.invokeMethod('allowUpdates');
  static Future<void> approveInstallation() async =>
      channel.invokeMethod('approveUpdate');
}

class UpdatePanel extends StatefulWidget {
  const UpdatePanel({super.key});
  @override
  State<UpdatePanel> createState() => _UpdatePanelState();
}

class _UpdatePanelState extends State<UpdatePanel> with WidgetsBindingObserver {
  bool busy = false, allowed = true, approval = false;
  String status =
      'Automatic updates use Wi-Fi and install while your phone is idle.';
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    refreshStatus();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) refreshStatus();
  }

  Future<void> refreshStatus() async {
    try {
      final current = await AppUpdates.status();
      if (mounted) {
        setState(() {
          allowed = current['installAllowed'] != false;
          approval = current['approvalRequired'] == true;
          status = '${current['message'] ?? status}';
        });
      }
    } catch (_) {
      /* Native updates are unavailable on non-Android platforms. */
    }
  }

  Future<void> check() async {
    setState(() => busy = true);
    try {
      if (!allowed) {
        await AppUpdates.allowInstallation();
        return;
      }
      if (approval) {
        await AppUpdates.approveInstallation();
        return;
      }
      final release = await AppUpdates.check();
      if (!mounted) return;
      if (release == null) {
        setState(() => status = 'You have the latest release.');
        return;
      }
      setState(() => status = 'Downloading and verifying the official update…');
      final message = await AppUpdates.install(release);
      if (mounted) setState(() => status = message);
    } catch (e) {
      if (mounted) setState(() => status = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => ListTile(
    leading: busy
        ? const SizedBox(
            width: 24,
            height: 24,
            child: CircularProgressIndicator(),
          )
        : const Icon(Icons.system_update),
    title: const Text('ArienX Mobile $mobileVersion'),
    subtitle: Text(status),
    trailing: TextButton(
      onPressed: busy ? null : check,
      child: Text(
        !allowed
            ? 'Allow updates'
            : approval
            ? 'Finish update'
            : 'Check updates',
      ),
    ),
  );
}
