import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import '../core/cloud.dart';

class ConnectScreen extends StatefulWidget {
  final CloudRepository repo;
  const ConnectScreen({super.key, required this.repo});
  @override
  State<ConnectScreen> createState() => _ConnectScreenState();
}

class _ConnectScreenState extends State<ConnectScreen> {
  final url = TextEditingController(text: 'https://myarienx.netlify.app'),
      email = TextEditingController(),
      password = TextEditingController(),
      name = TextEditingController(text: 'My phone');
  bool busy = false;
  String error = '';
  String notice = '';
  Future<void> run(Future<void> Function() action) async {
    setState(() => busy = true);
    try {
      await action();
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> scan() async {
    final raw = await Navigator.of(
      context,
    ).push<String>(MaterialPageRoute(builder: (_) => const ScannerScreen()));
    if (raw == null || !mounted) return;
    final p = pairingCode(raw);
    final accept = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Connect this phone?'),
        content: Text(
          'Cloud: ${p.cloud.host}\n\nThis gives this phone access to your ArienX account. Scan only the QR shown in your desktop Cloud Core settings.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Connect'),
          ),
        ],
      ),
    );
    if (accept == true) await widget.repo.pair(raw, name.text);
  }

  @override
  void dispose() {
    url.dispose();
    email.dispose();
    password.dispose();
    name.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    body: SafeArea(
      child: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 440),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Icon(Icons.radar, size: 96, color: Color(0xff00d4ff)),
                const SizedBox(height: 20),
                Text(
                  'A R I E N X',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.headlineMedium,
                ),
                const SizedBox(height: 10),
                const Text(
                  'One identity. Every device.',
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 32),
                TextField(
                  controller: name,
                  decoration: const InputDecoration(labelText: 'Phone name'),
                ),
                const SizedBox(height: 12),
                FilledButton.icon(
                  onPressed: busy ? null : () => run(scan),
                  icon: const Icon(Icons.qr_code_scanner),
                  label: const Text('Scan desktop QR'),
                ),
                const Padding(
                  padding: EdgeInsets.all(12),
                  child: Text(
                    'Desktop → Settings → Cloud Core → Connect phone',
                    textAlign: TextAlign.center,
                  ),
                ),
                ExpansionTile(
                  title: const Text('Sign in or create an account'),
                  children: [
                    TextField(
                      controller: url,
                      keyboardType: TextInputType.url,
                      decoration: const InputDecoration(
                        labelText: 'Cloud address',
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: email,
                      keyboardType: TextInputType.emailAddress,
                      decoration: const InputDecoration(labelText: 'Email'),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: password,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: 'Password'),
                    ),
                    const SizedBox(height: 12),
                    FilledButton(
                      onPressed: busy
                          ? null
                          : () => run(
                              () => widget.repo.login(
                                url.text,
                                email.text,
                                password.text,
                                name.text,
                              ),
                            ),
                      child: const Text('Sign in'),
                    ),
                    const SizedBox(height: 8),
                    OutlinedButton(
                      onPressed: busy
                          ? null
                          : () => run(() async {
                              final result = await widget.repo.signup(
                                url.text,
                                email.text,
                                password.text,
                                name.text,
                              );
                              if (mounted) setState(() => notice = result);
                            }),
                      child: const Text('Create account'),
                    ),
                  ],
                ),
                if (busy) const LinearProgressIndicator(),
                if (error.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.all(12),
                    child: Text(
                      error,
                      style: const TextStyle(color: Colors.orange),
                    ),
                  ),
                if (notice.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.all(12),
                    child: Text(
                      notice,
                      style: const TextStyle(color: Color(0xff00d4ff)),
                    ),
                  ),
                const SizedBox(height: 20),
                const Text(
                  'Cloud chat stays available when your computer is off. Desktop actions resume when it reconnects.',
                  textAlign: TextAlign.center,
                ),
              ],
            ),
          ),
        ),
      ),
    ),
  );
}

class ScannerScreen extends StatefulWidget {
  const ScannerScreen({super.key});
  @override
  State<ScannerScreen> createState() => _ScannerScreenState();
}

class _ScannerScreenState extends State<ScannerScreen> {
  final controller = MobileScannerController(formats: [BarcodeFormat.qrCode]);
  bool found = false;
  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Scan desktop QR'),
      actions: [
        IconButton(
          tooltip: 'Flashlight',
          onPressed: controller.toggleTorch,
          icon: const Icon(Icons.flashlight_on),
        ),
      ],
    ),
    body: MobileScanner(
      controller: controller,
      onDetect: (capture) {
        if (found) return;
        for (final code in capture.barcodes) {
          if (code.rawValue != null) {
            try {
              pairingCode(code.rawValue!);
              found = true;
              Navigator.pop(context, code.rawValue);
              return;
            } catch (_) {}
          }
        }
      },
      errorBuilder: (context, error) => const Center(
        child: Text(
          'Camera is unavailable. Allow camera access in Android settings, or use account sign-in.',
        ),
      ),
    ),
  );
}
