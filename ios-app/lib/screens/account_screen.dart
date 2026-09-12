import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../app_config.dart';
import '../services/api_service.dart';
import '../services/consent_service.dart';
import '../services/notification_service.dart';
import '../services/quant_lab_store.dart';
import '../widgets/common.dart';
import '../widgets/glass.dart';

class AccountScreen extends StatefulWidget {
  const AccountScreen({
    super.key,
    required this.api,
    required this.consent,
    required this.user,
    required this.onAuthChanged,
    required this.onManageProviderConnection,
    required this.onOpenStoredLabResult,
    required this.notifications,
  });
  final ApiService api;
  final ConsentService consent;
  final Map<String, dynamic>? user;
  final VoidCallback onAuthChanged;
  final VoidCallback onManageProviderConnection;
  final Future<void> Function(Map<String, dynamic> item) onOpenStoredLabResult;
  final NotificationService notifications;

  @override
  State<AccountScreen> createState() => AccountScreenState();
}

class AccountScreenState extends State<AccountScreen>
    with WidgetsBindingObserver {
  bool _notificationsEnabled = false;
  bool _dailyReminderEnabled = false;
  String _notificationStatus = 'unknown';
  List<String> _marketAlerts = const [];
  Map<String, dynamic>? _analysisStatus;
  bool _analysisLoading = false;
  String? _analysisError;
  final _quantLabStore = QuantLabStore();
  List<Map<String, dynamic>> _storedLabResults = const [];
  bool _storedLabsLoading = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _loadNotificationSettings();
    widget.notifications.refreshSchedules();
    _loadAnalysisStatus();
    _loadStoredLabResults();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && widget.user != null) {
      _loadAnalysisStatus();
      _loadStoredLabResults();
    }
  }

  @override
  void didUpdateWidget(covariant AccountScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.user == null && widget.user != null) {
      _syncMarketAlertsFromServer();
      _syncPushRegistration();
      _loadAnalysisStatus();
      _loadStoredLabResults();
    } else if (oldWidget.user != null && widget.user == null) {
      setState(() {
        _analysisStatus = null;
        _analysisError = null;
      });
    }
  }

  Future<void> _loadNotificationSettings() async {
    final enabled = await widget.notifications.isEnabled();
    final daily = await widget.notifications.isDailyReminderEnabled();
    final status = await widget.notifications.authorizationStatus();
    final marketAlerts = await widget.notifications.marketAlertTickers();
    if (mounted) {
      setState(() {
        _notificationsEnabled = enabled;
        _dailyReminderEnabled = daily;
        _notificationStatus = status;
        _marketAlerts = marketAlerts;
      });
    }
  }

  Future<void> _loadAnalysisStatus() async {
    if (widget.user == null) {
      if (mounted) setState(() => _analysisStatus = null);
      return;
    }
    setState(() {
      _analysisLoading = true;
      _analysisError = null;
    });
    try {
      final status = await widget.api.intelligenceStatus();
      if (mounted) setState(() => _analysisStatus = status);
    } catch (error) {
      if (mounted) setState(() => _analysisError = error.toString());
    } finally {
      if (mounted) setState(() => _analysisLoading = false);
    }
  }

  Future<void> _loadStoredLabResults() async {
    final results = await _quantLabStore.readAll();
    if (mounted) {
      setState(() {
        _storedLabResults = results;
        _storedLabsLoading = false;
      });
    }
  }

  Future<void> _deleteStoredLabResult(Map<String, dynamic> item) async {
    final id = item['id']?.toString() ?? '';
    if (id.isEmpty) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete stored result?'),
        content: const Text(
          'This removes this Quant Lab report from this device only.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    final results = await _quantLabStore.delete(id);
    if (mounted) setState(() => _storedLabResults = results);
  }

  String _storedLabWhen(Map<String, dynamic> item) {
    final savedAt = DateTime.tryParse(item['saved_at']?.toString() ?? '');
    if (savedAt == null) return 'Saved on this device';
    final local = savedAt.toLocal();
    return 'Saved ${local.year}-${local.month.toString().padLeft(2, '0')}-${local.day.toString().padLeft(2, '0')} · ${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';
  }

  Widget _buildStoredLabResults() {
    return AppCard(
      padding: EdgeInsets.zero,
      child: _storedLabsLoading
          ? const Padding(
              padding: EdgeInsets.all(18),
              child: Center(child: CircularProgressIndicator()),
            )
          : _storedLabResults.isEmpty
          ? const Padding(
              padding: EdgeInsets.all(18),
              child: Text(
                'No saved Quant Lab reports on this device yet. Completed reports are saved here automatically.',
                style: TextStyle(color: OryntraPalette.muted),
              ),
            )
          : Column(
              children: _storedLabResults.map((item) {
                final tickers = item['tickers'] is List
                    ? (item['tickers'] as List).join(', ')
                    : 'Research universe';
                final fingerprint = item['fingerprint']?.toString() ?? '';
                return ListTile(
                  leading: const Icon(Icons.auto_graph_outlined),
                  title: Text(
                    tickers.isEmpty ? 'Research universe' : tickers,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  subtitle: Text(
                    'Tap to reopen · ${_storedLabWhen(item)}${fingerprint.isEmpty ? '' : ' · ${fingerprint.substring(0, fingerprint.length.clamp(0, 8))}'}',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                  onTap: () => widget.onOpenStoredLabResult(item),
                  trailing: IconButton(
                    tooltip: 'Delete stored report',
                    icon: const Icon(Icons.delete_outline_rounded),
                    onPressed: () => _deleteStoredLabResult(item),
                  ),
                );
              }).toList(),
            ),
    );
  }

  Future<void> refreshProviderStatus() => _loadAnalysisStatus();

  Future<void> _toggleNotifications(bool enabled) async {
    if (enabled) {
      final granted = await widget.notifications.requestPermission();
      if (granted) {
        await Future<void>.delayed(const Duration(milliseconds: 750));
        await _syncPushRegistration();
      }
    } else {
      await widget.notifications.setEnabled(false);
    }
    await _loadNotificationSettings();
  }

  Future<void> _open(String url) =>
      launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);

  Future<void> _showAuth({bool create = false}) async {
    final changed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _AuthSheet(api: widget.api, create: create),
    );
    if (changed == true) widget.onAuthChanged();
  }

  Future<void> showCreateAccount() => _showAuth(create: true);

  Future<void> refreshNotificationSettings() => _loadNotificationSettings();

  Future<void> _deleteAccount() async {
    final password = TextEditingController();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete Oryntra AI account?'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'This permanently deletes your account, sessions, watchlist entries, subscriptions, and paper trades. This cannot be undone.',
            ),
            const SizedBox(height: 14),
            TextField(
              controller: password,
              obscureText: true,
              decoration: const InputDecoration(labelText: 'Confirm password'),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(context).colorScheme.error,
            ),
            child: const Text('Delete permanently'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await widget.api.deleteAccount(password.text);
      await widget.notifications.clearMarketAlerts();
      widget.onAuthChanged();
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('Account deleted.')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.toString())));
      }
    }
  }

  Future<void> _removeMarketAlert(String ticker) async {
    await widget.notifications.removeMarketAlert(ticker);
    if (widget.user != null) {
      try {
        await widget.api.removeStockAlertSubscription(ticker);
      } on ApiException {
        // Local removal still succeeds if remote alert cleanup is unavailable.
      }
    }
    await _loadNotificationSettings();
  }

  Future<void> _syncPushRegistration() async {
    if (widget.user == null) return;
    Map<String, String>? registration;
    for (var attempt = 0; attempt < 3 && registration == null; attempt++) {
      if (attempt > 0) {
        await Future<void>.delayed(Duration(seconds: attempt));
      }
      registration = await widget.notifications.pushRegistration();
    }
    if (registration == null) return;
    try {
      await widget.api.registerPushDevice(
        token: registration['token']!,
        environment: registration['environment']!,
      );
    } on ApiException {
      // Registration is best-effort and retried when the account view resumes.
    }
  }

  Future<void> _syncMarketAlertsFromServer() async {
    if (widget.user == null) return;
    try {
      final remote = await widget.api.stockAlertSubscriptions();
      final local = await widget.notifications.marketAlertTickers();
      final merged = <String>{
        ...remote,
        ...local,
      }.take(NotificationService.marketAlertLimit).toList();
      for (final ticker in merged.where((ticker) => !remote.contains(ticker))) {
        await widget.api.addStockAlertSubscription(ticker);
      }
      await widget.notifications.replaceMarketAlerts(merged);
    } on ApiException {
      // Preserve local alert settings when server synchronization is unavailable.
    }
    await _loadNotificationSettings();
  }

  Widget _buildAnalysisCard(BuildContext context) {
    final policyRaw = _analysisStatus?['policy'];
    final quotaRaw = _analysisStatus?['quota'];
    final policy = policyRaw is Map
        ? Map<String, dynamic>.from(policyRaw)
        : <String, dynamic>{};
    final quota = quotaRaw is Map
        ? Map<String, dynamic>.from(quotaRaw)
        : <String, dynamic>{};
    final permitted = policy['analysis_permitted'] == true;
    final used = quota['used']?.toString() ?? '—';
    final licenseMode =
        policy['license_mode']?.toString().replaceAll('_', ' ') ?? 'unknown';
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Icon(Icons.analytics_outlined, color: Color(0xFF38CFF3)),
              const SizedBox(width: 10),
              const Expanded(
                child: Text(
                  'Analysis access',
                  style: TextStyle(fontWeight: FontWeight.w900, fontSize: 17),
                ),
              ),
              if (_analysisLoading)
                const SizedBox.square(
                  dimension: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              else
                Icon(
                  permitted ? Icons.check_circle : Icons.lock_outline_rounded,
                  color: permitted ? const Color(0xFF2DD4BF) : Colors.white54,
                ),
            ],
          ),
          const SizedBox(height: 8),
          const Text(
            'Oryntra processes licensed candle data on the server and returns derived indicators, pattern evidence, confidence, and educational trade-planning levels. Raw OHLCV history is not distributed to the app.',
            style: TextStyle(fontSize: 12, color: Colors.white70),
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: Colors.white.withValues(alpha: .045),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: Colors.white.withValues(alpha: .08)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  permitted ? 'ANALYSIS ENABLED' : 'ANALYSIS RESTRICTED',
                  style: TextStyle(
                    color: permitted
                        ? const Color(0xFF2DD4BF)
                        : const Color(0xFFFBBF24),
                    fontWeight: FontWeight.w900,
                  ),
                ),
                const SizedBox(height: 5),
                Text('API calls made today: $used'),
                Text(
                  'License mode: ${licenseMode.toUpperCase()} · Chart: TradingView',
                  style: const TextStyle(fontSize: 11, color: Colors.white60),
                ),
              ],
            ),
          ),
          if (_analysisError != null) ...[
            const SizedBox(height: 10),
            Text(
              _analysisError!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ],
          const SizedBox(height: 10),
          OutlinedButton.icon(
            onPressed: _analysisLoading ? null : _loadAnalysisStatus,
            icon: const Icon(Icons.refresh_rounded),
            label: const Text('Refresh analysis access'),
          ),
          const SizedBox(height: 7),
          const Text(
            'The individual market-data configuration is restricted to the account owner. Public subscriptions must remain disabled until the operator has written commercial data rights.',
            style: TextStyle(fontSize: 10, color: Colors.white54),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final user = widget.user;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 130),
      children: [
        const InstitutionalSectionLabel(label: 'Account & controls'),
        LiquidGlass(
          child: user == null
              ? Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      'Your research workspace',
                      style: Theme.of(context).textTheme.headlineSmall
                          ?.copyWith(fontWeight: FontWeight.w800),
                    ),
                    const SizedBox(height: 6),
                    const Text(
                      'Sign in to keep your research, watchlist, and paper activity together.',
                    ),
                    const SizedBox(height: 16),
                    FilledButton(
                      onPressed: () => _showAuth(create: true),
                      child: const Text('Create account'),
                    ),
                    const SizedBox(height: 8),
                    OutlinedButton(
                      onPressed: _showAuth,
                      child: const Text('Sign in'),
                    ),
                  ],
                )
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      user['display_name']?.toString().isNotEmpty == true
                          ? user['display_name'].toString()
                          : 'Oryntra AI user',
                      style: Theme.of(context).textTheme.headlineSmall
                          ?.copyWith(fontWeight: FontWeight.w800),
                    ),
                    Text(
                      user['email']?.toString() ?? '',
                      style: const TextStyle(color: OryntraPalette.muted),
                    ),
                    const SizedBox(height: 16),
                    OutlinedButton(
                      onPressed: () async {
                        await widget.api.logout();
                        await widget.notifications.clearMarketAlerts();
                        widget.onAuthChanged();
                      },
                      child: const Text('Sign out'),
                    ),
                  ],
                ),
        ),
        const SizedBox(height: 14),
        if (user != null) _buildAnalysisCard(context),
        if (user != null) const SizedBox(height: 14),
        if (user != null) ...[
          const InstitutionalSectionLabel(label: 'Data connection'),
          AppCard(
            padding: EdgeInsets.zero,
            child: ListTile(
              leading: const Icon(Icons.key_outlined),
              title: const Text('API settings'),
              subtitle: const Text(
                'Change the Polygon / Massive or Twelve Data key on this phone.',
              ),
              trailing: const Icon(Icons.chevron_right_rounded),
              onTap: widget.onManageProviderConnection,
            ),
          ),
          const InstitutionalSectionLabel(label: 'Stored lab results'),
          _buildStoredLabResults(),
        ],
        const InstitutionalSectionLabel(label: 'Preferences'),
        AppCard(
          padding: EdgeInsets.zero,
          child: Column(
            children: [
              SwitchListTile.adaptive(
                secondary: const Icon(Icons.notifications_outlined),
                title: const Text('Notifications'),
                subtitle: Text('Permission: $_notificationStatus'),
                value: _notificationsEnabled,
                onChanged: _toggleNotifications,
              ),
              const Divider(height: 1),
              SwitchListTile.adaptive(
                secondary: const Icon(Icons.schedule_outlined),
                title: const Text('Daily market reminder'),
                subtitle: const Text('Weekdays at the 9:30 AM ET market open'),
                value: _dailyReminderEnabled,
                onChanged: _notificationsEnabled
                    ? (v) async {
                        await widget.notifications.setDailyReminder(v);
                        await _loadNotificationSettings();
                      }
                    : null,
              ),
            ],
          ),
        ),
        if (user != null)
          const InstitutionalSectionLabel(label: 'Market alerts'),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(
                    Icons.notifications_active_outlined,
                    color: Theme.of(context).colorScheme.primary,
                  ),
                  const SizedBox(width: 10),
                  const Expanded(
                    child: Text(
                      'Stock market alerts',
                      style: TextStyle(fontWeight: FontWeight.w800),
                    ),
                  ),
                  Text(
                    '${_marketAlerts.length}/${NotificationService.marketAlertLimit}',
                    style: const TextStyle(color: OryntraPalette.muted),
                  ),
                ],
              ),
              const SizedBox(height: 7),
              const Text(
                'Choose stocks after scanning. A 9:00 AM ET premarket check and noon/4:00 PM ET daily-move checks run on weekdays.',
                style: TextStyle(fontSize: 12, color: OryntraPalette.muted),
              ),
              const SizedBox(height: 12),
              if (_marketAlerts.isEmpty)
                const Text(
                  'No stocks selected yet.',
                  style: TextStyle(color: OryntraPalette.muted),
                )
              else
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: _marketAlerts
                      .map(
                        (ticker) => InputChip(
                          label: Text(ticker),
                          onDeleted: () => _removeMarketAlert(ticker),
                        ),
                      )
                      .toList(),
                ),
            ],
          ),
        ),
        const InstitutionalSectionLabel(label: 'Policies & support'),
        AppCard(
          padding: EdgeInsets.zero,
          child: Column(
            children: [
              ListTile(
                leading: const Icon(Icons.privacy_tip_outlined),
                title: const Text('Privacy choices'),
                onTap: widget.consent.showPrivacyOptions,
              ),
              ListTile(
                leading: const Icon(Icons.policy_outlined),
                title: const Text('Privacy policy'),
                onTap: () => _open(AppConfig.privacyUrl),
              ),
              ListTile(
                leading: const Icon(Icons.description_outlined),
                title: const Text('Terms of service'),
                onTap: () => _open(AppConfig.termsUrl),
              ),
              ListTile(
                leading: const Icon(Icons.warning_amber_outlined),
                title: const Text('Risk disclaimer'),
                onTap: () => _open(AppConfig.riskUrl),
              ),
              ListTile(
                leading: const Icon(Icons.receipt_long_outlined),
                title: const Text('Billing and subscription policy'),
                onTap: () => _open(AppConfig.billingUrl),
              ),
              ListTile(
                leading: const Icon(Icons.science_outlined),
                title: const Text('Methodology'),
                onTap: () => _open(AppConfig.methodologyUrl),
              ),
              ListTile(
                leading: const Icon(Icons.support_agent),
                title: const Text('Contact'),
                onTap: () => _open(AppConfig.contactUrl),
              ),
            ],
          ),
        ),
        if (user != null) ...[
          const InstitutionalSectionLabel(label: 'Account management'),
          AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  'Account controls',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 8),
                TextButton.icon(
                  onPressed: _deleteAccount,
                  icon: const Icon(Icons.delete_forever_outlined),
                  label: const Text('Delete account permanently'),
                  style: TextButton.styleFrom(
                    foregroundColor: Theme.of(context).colorScheme.error,
                  ),
                ),
              ],
            ),
          ),
        ],
        Padding(
          padding: const EdgeInsets.all(20),
          child: Text(
            'Oryntra provides educational market intelligence, not investment advice, brokerage services, or order execution. Charts are supplied independently by TradingView.\nOryntra AI v${AppConfig.appVersion}${AppConfig.previewMode ? ' · browser preview' : ''}',
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 12, color: OryntraPalette.muted),
          ),
        ),
      ],
    );
  }
}

