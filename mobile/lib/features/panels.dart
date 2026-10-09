import 'package:flutter/material.dart';
import '../core/cloud.dart';
import 'files.dart';
import '../core/notifications.dart';
import '../core/reminders.dart';
import '../core/updates.dart';

Future<void> perform(
  BuildContext context,
  Future<void> Function() action,
) async {
  try {
    await action();
    if (context.mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('Saved')));
    }
  } catch (e) {
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }
}

Future<String?> ask(
  BuildContext context,
  String title, {
  String initial = '',
}) async {
  final input = TextEditingController(text: initial);
  final value = await showDialog<String>(
    context: context,
    builder: (c) => AlertDialog(
      title: Text(title),
      content: TextField(controller: input, autofocus: true, maxLines: 3),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(c),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(c, input.text.trim()),
          child: const Text('Save'),
        ),
      ],
    ),
  );
  input.dispose();
  return value;
}

Widget heading(BuildContext context, String text) => Padding(
  padding: const EdgeInsets.symmetric(vertical: 16),
  child: Text(text, style: Theme.of(context).textTheme.headlineSmall),
);

class Dashboard extends StatelessWidget {
  final CloudRepository repo;
  const Dashboard({super.key, required this.repo});
  @override
  Widget build(BuildContext context) {
    final workers = repo.telemetry['workers'] as List? ?? [],
        apps = repo.telemetry['extensions'] as List? ?? [];
    return RefreshIndicator(
      onRefresh: repo.refresh,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          const SizedBox(height: 12),
          Center(
            child: Container(
              width: 150,
              height: 150,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(color: const Color(0xff00d4ff), width: 2),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x3000d4ff),
                    blurRadius: 32,
                    spreadRadius: 8,
                  ),
                ],
              ),
              child: const Icon(
                Icons.radar,
                size: 96,
                color: Color(0xff8ffcff),
              ),
            ),
          ),
          const SizedBox(height: 24),
          Text(
            'CONNECTED INTELLIGENCE',
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 8),
          Text(repo.email, textAlign: TextAlign.center),
          heading(context, 'Your ecosystem'),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              metric('CLOUD', repo.online ? 'Connected' : 'Offline'),
              metric(
                'DEVICES',
                '${repo.devices.where((d) => d['online'] == true).length} online',
              ),
              metric(
                'WORKERS',
                '${workers.where((w) => '${w['status']}'.toLowerCase() == 'running').length} active',
              ),
              metric('MEMORY', 'Revision ${repo.memory['revision']}'),
            ],
          ),
          heading(context, 'Desktop vitals'),
          if (repo.telemetry['at'] != null)
            Text('Last reported: ${repo.telemetry['at']}'),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              metric('CPU', percent(repo.telemetry['cpu'])),
              metric('RAM', percent(repo.telemetry['ram'])),
              metric('BATTERY', percent(repo.telemetry['battery'])),
              metric('EXTENSIONS', '${apps.length}'),
            ],
          ),
          heading(context, 'Devices'),
          for (final d in repo.devices)
            Card(
              child: ListTile(
                leading: Icon(
                  Icons.computer,
                  color: d['online'] == true
                      ? Colors.greenAccent
                      : Colors.blueGrey,
                ),
                title: Text('${d['label']}'),
                subtitle: Text(
                  d['paired'] != true
                      ? 'Revoked'
                      : d['online'] == true
                      ? 'Online'
                      : 'Offline · cloud chat is available',
                ),
                trailing: d['device_id'] == repo.selectedDevice
                    ? const Icon(Icons.check_circle_outline)
                    : null,
                onTap: d['paired'] == true
                    ? () => repo.selectDevice(d['device_id'])
                    : null,
              ),
            ),
          if (repo.devices.isEmpty)
            const Text('Pair your desktop in Settings → Cloud Core.'),
          heading(context, 'Recent activity'),
          for (final t in repo.tasks.reversed.take(5))
            ListTile(
              leading: const Icon(Icons.history),
              title: Text('${t['action']}'),
              subtitle: Text('${t['status']}'),
            ),
          const SizedBox(height: 24),
        ],
      ),
    );
  }

  String percent(dynamic v) => v is num ? '${v.round()}%' : 'Not reported';
  Widget metric(String label, String value) => SizedBox(
    width: 145,
    child: Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              label,
              style: const TextStyle(
                fontSize: 11,
                letterSpacing: 2,
                color: Color(0xff5ab8cc),
              ),
            ),
            const SizedBox(height: 8),
            Text(
              value,
              style: const TextStyle(fontSize: 21, color: Color(0xff8ffcff)),
            ),
          ],
        ),
      ),
    ),
  );
}

