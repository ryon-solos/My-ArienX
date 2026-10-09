import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import '../core/cloud.dart';

class ArienXSearch extends SearchDelegate<int?> {
  final CloudRepository repo;
  ArienXSearch(this.repo)
    : super(searchFieldLabel: 'Search your ArienX ecosystem');
  @override
  List<Widget> buildActions(BuildContext context) => [
    IconButton(
      tooltip: 'Clear',
      onPressed: () {
        query = '';
      },
      icon: const Icon(Icons.clear),
    ),
  ];
  @override
  Widget buildLeading(BuildContext context) => IconButton(
    tooltip: 'Back',
    onPressed: () => close(context, null),
    icon: const Icon(Icons.arrow_back),
  );
  @override
  Widget buildSuggestions(BuildContext context) => results(context);
  @override
  Widget buildResults(BuildContext context) => results(context);
  Widget results(BuildContext context) {
    final hits =
        <({String title, String detail, int section, String? chat})>[
              for (final c in repo.chats)
                (
                  title: '${c['title']}',
                  detail: 'Conversation',
                  section: 0,
                  chat: '${c['id']}',
                ),
              for (final f in (repo.memory['facts'] as Map? ?? {}).values)
                (
                  title: '${f['key']}',
                  detail: 'Memory · ${f['category']} · ${f['value']}',
                  section: 2,
                  chat: null,
                ),
              for (final a in repo.telemetry['extensions'] as List? ?? [])
                (
                  title: '${a['name']}',
                  detail: 'Extension · ${a['version']} · ${a['status']}',
                  section: 2,
                  chat: null,
                ),
              for (final w in repo.telemetry['workers'] as List? ?? [])
                (
                  title: '${w['heading']}',
                  detail:
                      'Worker · ${w['provider']} · ${w['model']} · ${w['status']}',
                  section: 2,
                  chat: null,
                ),
              for (final t in repo.tasks)
                (
                  title: '${t['action']}',
                  detail: 'Timeline · ${t['status']} · ${t['result'] ?? ''}',
                  section: 2,
                  chat: null,
                ),
              for (final setting in [
                'Theme',
                'Cloud provider',
                'Notifications',
                'Trusted phones',
                'Security',
                'Diagnostics',
              ])
                (title: setting, detail: 'Settings', section: 3, chat: null),
            ]
            .where(
              (h) => '${h.title} ${h.detail}'.toLowerCase().contains(
                query.toLowerCase(),
              ),
            )
            .toList();
    if (query.trim().isEmpty) {
      return const Center(
        child: Text(
          'Search chats, memory, projects, extensions, workers, settings and activity.',
        ),
      );
    }
    if (hits.isEmpty) {
      return const Center(
        child: Text('No matching items in the latest synchronized data.'),
      );
    }
    return ListView.builder(
      itemCount: hits.length,
      itemBuilder: (context, i) {
        final h = hits[i];
        return ListTile(
          title: Text(h.title),
          subtitle: Text(h.detail),
          onTap: () {
            final router = GoRouter.of(context);
            close(context, h.section);
            if (h.chat != null) router.push('/chat/${h.chat}');
          },
        );
      },
    );
  }
}
