import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'core/cloud.dart';
import 'core/notifications.dart';
import 'features/connect.dart';
import 'features/chat.dart';
import 'features/live_screen.dart';
import 'features/panels.dart';
import 'features/files.dart';
import 'features/search.dart';
import 'features/clock.dart';
import 'core/updates.dart';
import 'core/reminders.dart';

final repositoryProvider = Provider<CloudRepository>(
  (ref) => throw StateError('Repository must be provided'),
);
Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  try {
    final repo = CloudRepository(await EncryptedVault.open());
    final notifications = PhoneNotifications();
    await notifications.initialize();
    repo.notify = notifications.show;
    final reminders = PhoneReminders();
    repo.syncReminders = reminders.syncMemory;
    await repo.restore();
    try {
      await AppUpdates.configureAutomatic(
        repo.preferences['autoUpdates'] != false,
        checkNow: false,
      );
    } catch (_) {
      /* Updates must not block account access on unsupported platforms. */
    }
    runApp(
      ProviderScope(
        overrides: [repositoryProvider.overrideWithValue(repo)],
        child: const ArienXApp(),
      ),
    );
  } catch (_) {
    runApp(
      const MaterialApp(
        home: Scaffold(
          body: Center(
            child: Text(
              'Secure storage could not open. Restart ArienX; your data has not been erased.',
            ),
          ),
        ),
      ),
    );
  }
}

class ArienXApp extends ConsumerStatefulWidget {
  const ArienXApp({super.key});
  @override
  ConsumerState<ArienXApp> createState() => _ArienXAppState();
}

class _ArienXAppState extends ConsumerState<ArienXApp>
    with WidgetsBindingObserver {
  late final CloudRepository repo = ref.read(repositoryProvider);
  final navigatorKey = GlobalKey<NavigatorState>();
  static const shareChannel = MethodChannel('app.arienx/share');
  bool wasAuthenticated = false;
  late final GoRouter router = GoRouter(
    navigatorKey: navigatorKey,
    refreshListenable: repo,
    initialLocation: '/',
    redirect: (context, state) =>
        !repo.authenticated && state.matchedLocation != '/connect'
        ? '/connect'
        : repo.authenticated && state.matchedLocation == '/connect'
        ? '/'
        : null,
    routes: [
      GoRoute(
        path: '/connect',
        builder: (_, _) => ConnectScreen(repo: repo),
      ),
      GoRoute(
        path: '/',
        builder: (_, _) => HomeShell(repo: repo),
      ),
      GoRoute(
        path: '/live/:id',
        builder: (_, s) => LiveScreen(repo: repo, id: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/chat/:id',
        builder: (_, s) => ChatScreen(repo: repo, id: s.pathParameters['id']!),
      ),
    ],
  );
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    repo.addListener(changed);
    shareChannel.setMethodCallHandler((call) async {
      if (call.method == 'shared') await readShare();
    });
    WidgetsBinding.instance.addPostFrameCallback((_) => readShare());
  }

  void changed() {
    if (mounted) setState(() {});
    if (repo.authenticated && !wasAuthenticated) {
      WidgetsBinding.instance.addPostFrameCallback((_) => readShare());
    }
    wasAuthenticated = repo.authenticated;
  }

  Future<void> readShare() async {
    if (!repo.authenticated) return;
    try {
      final data = await shareChannel.invokeMapMethod<String, dynamic>(
        'takeShared',
      );
      if (!mounted || data == null) return;
      if (data['path'] != null) {
        navigatorKey.currentState?.push(
          MaterialPageRoute(
            builder: (_) => FilesScreen(
              repo: repo,
              initialPath: data['path'],
              initialName: data['name'],
            ),
          ),
        );
      } else if (data['error'] != null) {
        final c = navigatorKey.currentContext;
        if (c != null && c.mounted) {
          ScaffoldMessenger.of(
            c,
          ).showSnackBar(SnackBar(content: Text('${data['error']}')));
        }
      }
    } on MissingPluginException {
      /* No Android share channel on widget-test hosts. */
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) =>
      repo.setActive(state == AppLifecycleState.resumed);
  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    repo.removeListener(changed);
    shareChannel.setMethodCallHandler(null);
    router.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp.router(
    title: 'ArienX',
    debugShowCheckedModeBanner: false,
    routerConfig: router,
    themeMode: repo.preferences['light'] == true
        ? ThemeMode.light
        : ThemeMode.dark,
    theme: ThemeData(
      useMaterial3: true,
      colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xff007a99)),
    ),
    darkTheme: ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      scaffoldBackgroundColor: const Color(0xff00060a),
      colorScheme: ColorScheme.fromSeed(
        seedColor: const Color(0xff00d4ff),
        brightness: Brightness.dark,
        surface: const Color(0xff010d14),
        primary: const Color(0xff00d4ff),
      ),
      cardTheme: const CardThemeData(
        color: Color(0xff010f18),
        margin: EdgeInsets.symmetric(vertical: 6),
      ),
      inputDecorationTheme: const InputDecorationTheme(
        border: OutlineInputBorder(),
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: Color(0xff00060a),
        foregroundColor: Color(0xff8ffcff),
      ),
      textTheme: const TextTheme(
        headlineMedium: TextStyle(
          fontWeight: FontWeight.w300,
          letterSpacing: 3,
        ),
      ),
    ),
  );
}

