import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../app_config.dart';
import '../services/api_service.dart';
import '../services/provider_key_store.dart';
import '../services/turnstile_service.dart';
import '../widgets/glass.dart';

class AuthGateScreen extends StatefulWidget {
  const AuthGateScreen({
    super.key,
    required this.api,
    required this.onAuthenticated,
  });

  final ApiService api;
  final Future<void> Function() onAuthenticated;

  @override
  State<AuthGateScreen> createState() => _AuthGateScreenState();
}

class _AuthGateScreenState extends State<AuthGateScreen> {
  final _name = TextEditingController();
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _create = false;
  bool _acceptLegal = false;
  bool _loading = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_create && !_acceptLegal) {
      setState(
        () => _error =
            'Accept the Terms, Privacy Policy, and research-only disclosure to create an account.',
      );
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final captchaToken = AppConfig.previewMode
          ? ''
          : await requestTurnstileToken(context);
      if (!AppConfig.previewMode && captchaToken == null) {
        if (mounted) {
          setState(() => _error = 'Complete the security check to continue.');
        }
        return;
      }
      if (_create) {
        await widget.api.signup(
          _email.text.trim(),
          _password.text,
          _name.text.trim(),
          _acceptLegal,
          captchaToken: captchaToken ?? '',
        );
      } else {
        await widget.api.login(
          _email.text.trim(),
          _password.text,
          captchaToken: captchaToken ?? '',
        );
      }
      await widget.onAuthenticated();
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _open(String url) =>
      launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);

  @override
  Widget build(BuildContext context) {
    final colors = OryntraColors.of(context);
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: DecoratedBox(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: Theme.of(context).brightness == Brightness.dark
                ? const [Color(0xFF0A2341), Color(0xFF030B18)]
                : const [Color(0xFFF9FCFF), Color(0xFFE9F1F8)],
          ),
        ),
        child: SafeArea(
          child: LayoutBuilder(
            builder: (context, constraints) => SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
              child: ConstrainedBox(
                constraints: BoxConstraints(
                  minHeight: constraints.maxHeight - 44,
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Row(
                      children: [
                        ClipRRect(
                          borderRadius: BorderRadius.circular(11),
                          child: Image.asset(
                            'assets/oryntra-icon.png',
                            width: 42,
                            height: 42,
                          ),
                        ),
                        const SizedBox(width: 11),
                        Text(
                          'ORYNTRA AI',
                          style: Theme.of(context).textTheme.titleMedium
                              ?.copyWith(
                                letterSpacing: -.4,
                                fontWeight: FontWeight.w900,
                              ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 38),
                    Text(
                      'MARKET INTELLIGENCE',
                      style: TextStyle(
                        color: colors.blueBright,
                        fontSize: 11,
                        letterSpacing: 1.4,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    const SizedBox(height: 10),
                    Text(
                      'Find intelligence\nin every trade.',
                      style: Theme.of(context).textTheme.displaySmall?.copyWith(
                        fontWeight: FontWeight.w900,
                      ),
                    ),
                    const SizedBox(height: 12),
                    Text(
                      'A private market-research workspace for structured evidence—not brokerage execution or automated trading.',
                      style: Theme.of(
                        context,
                      ).textTheme.bodyMedium?.copyWith(color: colors.muted),
                    ),
                    const SizedBox(height: 26),
                    LiquidGlass(
                      radius: 24,
                      padding: const EdgeInsets.all(18),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(
                            _create
                                ? 'Create your account.'
                                : 'Sign in to continue.',
                            style: Theme.of(context).textTheme.headlineSmall
                                ?.copyWith(fontWeight: FontWeight.w900),
                          ),
                          const SizedBox(height: 5),
                          Text(
                            _create
                                ? 'Then connect one provider key directly from this phone.'
                                : 'Your workspace is tied to your Oryntra account.',
                            style: Theme.of(context).textTheme.bodySmall
                                ?.copyWith(color: colors.muted),
                          ),
                          const SizedBox(height: 18),
                          SegmentedButton<bool>(
                            segments: const [
                              ButtonSegment(
                                value: false,
                                label: Text('Sign in'),
                              ),
                              ButtonSegment(
                                value: true,
                                label: Text('Create account'),
                              ),
                            ],
                            selected: {_create},
                            onSelectionChanged: _loading
                                ? null
                                : (value) => setState(() {
                                    _create = value.first;
                                    _error = null;
                                  }),
                          ),
                          const SizedBox(height: 16),
                          if (_create) ...[
                            TextField(
                              controller: _name,
                              textCapitalization: TextCapitalization.words,
                              autofillHints: const [AutofillHints.name],
                              decoration: const InputDecoration(
                                labelText: 'Name',
                              ),
                            ),
                            const SizedBox(height: 10),
                          ],
                          TextField(
                            controller: _email,
                            keyboardType: TextInputType.emailAddress,
                            autofillHints: const [AutofillHints.email],
                            decoration: const InputDecoration(
                              labelText: 'Email address',
                            ),
                          ),
                          const SizedBox(height: 10),
                          TextField(
                            controller: _password,
                            obscureText: true,
                            autofillHints: [
                              _create
                                  ? AutofillHints.newPassword
                                  : AutofillHints.password,
                            ],
                            decoration: InputDecoration(
                              labelText: _create
                                  ? 'Password (8+ characters)'
                                  : 'Password',
                            ),
                          ),
                          if (_create) ...[
                            const SizedBox(height: 6),
                            CheckboxListTile(
                              contentPadding: EdgeInsets.zero,
                              value: _acceptLegal,
                              onChanged: _loading
                                  ? null
                                  : (value) => setState(
                                      () => _acceptLegal = value ?? false,
                                    ),
                              title: const Text(
                                'I accept the Terms, Privacy Policy, and research-only risk disclosure.',
                                style: TextStyle(fontSize: 12),
                              ),
                              controlAffinity: ListTileControlAffinity.leading,
                            ),
                            Wrap(
                              spacing: 2,
                              children: [
                                TextButton(
                                  onPressed: () => _open(AppConfig.termsUrl),
                                  child: const Text('Terms'),
                                ),
                                TextButton(
                                  onPressed: () => _open(AppConfig.privacyUrl),
                                  child: const Text('Privacy'),
                                ),
                                TextButton(
                                  onPressed: () => _open(AppConfig.riskUrl),
                                  child: const Text('Risk disclosure'),
                                ),
                              ],
                            ),
                          ],
                          if (_error != null)
                            Padding(
                              padding: const EdgeInsets.only(top: 10),
                              child: Text(
                                _error!,
                                style: TextStyle(
                                  color: Theme.of(context).colorScheme.error,
                                  fontSize: 12,
                                ),
                              ),
                            ),
                          const SizedBox(height: 14),
                          FilledButton(
                            onPressed: _loading ? null : _submit,
                            child: Text(
                              _loading
                                  ? 'Please wait…'
                                  : (_create ? 'Create account' : 'Sign in'),
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 16),
                    Text(
                      'V1.0 · RESEARCH ONLY · NO BROKER EXECUTION',
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        color: colors.muted,
                        fontSize: 10,
                        letterSpacing: .8,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class ProviderSetupScreen extends StatefulWidget {
  const ProviderSetupScreen({
    super.key,
    required this.api,
    required this.onConnected,
    this.onSignOut,
  });

  final ApiService api;
  final Future<void> Function() onConnected;
  final Future<void> Function()? onSignOut;

  @override
  State<ProviderSetupScreen> createState() => _ProviderSetupScreenState();
}

class _ProviderSetupScreenState extends State<ProviderSetupScreen> {
  final _key = TextEditingController();
  final _store = ProviderKeyStore();
  String _provider = 'polygon';
  bool _saving = false;
  bool _hasSavedKey = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadSavedConnection();
  }

  Future<void> _loadSavedConnection() async {
    final connection = await _store.readConnection();
    if (!mounted || connection == null) return;
    setState(() {
      _provider = connection.provider;
      _hasSavedKey = true;
    });
  }

  @override
  void dispose() {
    _key.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_key.text.trim().isEmpty) {
      setState(() => _error = 'Paste your provider API key to continue.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await widget.api.verifyProviderKey(_provider, _key.text);
      await _store.saveConnection(_provider, _key.text);
      if (mounted) setState(() => _hasSavedKey = true);
      if (!mounted) return;
      final choice = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Provider connected'),
          content: const Text(
            'Your key is kept in this phone’s encrypted app storage and is sent directly to the selected provider. Oryntra does not receive or store it.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Edit keys'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Continue to Oryntra'),
            ),
          ],
        ),
      );
      if (choice == true) await widget.onConnected();
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _open(String url) =>
      launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);

  @override
  Widget build(BuildContext context) {
    final colors = OryntraColors.of(context);
    final polygon = _provider == 'polygon';
    final providerName = polygon ? 'Polygon / Massive' : 'Twelve Data';
    return Scaffold(
      backgroundColor: colors.deepNavy,
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(20),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 520),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      ClipRRect(
                        borderRadius: BorderRadius.circular(10),
                        child: Image.asset(
                          'assets/oryntra-icon.png',
                          width: 38,
                          height: 38,
                        ),
                      ),
                      const SizedBox(width: 10),
                      const Text(
                        'ORYNTRA AI',
                        style: TextStyle(fontWeight: FontWeight.w900),
                      ),
                    ],
                  ),
                  const SizedBox(height: 42),
                  Text(
                    'STEP 2 OF 2 · YOUR DATA CONNECTION',
                    style: TextStyle(
                      color: colors.blueBright,
                      fontSize: 11,
                      letterSpacing: 1.2,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    'Connect one provider key.',
                    style: Theme.of(context).textTheme.displaySmall?.copyWith(
                      fontWeight: FontWeight.w900,
                    ),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    'Your key stays in this phone’s encrypted app storage and is sent directly to your chosen provider. Oryntra only receives the normalized bars needed for an in-memory analysis.',
                    style: Theme.of(
                      context,
                    ).textTheme.bodyMedium?.copyWith(color: colors.muted),
                  ),
                  const SizedBox(height: 24),
                  LiquidGlass(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        SegmentedButton<String>(
                          segments: const [
                            ButtonSegment(
                              value: 'polygon',
                              label: Text('Polygon / Massive'),
                            ),
                            ButtonSegment(
                              value: 'twelvedata',
                              label: Text('Twelve Data'),
                            ),
                          ],
                          selected: {_provider},
                          onSelectionChanged: _saving
                              ? null
                              : (choice) => setState(() {
                                  _provider = choice.first;
                                  _error = null;
                                }),
                        ),
                        const SizedBox(height: 18),
                        Text(
                          providerName,
                          style: Theme.of(context).textTheme.titleLarge
                              ?.copyWith(fontWeight: FontWeight.w900),
                        ),
                        const SizedBox(height: 5),
                        Text(
                          polygon
                              ? 'EOD daily bars · Basic: 5 calls/min · suited to daily and swing research.'
                              : '1-minute capability · Basic: 8 credits/min, 800/day · Oryntra V1.0 uses daily bars.',
                          style: Theme.of(
                            context,
                          ).textTheme.bodySmall?.copyWith(color: colors.muted),
                        ),
                        const SizedBox(height: 8),
                        TextButton.icon(
                          onPressed: () => _open(
                            polygon
                                ? 'https://www.polygon.io/dashboard/subscriptions?checkoutCycle=monthly&checkoutProducts=stocks_advanced'
                                : 'https://twelvedata.com/register',
                          ),
                          icon: Icon(
                            Icons.open_in_new_rounded,
                            color: colors.blueBright,
                          ),
                          label: Text(
                            'Create account / get key',
                            style: TextStyle(
                              color: colors.blueBright,
                              fontWeight: FontWeight.w800,
                            ),
                          ),
                        ),
                        const SizedBox(height: 8),
                        TextField(
                          controller: _key,
                          obscureText: true,
                          autocorrect: false,
                          enableSuggestions: false,
                          decoration: const InputDecoration(
                            labelText: 'API key',
                            hintText: 'Paste your key',
                          ),
                        ),
                        if (_hasSavedKey)
                          Padding(
                            padding: const EdgeInsets.only(top: 8),
                            child: Text(
                              'A saved $providerName connection is already on this phone. Paste a new key only to replace it.',
                              style: TextStyle(
                                fontSize: 11,
                                color: colors.muted,
                              ),
                            ),
                          ),
                        if (_error != null)
                          Padding(
                            padding: const EdgeInsets.only(top: 10),
                            child: Text(
                              _error!,
                              style: TextStyle(
                                color: Theme.of(context).colorScheme.error,
                              ),
                            ),
                          ),
                        const SizedBox(height: 16),
                        FilledButton(
                          onPressed: _saving ? null : _save,
                          child: Text(
                            _saving ? 'Verifying key…' : 'Verify and save key',
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 16),
                  Text(
                    'Provider-plan capability is not a redistribution license. Use a plan and rights that allow your exact use.',
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 11, color: colors.muted),
                  ),
                  if (widget.onSignOut != null)
                    TextButton(
                      onPressed: _saving ? null : widget.onSignOut,
                      child: const Text('Use a different account'),
                    ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class AppStartupScreen extends StatelessWidget {
  const AppStartupScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return const Scaffold(backgroundColor: Colors.black, body: _StartupMark());
  }
}

class _StartupMark extends StatefulWidget {
  const _StartupMark();

  @override
  State<_StartupMark> createState() => _StartupMarkState();
}

class _StartupMarkState extends State<_StartupMark>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2000),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Center(
      child: AnimatedBuilder(
        animation: _controller,
        builder: (context, child) {
          final progress = _controller.value;
          final arrival = Curves.easeOutCubic.transform(
            (progress / .42).clamp(0.0, 1.0),
          );
          final breathe =
              (Curves.easeInOut.transform(((progress + .16) % 1.0)) - .5) * 2;
          return Stack(
            alignment: Alignment.center,
            children: [
              _StartupRing(
                progress: (progress + .08) % 1.0,
                size: 150,
                opacity: .30,
              ),
              _StartupRing(
                progress: (progress + .52) % 1.0,
                size: 150,
                opacity: .18,
              ),
              Transform.translate(
                offset: Offset(0, 18 * (1 - arrival) + breathe * 3),
                child: Opacity(
                  opacity: .25 + arrival * .75,
                  child: Transform.scale(
                    scale: .72 + arrival * .28 + breathe * .025,
                    child: child,
                  ),
                ),
              ),
            ],
          );
        },
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(25),
              child: Image.asset(
                'assets/oryntra-icon.png',
                width: 104,
                height: 104,
              ),
            ),
            const SizedBox(height: 22),
            const Text(
              'ORYNTRA AI',
              style: TextStyle(
                color: Color(0xFFEAF7FF),
                fontSize: 12,
                fontWeight: FontWeight.w800,
                letterSpacing: 2.6,
              ),
            ),
            const SizedBox(height: 9),
            const Text(
              'Opening your research workspace',
              style: TextStyle(color: Color(0xFF7E9CB9), fontSize: 11),
            ),
          ],
        ),
      ),
    );
  }
}

class _StartupRing extends StatelessWidget {
  const _StartupRing({
    required this.progress,
    required this.size,
    required this.opacity,
  });

  final double progress;
  final double size;
  final double opacity;

  @override
  Widget build(BuildContext context) {
    final expansion = Curves.easeOut.transform(progress);
    return Opacity(
      opacity: (1 - expansion) * opacity,
      child: Transform.scale(
        scale: .62 + expansion * 1.2,
        child: Container(
          width: size,
          height: size,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            border: Border.all(color: const Color(0xFF3ACBF4), width: 1.3),
          ),
        ),
      ),
    );
  }
}