class RemotePanel extends StatefulWidget {
  final CloudRepository repo;
  const RemotePanel({super.key, required this.repo});
  @override
  State<RemotePanel> createState() => _RemotePanelState();
}

class _RemotePanelState extends State<RemotePanel> {
  bool busy = false;
  Future<void> command(
    String action,
    Map<String, dynamic> args,
    String label,
  ) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(label),
        content: const Text(
          'Send this command to the selected desktop? If offline, it stays queued for up to 24 hours. Existing desktop confirmation gates still apply.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Queue command'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => busy = true);
    await perform(context, () => widget.repo.queue(action, args));
    if (mounted) setState(() => busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final repo = widget.repo;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        heading(context, 'Remote desktop'),
        DropdownButtonFormField<String>(
          initialValue: repo.selectedDevice.isEmpty
              ? null
              : repo.selectedDevice,
          items: [
            for (final d in repo.devices.where((d) => d['paired'] == true))
              DropdownMenuItem(
                value: '${d['device_id']}',
                child: Text('${d['label']}'),
              ),
          ],
          onChanged: (v) {
            if (v != null) repo.selectDevice(v);
          },
          decoration: const InputDecoration(labelText: 'Target device'),
        ),
        const SizedBox(height: 12),
        const Text(
          'Commands use the existing Device Bridge. Nothing can run on a powered-off computer.',
        ),
        if (busy) const LinearProgressIndicator(),
        Wrap(
          spacing: 8,
          children: [
            for (final app in ['VS Code', 'Chrome', 'Calculator', 'Notes'])
              ActionChip(
                label: Text('Open $app'),
                onPressed: busy
                    ? null
                    : () => command('open_app', {'app_name': app}, 'Open $app'),
              ),
            ActionChip(
              label: const Text('System status'),
              onPressed: busy
                  ? null
                  : () => command('system_status', {}, 'Get system status'),
            ),
            ActionChip(
              label: const Text('Lock'),
              onPressed: busy
                  ? null
                  : () => command('computer_settings', {
                      'action': 'lock_screen',
                    }, 'Lock desktop'),
            ),
            ActionChip(
              label: const Text('Restart'),
              onPressed: busy
                  ? null
                  : () => command('computer_settings', {
                      'action': 'restart',
                    }, 'Request restart'),
            ),
            ActionChip(
              label: const Text('Shutdown'),
              onPressed: busy
                  ? null
                  : () => command('computer_settings', {
                      'action': 'shutdown',
                    }, 'Request shutdown'),
            ),
          ],
        ),
        OutlinedButton.icon(
          onPressed: busy
              ? null
              : () async {
                  final value = await ask(
                    context,
                    'Open a website',
                    initial: 'https://',
                  );
                  if (value != null && value.isNotEmpty) {
                    final u = Uri.tryParse(value);
                    if (u == null || !['http', 'https'].contains(u.scheme)) {
                      if (context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(
                            content: Text('Enter an HTTP or HTTPS address.'),
                          ),
                        );
                      }
                      return;
                    }
                    await command('browser_control', {
                      'action': 'navigate',
                      'url': value,
                    }, 'Open website');
                  }
                },
          icon: const Icon(Icons.language),
          label: const Text('Open website'),
        ),
        heading(context, 'Queue & timeline'),
        for (final t in repo.tasks.reversed)
          Card(
            child: ListTile(
              title: Text('${t['action']}'),
              subtitle: Text(
                '${t['status']}${t['result'] == null ? '' : '\n${t['result']}'}',
              ),
              trailing: t['status'] == 'queued'
                  ? IconButton(
                      tooltip: 'Cancel queued task',
                      icon: const Icon(Icons.cancel_outlined),
                      onPressed: () =>
                          perform(context, () => repo.cancel(t['id'])),
                    )
                  : null,
            ),
          ),
        if (repo.tasks.isEmpty) const Text('No recent commands.'),
      ],
    );
  }
}