class HomeShell extends StatefulWidget {
  final CloudRepository repo;
  const HomeShell({super.key, required this.repo});
  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int index = 0;
  bool starting = false;
  DateTime? checked;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => checkUpdates());
  }

  Future<void> checkUpdates() async {
    if (checked != null && DateTime.now().difference(checked!).inHours < 24) {
      return;
    }
    checked = DateTime.now();
    try {
      await AppUpdates.configureAutomatic(
        widget.repo.preferences['autoUpdates'] != false,
      );
    } catch (_) {
      // Keep chat usable when Android's update service is unavailable.
    }
  }

  Future<void> start(bool live) async {
    if (starting) return;
    setState(() => starting = true);
    try {
      final id = await widget.repo.createChat();
      if (mounted) context.push('${live ? '/live' : '/chat'}/$id');
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('$e')));
      }
    } finally {
      if (mounted) setState(() => starting = false);
    }
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: widget.repo,
    builder: (context, _) => Scaffold(
      drawer: Drawer(
        child: SafeArea(child: ChatList(repo: widget.repo)),
      ),
      appBar: AppBar(
        title: const Text('ArienX'),
        actions: [
          IconButton(
            tooltip: 'Search',
            icon: const Icon(Icons.search),
            onPressed: () async {
              final page = await showSearch<int?>(
                context: context,
                delegate: ArienXSearch(widget.repo),
              );
              if (mounted && page != null) setState(() => index = page);
            },
          ),
          IconButton(
            tooltip: 'Refresh',
            onPressed: widget.repo.refreshing ? null : widget.repo.refresh,
            icon: const Icon(Icons.sync),
          ),
        ],
      ),
      body: Column(
        children: [
          if (!widget.repo.online)
            Padding(
              padding: const EdgeInsets.all(8),
              child: Text(
                widget.repo.error.isEmpty
                    ? 'Connecting…'
                    : 'Offline · saved memory and drafts remain available',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
          Expanded(
            child: switch (index) {
              1 => Column(
                children: [
                  ListTile(
                    leading: const Icon(Icons.account_circle_outlined),
                    title: Text(widget.repo.email),
                    subtitle: const Text('Your account, devices and activity'),
                  ),
                  Expanded(child: Dashboard(repo: widget.repo)),
                ],
              ),
              2 => LibraryPanel(repo: widget.repo),
              3 => SettingsPanel(repo: widget.repo),
              4 => ClockPanel(repo: widget.repo),
              _ => Center(
                child: Padding(
                  padding: const EdgeInsets.all(28),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(
                        Icons.auto_awesome,
                        size: 64,
                        color: Color(0xff00d4ff),
                      ),
                      const SizedBox(height: 24),
                      Text(
                        'How can I help?',
                        style: Theme.of(context).textTheme.headlineMedium,
                      ),
                      const SizedBox(height: 12),
                      const Text(
                        'Your conversations and shared memory, together.',
                        textAlign: TextAlign.center,
                      ),
                      const SizedBox(height: 28),
                      FilledButton.icon(
                        onPressed: starting ? null : () => start(false),
                        icon: const Icon(Icons.edit_outlined),
                        label: const Text('Start a chat'),
                      ),
                      const SizedBox(height: 12),
                      OutlinedButton.icon(
                        onPressed: starting ? null : () => start(true),
                        icon: const Icon(Icons.graphic_eq),
                        label: const Text('Live Talk'),
                      ),
                      const SizedBox(height: 20),
                      const Text(
                        'Open the side panel to continue a conversation.',
                        textAlign: TextAlign.center,
                      ),
                    ],
                  ),
                ),
              ),
            },
          ),
        ],
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          child: Row(
            children: [
              PopupMenuButton<int>(
                tooltip: 'ArienX menu',
                icon: const Icon(Icons.apps_rounded),
                onSelected: (v) => setState(() => index = v),
                itemBuilder: (_) => const [
                  PopupMenuItem(value: 0, child: Text('Chat')),
                  PopupMenuItem(value: 1, child: Text('Profile')),
                  PopupMenuItem(value: 2, child: Text('Explore')),
                  PopupMenuItem(value: 3, child: Text('Settings')),
                  PopupMenuItem(value: 4, child: Text('Clock')),
                ],
              ),
              const Spacer(),
              TextButton.icon(
                onPressed: starting ? null : () => start(true),
                icon: const Icon(Icons.graphic_eq),
                label: const Text('Live Talk'),
              ),
            ],
          ),
        ),
      ),
    ),
  );
}