class _AuthSheet extends StatefulWidget {
  const _AuthSheet({required this.api, required this.create});
  final ApiService api;
  final bool create;
  @override
  State<_AuthSheet> createState() => _AuthSheetState();
}

class _AuthSheetState extends State<_AuthSheet> {
  final _name = TextEditingController();
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _loading = false;
  bool _acceptLegal = false;
  String? _error;

  Future<void> _submit() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      if (widget.create) {
        await widget.api.signup(
          _email.text.trim(),
          _password.text,
          _name.text.trim(),
          _acceptLegal,
        );
      } else {
        await widget.api.login(_email.text.trim(), _password.text);
      }
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(
        12,
        12,
        12,
        MediaQuery.viewInsetsOf(context).bottom + 12,
      ),
      child: LiquidGlass(
        opacity: .94,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              widget.create ? 'Create account' : 'Sign in',
              style: Theme.of(
                context,
              ).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 14),
            if (widget.create) ...[
              TextField(
                controller: _name,
                textCapitalization: TextCapitalization.words,
                decoration: const InputDecoration(labelText: 'Display name'),
              ),
              const SizedBox(height: 10),
            ],
            TextField(
              controller: _email,
              keyboardType: TextInputType.emailAddress,
              autofillHints: const [AutofillHints.email],
              decoration: const InputDecoration(labelText: 'Email'),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: _password,
              obscureText: true,
              autofillHints: [
                widget.create
                    ? AutofillHints.newPassword
                    : AutofillHints.password,
              ],
              decoration: InputDecoration(
                labelText: widget.create
                    ? 'Password (8+ characters)'
                    : 'Password',
              ),
            ),
            if (widget.create) ...[
              const SizedBox(height: 10),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: _acceptLegal,
                onChanged: _loading
                    ? null
                    : (value) => setState(() => _acceptLegal = value ?? false),
                title: const Text(
                  'I accept the Terms, Privacy Policy, and Trading Risk Disclaimer.',
                  style: TextStyle(fontSize: 12),
                ),
              ),
              Wrap(
                spacing: 6,
                children: [
                  TextButton(
                    onPressed: () => launchUrl(
                      Uri.parse(AppConfig.termsUrl),
                      mode: LaunchMode.externalApplication,
                    ),
                    child: const Text('Terms'),
                  ),
                  TextButton(
                    onPressed: () => launchUrl(
                      Uri.parse(AppConfig.privacyUrl),
                      mode: LaunchMode.externalApplication,
                    ),
                    child: const Text('Privacy'),
                  ),
                  TextButton(
                    onPressed: () => launchUrl(
                      Uri.parse(AppConfig.riskUrl),
                      mode: LaunchMode.externalApplication,
                    ),
                    child: const Text('Risk disclaimer'),
                  ),
                ],
              ),
            ],
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: 10),
                child: Text(
                  _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
            const SizedBox(height: 16),
            FilledButton(
              onPressed: _loading ? null : _submit,
              child: Text(
                _loading
                    ? 'Please wait…'
                    : (widget.create ? 'Create account' : 'Sign in'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