class LibraryPanel extends StatefulWidget {
  final CloudRepository repo;
  const LibraryPanel({super.key, required this.repo});
  @override
  State<LibraryPanel> createState() => _LibraryPanelState();
}

class _LibraryPanelState extends State<LibraryPanel> {
  String query = '', section = 'Memory';
  bool matches(dynamic value) =>
      '$value'.toLowerCase().contains(query.toLowerCase());
  Future<void> addFact() async {
    final key = await ask(context, 'Name this cloud-safe fact');
    if (key == null || key.isEmpty || !mounted) return;
    final value = await ask(context, 'Fact value · no secrets');
    if (value == null || value.isEmpty || !mounted) return;
    await perform(context, () => widget.repo.saveFact('notes', key, value));
  }

  @override
  Widget build(BuildContext context) {
    final repo = widget.repo;
    final facts = (repo.memory['facts'] as Map? ?? {}).values.where(matches);
    final apps = (repo.telemetry['extensions'] as List? ?? []).where(matches);
    final workers = (repo.telemetry['workers'] as List? ?? []).where(matches);
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        heading(context, 'Explore ArienX'),
        OutlinedButton.icon(
          onPressed: () => Navigator.push(
            context,
            MaterialPageRoute(builder: (_) => FilesScreen(repo: repo)),
          ),
          icon: const Icon(Icons.folder_shared_outlined),
          label: const Text('Shared files & camera'),
        ),
        TextField(
          onChanged: (v) => setState(() => query = v),
          decoration: const InputDecoration(
            prefixIcon: Icon(Icons.search),
            hintText: 'Search memory, apps, workers, activity',
          ),
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 8,
          children: [
            for (final s in ['Memory', 'Extensions', 'Workers', 'Timeline'])
              ChoiceChip(
                label: Text(s),
                selected: s == section,
                onSelected: (_) => setState(() => section = s),
              ),
          ],
        ),
        if (section == 'Memory') ...[
          heading(context, 'Unified memory'),
          const Text(
            'Only explicitly shared cloud-safe facts. Revision conflicts require refreshing; no silent overwrite.',
          ),
          FilledButton.icon(
            onPressed: addFact,
            icon: const Icon(Icons.add),
            label: const Text('Add safe fact'),
          ),
          for (final f in facts)
            Card(
              child: ListTile(
                title: Text('${f['key']}'),
                subtitle: Text('${f['category']} · ${f['value']}'),
              ),
            ),
          if (facts.isEmpty) const Text('No matching facts.'),
        ],
        if (section == 'Extensions') ...[
          heading(context, 'Desktop extensions'),
          const Text(
            'Lifecycle commands run through the existing sandbox and permission gates.',
          ),
          OutlinedButton.icon(
            onPressed: () async {
              final request = await ask(context, 'Describe a new extension');
              if (request != null && request.isNotEmpty && context.mounted) {
                await perform(
                  context,
                  () => repo.queue('extension_request', {
                    'operation': 'create',
                    'request': request,
                  }),
                );
              }
            },
            icon: const Icon(Icons.add),
            label: const Text('Create extension'),
          ),
          for (final a in apps)
            Card(
              child: ExpansionTile(
                title: Text('${a['name']}'),
                subtitle: Text(
                  '${a['version']} · ${a['status']} · ${a['enabled'] == true ? 'enabled' : 'disabled'}',
                ),
                children: [
                  Text(
                    'Permissions: ${(a['permissions'] as List? ?? []).join(', ')}',
                  ),
                  Wrap(
                    spacing: 8,
                    children: [
                      for (final op in [
                        'open',
                        a['enabled'] == true ? 'disable' : 'enable',
                        'rollback',
                        'remove',
                      ])
                        TextButton(
                          onPressed: () async {
                            final ok = await showDialog<bool>(
                              context: context,
                              builder: (c) => AlertDialog(
                                title: Text('$op ${a['name']}?'),
                                actions: [
                                  TextButton(
                                    onPressed: () => Navigator.pop(c, false),
                                    child: const Text('Cancel'),
                                  ),
                                  FilledButton(
                                    onPressed: () => Navigator.pop(c, true),
                                    child: const Text('Confirm'),
                                  ),
                                ],
                              ),
                            );
                            if (ok == true && context.mounted) {
                              await perform(
                                context,
                                () => repo.queue('extension_request', {
                                  'operation': op,
                                  'id': a['id'],
                                }),
                              );
                            }
                          },
                          child: Text(op),
                        ),
                    ],
                  ),
                ],
              ),
            ),
          if (apps.isEmpty)
            const Text('No extensions reported by the selected desktop.'),
        ],
        if (section == 'Workers') ...[
          heading(context, 'Worker monitor'),
          const Text('Read-only · last desktop snapshot'),
          for (final w in workers)
            Card(
              child: ListTile(
                leading: const Icon(Icons.hub_outlined),
                title: Text('${w['heading']}'),
                subtitle: Text(
                  '${w['provider']} / ${w['model']}\n${w['status']} · ${w['runtime'] ?? '—'} seconds',
                ),
              ),
            ),
          if (workers.isEmpty) const Text('No worker activity reported.'),
        ],
        if (section == 'Timeline') ...[
          heading(context, 'Activity timeline'),
          for (final t in repo.tasks.reversed.where(matches))
            ListTile(
              leading: const Icon(Icons.history),
              title: Text('${t['action']} · ${t['status']}'),
              subtitle: Text('${t['createdAt'] ?? ''}'),
            ),
        ],
        const SizedBox(height: 24),
      ],
    );
  }
}

