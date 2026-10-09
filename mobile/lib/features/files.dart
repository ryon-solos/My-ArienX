import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';
import '../core/cloud.dart';
import 'panels.dart';

class FilesScreen extends StatefulWidget {
  final CloudRepository repo;
  final String? initialPath, initialName;
  const FilesScreen({
    super.key,
    required this.repo,
    this.initialPath,
    this.initialName,
  });
  @override
  State<FilesScreen> createState() => _FilesScreenState();
}

class _FilesScreenState extends State<FilesScreen> {
  List<dynamic> files = [];
  bool busy = false;
  String error = '';
  @override
  void initState() {
    super.initState();
    load();
    if (widget.initialPath != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) {
          upload(
            false,
            sharedPath: widget.initialPath,
            sharedName: widget.initialName,
          );
        }
      });
    }
  }

  Future<void> load() async {
    try {
      final result = await widget.repo.request('/api/mobile/files');
      if (mounted) setState(() => files = result['files']);
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> upload(
    bool camera, {
    String? sharedPath,
    String? sharedName,
  }) async {
    if (busy) return;
    setState(() => busy = true);
    try {
      String? path = sharedPath;
      String? name = sharedName;
      if (sharedPath != null) {
        // Android provided a user-selected share URI copied into our private cache.
      } else if (camera) {
        final image = await ImagePicker().pickImage(
          source: ImageSource.camera,
          maxWidth: 1600,
          imageQuality: 80,
        );
        path = image?.path;
        name = image?.name;
      } else {
        final selection = await FilePicker.platform.pickFiles();
        path = selection?.files.single.path;
        name = selection?.files.single.name;
      }
      if (path == null || !mounted) return;
      final file = File(path);
      if (await file.length() > 3 * 1024 * 1024) {
        throw CloudFailure('Choose a file smaller than 3 MB.');
      }
      if (!mounted) return;
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: Text('Share $name with ArienX?'),
          content: Text(
            'This uploads the selected file to your account at ${widget.repo.origin}. Do not share credentials or private keys.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(c, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(c, true),
              child: const Text('Upload'),
            ),
          ],
        ),
      );
      if (ok != true) return;
      await widget.repo.request(
        '/api/mobile/files',
        method: 'POST',
        body: {'name': name, 'data': base64Encode(await file.readAsBytes())},
      );
      await load();
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> download(dynamic item) async {
    final value = await widget.repo.request(
      '/api/mobile/files?id=${item['id']}',
    );
    final dir = await getTemporaryDirectory();
    final name = '${value['name']}'.replaceAll(
      RegExp(r'[^a-zA-Z0-9._ -]'),
      '_',
    );
    final file = File('${dir.path}/${value['id']}-$name');
    await file.writeAsBytes(base64Decode(value['data']));
    await SharePlus.instance.share(
      ShareParams(files: [XFile(file.path)], text: name),
    );
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Shared files'),
      actions: [
        IconButton(
          tooltip: 'Refresh',
          onPressed: load,
          icon: const Icon(Icons.sync),
        ),
      ],
    ),
    body: ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Text(
          'Explicit transfers only · up to 3 MB per file. Files are never opened automatically on your desktop.',
        ),
        if (error.isNotEmpty)
          Text(error, style: const TextStyle(color: Colors.orange)),
        if (busy) const LinearProgressIndicator(),
        Wrap(
          spacing: 8,
          children: [
            FilledButton.icon(
              onPressed: busy ? null : () => upload(false),
              icon: const Icon(Icons.attach_file),
              label: const Text('Choose file'),
            ),
            OutlinedButton.icon(
              onPressed: busy ? null : () => upload(true),
              icon: const Icon(Icons.camera_alt_outlined),
              label: const Text('Camera'),
            ),
          ],
        ),
        for (final f in files)
          Card(
            child: ListTile(
              title: Text('${f['name']}'),
              subtitle: Text('${f['size']} bytes'),
              trailing: PopupMenuButton<String>(
                onSelected: (v) => perform(
                  context,
                  () => v == 'download'
                      ? download(f)
                      : widget.repo.queue('mobile_receive_file', {
                          'file_id': f['id'],
                        }),
                ),
                itemBuilder: (_) => const [
                  PopupMenuItem(
                    value: 'download',
                    child: Text('Download / share'),
                  ),
                  PopupMenuItem(
                    value: 'desktop',
                    child: Text('Send to selected desktop'),
                  ),
                ],
              ),
            ),
          ),
      ],
    ),
  );
}