class SettingsPanel extends StatelessWidget {
  final CloudRepository repo;
  const SettingsPanel({super.key, required this.repo});
  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(16),
    children: [
      heading(context, 'Preferences'),
      SwitchListTile(
        title: const Text('Light theme'),
        value: repo.preferences['light'] == true,
        onChanged: (v) => repo.setPreference('light', v),
      ),
      DropdownButtonFormField<String>(
        initialValue: repo.preferences['provider'] ?? 'gemini',
        decoration: const InputDecoration(labelText: 'Cloud chat provider'),
        items: const [
          DropdownMenuItem(value: 'gemini', child: Text('Gemini')),
          DropdownMenuItem(value: 'openrouter', child: Text('OpenRouter')),
        ],
        onChanged: (v) => repo.setPreference('provider', v),
      ),
      const Text(
        'Uses the model configured in your Cloud Core. Provider credentials stay on the server.',
      ),
      SwitchListTile(
        title: const Text('Task notifications'),
        subtitle: const Text('While ArienX is connected in the foreground'),
        value: repo.preferences['notifications'] == true,
        onChanged: (v) => perform(context, () async {
          if (!v) {
            await repo.setPreference('notifications', false);
            return;
          }
          final n = PhoneNotifications();
          await n.initialize();
          if (await n.enable()) {
            await repo.setPreference('notifications', true);
          } else {
            throw CloudFailure('Notifications were not permitted.');
          }
        }),
      ),
      heading(context, 'ArienX check-ins'),
      SwitchListTile(
        title: const Text('Proactive memory reminders'),
        subtitle: const Text(
          'Scheduled daily check-ins and explicit daily HH:mm reminders from shared memory. Reopen weekly to refresh the schedule.',
        ),
        value: repo.preferences['checkins'] == true,
        onChanged: (v) => perform(context, () async {
          final n = PhoneReminders();
          await n.initialize();
          if (v && !await n.enable()) {
            throw CloudFailure('Allow notifications to enable check-ins.');
          }
          await repo.setPreference('checkins', v);
        }),
      ),
      ListTile(
        title: const Text('Daily check-in time'),
        subtitle: Text('${repo.preferences['checkinTime'] ?? '19:00'}'),
        trailing: const Icon(Icons.schedule),
        onTap: () async {
          final time = await showTimePicker(
            context: context,
            initialTime: const TimeOfDay(hour: 19, minute: 0),
          );
          if (time != null && context.mounted) {
            await perform(
              context,
              () => repo.setPreference(
                'checkinTime',
                '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}',
              ),
            );
          }
        },
      ),
      SwitchListTile(
        title: const Text('Automatic personal responses'),
        subtitle: const Text(
          'When connected and open after your check-in time, ArienX writes one daily message using shared memory and notifies you. Closed-app reminders use the saved schedule.',
        ),
        value: repo.preferences['autoRespond'] == true,
        onChanged: (v) => perform(context, () async {
          final n = PhoneNotifications();
          await n.initialize();
          if (v && !await n.enable()) {
            throw CloudFailure('Allow notifications for personal responses.');
          }
          await repo.setPreference('autoRespond', v);
        }),
      ),
      heading(context, 'App updates'),
      SwitchListTile(
        title: const Text('Automatic app updates'),
        subtitle: const Text(
          'Download on Wi-Fi; install while idle. Android may require approval.',
        ),
        value: repo.preferences['autoUpdates'] != false,
        onChanged: (v) async {
          await repo.setPreference('autoUpdates', v);
          await AppUpdates.configureAutomatic(v);
        },
      ),
      const UpdatePanel(),
      heading(context, 'Account & security'),
      ListTile(
        leading: const Icon(Icons.account_circle_outlined),
        title: Text(repo.email),
        subtitle: Text(repo.origin),
      ),
      const ListTile(
        leading: Icon(Icons.lock_outline),
        title: Text('Encrypted on this phone'),
        subtitle: Text(
          'Android secure storage protects the cache key. Cloud-safe memory and shared chat remain in your account.',
        ),
      ),
      heading(context, 'Trusted phones'),
      for (final s in repo.sessions)
        Card(
          child: ListTile(
            title: Text('${s['label']}'),
            subtitle: Text(
              s['active'] == true
                  ? 'Expires ${DateTime.fromMillisecondsSinceEpoch(s['expires']).toLocal()}'
                  : 'Revoked',
            ),
            onTap: () async {
              final name = await ask(
                context,
                'Rename phone',
                initial: '${s['label']}',
              );
              if (name != null && name.isNotEmpty && context.mounted) {
                await perform(context, () async {
                  await repo.request(
                    '/api/mobile/sessions',
                    method: 'PATCH',
                    body: {'id': s['id'], 'label': name},
                  );
                  await repo.refresh();
                });
              }
            },
            trailing: s['active'] == true
                ? IconButton(
                    tooltip: 'Revoke phone',
                    icon: const Icon(Icons.link_off),
                    onPressed: () async {
                      final ok = await showDialog<bool>(
                        context: context,
                        builder: (c) => AlertDialog(
                          title: Text('Revoke ${s['label']}?'),
                          content: const Text(
                            'This phone will lose cloud access immediately.',
                          ),
                          actions: [
                            TextButton(
                              onPressed: () => Navigator.pop(c, false),
                              child: const Text('Cancel'),
                            ),
                            FilledButton(
                              onPressed: () => Navigator.pop(c, true),
                              child: const Text('Revoke'),
                            ),
                          ],
                        ),
                      );
                      if (ok == true && context.mounted) {
                        await perform(context, () async {
                          await repo.request(
                            '/api/mobile/sessions',
                            method: 'DELETE',
                            body: {'id': s['id']},
                          );
                          await repo.refresh();
                        });
                      }
                    },
                  )
                : null,
          ),
        ),
      heading(context, 'Diagnostics'),
      ListTile(
        title: const Text('Cloud connection'),
        subtitle: Text(
          repo.online ? 'Connected · ${repo.lastSync}' : repo.error,
        ),
      ),
      ListTile(
        title: const Text('Memory revision'),
        subtitle: Text('${repo.memory['revision']}'),
      ),
      const ListTile(
        title: Text('Background behavior'),
        subtitle: Text(
          'Polling pauses while the app is in the background. Returning reconnects and refreshes automatically.',
        ),
      ),
      const ListTile(
        title: Text('Version'),
        subtitle: Text('ArienX Mobile $mobileVersion'),
      ),
      OutlinedButton.icon(
        onPressed: () => perform(context, repo.logout),
        icon: const Icon(Icons.logout),
        label: const Text('Revoke this session & sign out'),
      ),
      const SizedBox(height: 24),
    ],
  );
}
