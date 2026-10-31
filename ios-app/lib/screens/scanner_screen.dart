import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../app_config.dart';
import '../services/api_service.dart';
import '../services/notification_service.dart';
import '../services/widget_service.dart';
import '../widgets/common.dart';
import '../widgets/glass.dart';
import '../widgets/tradingview_chart.dart';

class ScannerScreen extends StatefulWidget {
  const ScannerScreen({
    super.key,
    required this.api,
    required this.notifications,
    required this.widgetService,
    required this.signedIn,
    required this.onCreateAccount,
    required this.onPaperTradeOpened,
    required this.onMarketAlertsChanged,
  });

  final ApiService api;
  final NotificationService notifications;
  final WidgetService widgetService;
  final bool signedIn;
  final VoidCallback onCreateAccount;
  final Future<void> Function() onPaperTradeOpened;
  final VoidCallback onMarketAlertsChanged;

  @override
  State<ScannerScreen> createState() => ScannerScreenState();
}

class ScannerScreenState extends State<ScannerScreen> {
  final _ticker = TextEditingController(text: 'AAPL');
  String _period = '6mo';
  bool _loading = false;
  bool _openingPaperTrade = false;
  Map<String, dynamic>? _result;
  String? _error;
  List<String> _marketAlertTickers = const [];

  @override
  void initState() {
    super.initState();
    _loadMarketAlerts();
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  Future<void> scanTicker(String ticker) async {
    final normalized = ticker.trim().toUpperCase();
    if (normalized.isEmpty) return;
    _ticker.value = TextEditingValue(
      text: normalized,
      selection: TextSelection.collapsed(offset: normalized.length),
    );
    await _scan();
  }

  Future<void> _scan() async {
    final ticker = _ticker.text.trim().toUpperCase();
    if (ticker.isEmpty) return;
    if (!widget.signedIn) {
      widget.onCreateAccount();
      return;
    }
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() {
      _loading = true;
      _error = null;
      _result = null;
    });
    try {
      final result = await widget.api.scan(ticker, period: _period);
      await widget.widgetService.updateScan(
        ticker: ticker,
        signal: _read(result, ['signal']),
        price: _read(result, ['trade_plan', 'entry_ideal']),
        quality: _read(result, ['quality_score']),
      );
      if (mounted) {
        setState(() => _result = result);
      }
    } on ApiException catch (error) {
      if (error.statusCode == 401 && mounted) {
        widget.onCreateAccount();
      } else if (mounted) {
        setState(() => _error = error.toString());
      }
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _loadMarketAlerts() async {
    final tickers = await widget.notifications.marketAlertTickers();
    if (mounted) setState(() => _marketAlertTickers = tickers);
  }

  String _read(
    Map<String, dynamic>? source,
    List<String> keys, {
    String fallback = '—',
  }) {
    dynamic current = source;
    for (final key in keys) {
      if (current is Map && current.containsKey(key)) {
        current = current[key];
      } else {
        return fallback;
      }
    }
    if (current == null) return fallback;
    if (current is double) return current.toStringAsFixed(2);
    return current.toString();
  }

  double? _number(dynamic value) {
    if (value is num) return value.toDouble();
    return double.tryParse(
      value?.toString().replaceAll(RegExp(r'[^0-9.\-]'), '') ?? '',
    );
  }

  List<Map<String, dynamic>> _patterns() {
    dynamic raw =
        _result?['patterns'] ??
        _result?['detected_patterns'] ??
        _result?['pattern_results'];
    if (raw is Map) {
      raw =
          raw['recent'] ??
          raw['items'] ??
          raw['patterns'] ??
          raw['top_pattern'];
    }
    if (raw is Map) raw = [raw];
    if (raw is! List) return const [];
    return raw.map((item) {
      if (item is Map) return Map<String, dynamic>.from(item);
      return <String, dynamic>{'name': item.toString()};
    }).toList();
  }

  String _setup() {
    return _read(
      _result,
      ['setup', 'setup_type'],
      fallback: _read(_result, [
        'setup',
        'type',
      ], fallback: _read(_result, ['setup_type'])),
    ).replaceAll('_', ' ');
  }

  String _entry() {
    return _read(
      _result,
      ['trade_plan', 'entry_ideal'],
      fallback: _read(_result, [
        'trade_plan',
        'entry',
      ], fallback: _read(_result, ['entry'])),
    );
  }

  String _scanCount() {
    return _read(
      _result,
      ['search_counter'],
      fallback: _read(_result, [
        'scan_count',
      ], fallback: _read(_result, ['total_scans'])),
    );
  }

  Future<void> _openLegal(String url, String label) async {
    final opened = await launchUrl(
      Uri.parse(url),
      mode: LaunchMode.externalApplication,
    );
    if (!opened && mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('Could not open $label.')));
    }
  }

  Future<void> _notifyAboutResult() async {
    if (_result == null) return;
    final allowed = await widget.notifications.requestPermission();
    if (!allowed || !mounted) return;
    await widget.notifications.showScanResult(
      ticker: _read(_result, ['ticker']),
      signal: _read(_result, ['signal']),
      quality: _read(_result, ['quality_score']),
    );
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            AppConfig.previewMode
                ? 'Notification interaction simulated in browser preview.'
                : 'Scan notification created.',
          ),
        ),
      );
    }
  }

  Future<void> _trackResult() async {
    if (_result == null) return;
    if (!widget.signedIn) {
      widget.onCreateAccount();
      return;
    }
    final ticker = _read(_result, ['ticker']).trim().toUpperCase();
    var outcome = await widget.notifications.addMarketAlert(ticker);
    if (outcome == MarketAlertAddResult.added ||
        outcome == MarketAlertAddResult.alreadyAdded) {
      try {
        await widget.api.addStockAlertSubscription(ticker);
      } on ApiException catch (error) {
        if (error.statusCode == 409) {
          await widget.notifications.removeMarketAlert(ticker);
          await _loadMarketAlerts();
          outcome = MarketAlertAddResult.limitReached;
        } else if (error.statusCode != 404) {
          if (mounted) {
            ScaffoldMessenger.of(
              context,
            ).showSnackBar(SnackBar(content: Text(error.toString())));
          }
        }
      }
    }
    await _loadMarketAlerts();
    widget.onMarketAlertsChanged();
    if (!mounted) return;
    final message = switch (outcome) {
      MarketAlertAddResult.added =>
        '$ticker added. Market reminders are set for 9:35 AM, noon, and 4:00 PM ET.',
      MarketAlertAddResult.alreadyAdded =>
        '$ticker is already one of your market alerts.',
      MarketAlertAddResult.limitReached =>
        'You can track up to ${NotificationService.marketAlertLimit} stocks. Remove one in Settings first.',
      MarketAlertAddResult.permissionDenied =>
        'Allow notifications in iPhone Settings to use market alerts.',
    };
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  String? _paperDirection() {
    final raw = _read(_result, [
      'trade_plan',
      'direction',
    ], fallback: _read(_result, ['setup', 'direction'])).toUpperCase();
    if (raw.contains('LONG') || raw.contains('BUY') || raw.contains('BULL')) {
      return 'LONG';
    }
    if (raw.contains('SHORT') || raw.contains('SELL') || raw.contains('BEAR')) {
      return 'SHORT';
    }
    return null;
  }

  bool get _hasPaperTradeSetup {
    final entry = _number(
      _read(_result, [
        'trade_plan',
        'entry_ideal',
      ], fallback: _read(_result, ['entry'])),
    );
    final stop = _number(
      _read(_result, [
        'trade_plan',
        'stop',
      ], fallback: _read(_result, ['stop'])),
    );
    final target = _number(
      _read(_result, [
        'trade_plan',
        'target',
      ], fallback: _read(_result, ['target'])),
    );
    return _paperDirection() != null &&
        entry != null &&
        stop != null &&
        target != null &&
        entry > 0 &&
        stop > 0 &&
        target > 0;
  }

  Future<void> _openPaperTrade() async {
    if (_result == null || !_hasPaperTradeSetup) return;
    if (!widget.signedIn) {
      widget.onCreateAccount();
      return;
    }
    final ticker = _read(_result, ['ticker']).trim().toUpperCase();
    final direction = _paperDirection()!;
    final entry = _number(
      _read(_result, [
        'trade_plan',
        'entry_ideal',
      ], fallback: _read(_result, ['entry'])),
    )!;
    final stop = _number(
      _read(_result, [
        'trade_plan',
        'stop',
      ], fallback: _read(_result, ['stop'])),
    )!;
    final target = _number(
      _read(_result, [
        'trade_plan',
        'target',
      ], fallback: _read(_result, ['target'])),
    )!;
    final quality = _number(
      _read(_result, [
        'quality_score',
      ], fallback: _read(_result, ['trade_plan', 'quality_score'])),
    );
    final shares = TextEditingController(text: '1');
    final size = await showModalBottomSheet<double>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) => Padding(
        padding: EdgeInsets.fromLTRB(
          12,
          12,
          12,
          MediaQuery.viewInsetsOf(context).bottom + 12,
        ),
        child: Material(
          color: OryntraPalette.navy,
          borderRadius: BorderRadius.circular(24),
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  'Open a $ticker paper trade?',
                  style: Theme.of(
                    context,
                  ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900),
                ),
                const SizedBox(height: 8),
                Text(
                  '$direction • Entry \$${entry.toStringAsFixed(2)} • Stop \$${stop.toStringAsFixed(2)} • Target \$${target.toStringAsFixed(2)}',
                  style: const TextStyle(color: OryntraPalette.muted),
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: shares,
                  autofocus: true,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(
                    labelText: 'Simulated shares',
                  ),
                ),
                const SizedBox(height: 12),
                const Text(
                  'This is an educational simulation only. No real order will be placed.',
                  style: TextStyle(fontSize: 12, color: OryntraPalette.muted),
                ),
                const SizedBox(height: 18),
                FilledButton(
                  onPressed: () {
                    final parsed = double.tryParse(shares.text.trim());
                    if (parsed != null && parsed > 0) {
                      Navigator.pop(context, parsed);
                    }
                  },
                  child: const Text('Open simulated trade'),
                ),
                TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('Cancel'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
    shares.dispose();
    if (size == null || !mounted) return;
    setState(() => _openingPaperTrade = true);
    try {
      await widget.api.openPaperTrade(
        ticker: ticker,
        direction: direction,
        entryPrice: entry,
        stopPrice: stop,
        targetPrice: target,
        size: size,
        notes: 'Opened from the Oryntra iOS scanner.',
        setupType: _setup(),
        qualityScore: quality,
      );
      if (!mounted) return;
      await widget.onPaperTradeOpened();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'Could not save this simulated trade. Please try again.',
            ),
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _openingPaperTrade = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final primary = Theme.of(context).colorScheme.primary;
    return ListView(
      padding: const EdgeInsets.only(bottom: 130),
      keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
      children: [
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text(
                'MARKET INTELLIGENCE',
                style: TextStyle(
                  color: OryntraPalette.blueBright,
                  fontSize: 11,
                  letterSpacing: 1.25,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 9),
              Row(
                children: [
                  Container(
                    width: 46,
                    height: 46,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(14),
                      color: OryntraPalette.panelRaised,
                      border: Border.all(color: OryntraPalette.rule),
                    ),
                    child: const Icon(
                      Icons.radar_rounded,
                      color: OryntraPalette.blueBright,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Find the setup.',
                          style: Theme.of(context).textTheme.titleLarge
                              ?.copyWith(fontWeight: FontWeight.w900),
                        ),
                        const Text(
                          'Structured analysis. Clear evidence. No broker execution.',
                          style: TextStyle(
                            color: OryntraPalette.muted,
                            fontSize: 12,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 20),
              TextField(
                controller: _ticker,
                textCapitalization: TextCapitalization.characters,
                autocorrect: false,
                decoration: const InputDecoration(
                  labelText: 'Ticker',
                  hintText: 'AAPL',
                  prefixIcon: Icon(Icons.search),
                ),
                onSubmitted: (_) => _scan(),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: _period,
                decoration: const InputDecoration(
                  labelText: 'Analysis lookback',
                ),
                items: const [
                  DropdownMenuItem(value: '1mo', child: Text('1 month')),
                  DropdownMenuItem(value: '6mo', child: Text('6 months')),
                  DropdownMenuItem(value: '1y', child: Text('1 year')),
                  DropdownMenuItem(value: '5y', child: Text('5 years')),
                ],
                onChanged: (value) => setState(() => _period = value ?? '6mo'),
              ),
              const SizedBox(height: 14),
              FilledButton.icon(
                onPressed: _loading ? null : _scan,
                icon: _loading
                    ? const SizedBox.square(
                        dimension: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.analytics_outlined),
                label: Text(_loading ? 'Analyzing…' : 'Analyze ticker'),
              ),
              const SizedBox(height: 10),
              Wrap(
                alignment: WrapAlignment.center,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  const Text(
                    'By searching for a stock using our scanner, you agree to our ',
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 11, color: OryntraPalette.muted),
                  ),
                  InkWell(
                    borderRadius: BorderRadius.circular(6),
                    onTap: () =>
                        _openLegal(AppConfig.termsUrl, 'Terms of Service'),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 2,
                        vertical: 4,
                      ),
                      child: Text(
                        'Terms of Service',
                        style: TextStyle(
                          fontSize: 11,
                          color: primary,
                          fontWeight: FontWeight.w800,
                          decoration: TextDecoration.underline,
                          decorationColor: primary,
                        ),
                      ),
                    ),
                  ),
                  const Text(
                    '.',
                    style: TextStyle(fontSize: 11, color: OryntraPalette.muted),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: ['AAPL', 'NVDA', 'MSFT', 'TSLA']
                    .map(
                      (ticker) => ActionChip(
                        label: Text(ticker),
                        avatar: const Icon(Icons.bolt_rounded, size: 16),
                        onPressed: _loading ? null : () => scanTicker(ticker),
                      ),
                    )
                    .toList(),
              ),
            ],
          ),
        ),
        if (_error != null)
          AppCard(
            child: Text(
              _error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
        if (_result != null) ...[
          const InstitutionalSectionLabel(label: 'Analysis output'),
          _ScanResult(
            result: _result!,
            period: _period,
            chartSymbol: _read(_result, [
              'chart',
              'symbol',
            ], fallback: _read(_result, ['ticker'])),
            patterns: _patterns(),
            setup: _setup(),
            entry: _entry(),
            scanCount: _scanCount(),
            read: (keys, {fallback = '—'}) =>
                _read(_result, keys, fallback: fallback),
            onPaperTrade: _hasPaperTradeSetup ? _openPaperTrade : null,
            openingPaperTrade: _openingPaperTrade,
            onTrackStock: _trackResult,
            stockTracked: _marketAlertTickers.contains(
              _read(_result, ['ticker']).trim().toUpperCase(),
            ),
            onTestNotification: _notifyAboutResult,
          ),
        ],
        const InstitutionalSectionLabel(label: 'Methodology'),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'What the scanner evaluates',
                style: Theme.of(
                  context,
                ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w900),
              ),
              const SizedBox(height: 14),
              const _ScannerFeature(
                icon: Icons.timeline_rounded,
                title: 'Trend context',
                body: 'Reviews historical direction over your selected period.',
              ),
              const _ScannerFeature(
                icon: Icons.auto_graph_rounded,
                title: 'Pattern structure',
                body:
                    'Surfaces qualifying formations with directional bias and confidence.',
              ),
              const _ScannerFeature(
                icon: Icons.science_outlined,
                title: 'Simulated plan',
                body:
                    'Shows educational entry, stop, target, and risk-to-reward levels.',
              ),
              const Divider(height: 26),
              const Text(
                'Results use historical market data and may be delayed. They do not predict future performance or place real trades.',
                style: TextStyle(fontSize: 12, color: OryntraPalette.muted),
              ),
              const SizedBox(height: 8),
              TextButton.icon(
                onPressed: () =>
                    _openLegal(AppConfig.riskUrl, 'Risk Disclaimer'),
                icon: const Icon(Icons.open_in_new_rounded, size: 17),
                label: const Text('Read the Risk Disclaimer'),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

typedef _Reader = String Function(List<String> keys, {String fallback});

class _ScanResult extends StatelessWidget {
  const _ScanResult({
    required this.result,
    required this.period,
    required this.chartSymbol,
    required this.patterns,
    required this.setup,
    required this.entry,
    required this.scanCount,
    required this.read,
    required this.onPaperTrade,
    required this.openingPaperTrade,
    required this.onTrackStock,
    required this.stockTracked,
    required this.onTestNotification,
  });

  final Map<String, dynamic> result;
  final String period;
  final String chartSymbol;
  final List<Map<String, dynamic>> patterns;
  final String setup;
  final String entry;
  final String scanCount;
  final _Reader read;
  final VoidCallback? onPaperTrade;
  final bool openingPaperTrade;
  final VoidCallback onTrackStock;
  final bool stockTracked;
  final VoidCallback onTestNotification;

  Color _signalColor(String signal) {
    final upper = signal.toUpperCase();
    if (upper.contains('BUY') ||
        upper.contains('BULL') ||
        upper.contains('LONG')) {
      return OryntraPalette.green;
    }
    if (upper.contains('SELL') ||
        upper.contains('BEAR') ||
        upper.contains('SHORT')) {
      return OryntraPalette.danger;
    }
    return OryntraPalette.blueBright;
  }

  @override
  Widget build(BuildContext context) {
    final signal = read(['signal'], fallback: read(['trade_plan', 'signal']));
    final signalColor = _signalColor(signal);
    final ticker = read(['ticker']);
    final company = read(['company_name'], fallback: ticker);
    final quality = read([
      'quality_score',
    ], fallback: read(['trade_plan', 'quality_score']));
    final price = entry;
    final stop = read(['trade_plan', 'stop'], fallback: read(['stop']));
    final target = read(['trade_plan', 'target'], fallback: read(['target']));
    final riskReward = read(['trade_plan', 'risk_reward']);

    return AppCard(
      padding: EdgeInsets.zero,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(22),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(
              padding: const EdgeInsets.all(18),
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  colors: [
                    signalColor.withValues(alpha: .30),
                    OryntraPalette.panelRaised,
                    OryntraPalette.navy,
                  ],
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              ticker,
                              style: Theme.of(context).textTheme.headlineMedium
                                  ?.copyWith(
                                    fontWeight: FontWeight.w900,
                                    letterSpacing: -.7,
                                  ),
                            ),
                            Text(
                              company,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                color: OryntraPalette.muted,
                                fontSize: 12,
                              ),
                            ),
                          ],
                        ),
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 13,
                          vertical: 8,
                        ),
                        decoration: BoxDecoration(
                          color: signalColor.withValues(alpha: .18),
                          borderRadius: BorderRadius.circular(999),
                          border: Border.all(
                            color: signalColor.withValues(alpha: .55),
                          ),
                        ),
                        child: Text(
                          signal,
                          style: TextStyle(
                            color: signalColor,
                            fontWeight: FontWeight.w900,
                            fontSize: 12,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Text(
                        '\$$price',
                        style: const TextStyle(
                          fontSize: 32,
                          fontWeight: FontWeight.w900,
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Padding(
                          padding: const EdgeInsets.only(bottom: 5),
                          child: Text(
                            setup,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              color: signalColor,
                              fontWeight: FontWeight.w800,
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 12,
                    ),
                    decoration: BoxDecoration(
                      color: OryntraPalette.deepNavy.withValues(alpha: .52),
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(color: OryntraPalette.rule),
                    ),
                    child: Row(
                      children: [
                        Expanded(
                          child: _TradeLevel(
                            label: 'Entry',
                            value: '\$$entry',
                            color: OryntraPalette.blueBright,
                          ),
                        ),
                        _LevelDivider(color: OryntraPalette.rule),
                        Expanded(
                          child: _TradeLevel(
                            label: 'Stop',
                            value: '\$$stop',
                            color: OryntraPalette.danger,
                          ),
                        ),
                        _LevelDivider(color: OryntraPalette.rule),
                        Expanded(
                          child: _TradeLevel(
                            label: 'Target',
                            value: '\$$target',
                            color: OryntraPalette.green,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 9),
                  Wrap(
                    spacing: 7,
                    runSpacing: 7,
                    children: [
                      _SummaryPill(
                        label: 'Quality',
                        value: quality,
                        color: OryntraPalette.blueBright,
                      ),
                      _SummaryPill(
                        label: 'R/R',
                        value: riskReward == '—' ? '—' : '$riskReward×',
                        color: OryntraPalette.green,
                      ),
                      _SummaryPill(
                        label: 'Analyses',
                        value: scanCount,
                        color: OryntraPalette.muted,
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  const Row(
                    children: [
                      Icon(
                        Icons.touch_app_outlined,
                        size: 15,
                        color: OryntraPalette.blueBright,
                      ),
                      SizedBox(width: 6),
                      Text(
                        'Interactive chart',
                        style: TextStyle(fontWeight: FontWeight.w800),
                      ),
                      Spacer(),
                      Text(
                        'Drag · pinch to zoom',
                        style: TextStyle(
                          color: OryntraPalette.muted,
                          fontSize: 11,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  TradingViewChart(
                    symbol: chartSymbol,
                    height: MediaQuery.sizeOf(
                      context,
                    ).height.clamp(320.0, 410.0).toDouble(),
                  ),
                  const SizedBox(height: 7),
                  Text(
                    '${period.toUpperCase()} analysis · chart data supplied independently by TradingView',
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      fontSize: 10,
                      color: OryntraPalette.muted,
                    ),
                  ),
                  const SizedBox(height: 14),
                  _IndicatorSummary(read: read),
                  const SizedBox(height: 14),
                  _PatternCard(patterns: patterns),
                  const SizedBox(height: 14),
                  OutlinedButton.icon(
                    onPressed: openingPaperTrade ? null : onPaperTrade,
                    icon: openingPaperTrade
                        ? const SizedBox.square(
                            dimension: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.science_outlined),
                    label: Text(
                      onPaperTrade == null
                          ? 'No paper-trade setup available'
                          : (openingPaperTrade
                                ? 'Opening simulated trade…'
                                : 'Paper trade this setup'),
                    ),
                  ),
                  const SizedBox(height: 9),
                  OutlinedButton.icon(
                    onPressed: stockTracked ? null : onTrackStock,
                    icon: const Icon(Icons.notifications_active_outlined),
                    label: Text(
                      stockTracked
                          ? '$ticker market alerts are on'
                          : 'Notify me about $ticker',
                    ),
                  ),
                  const SizedBox(height: 4),
                  const Text(
                    'Up to 5 stocks. Weekday reminders at 9:35 AM, noon, and 4:00 PM ET.',
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 11, color: OryntraPalette.muted),
                  ),
                  Align(
                    alignment: Alignment.centerRight,
                    child: TextButton.icon(
                      onPressed: onTestNotification,
                      style: TextButton.styleFrom(
                        foregroundColor: OryntraPalette.muted,
                        visualDensity: VisualDensity.compact,
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 3,
                        ),
                        textStyle: const TextStyle(fontSize: 11),
                      ),
                      icon: const Icon(
                        Icons.notification_add_outlined,
                        size: 14,
                      ),
                      label: const Text('Test alert'),
                    ),
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    'Oryntra provides educational market intelligence, not investment advice. Charts are supplied independently by TradingView. Oryntra does not execute trades.',
                    style: TextStyle(fontSize: 12, color: OryntraPalette.muted),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _TradeLevel extends StatelessWidget {
  const _TradeLevel({
    required this.label,
    required this.value,
    required this.color,
  });

  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Text(
          label.toUpperCase(),
          style: TextStyle(
            fontSize: 9,
            letterSpacing: .7,
            color: color.withValues(alpha: .88),
            fontWeight: FontWeight.w800,
          ),
        ),
        const SizedBox(height: 5),
        Text(
          value,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w900),
        ),
      ],
    );
  }
}

class _LevelDivider extends StatelessWidget {
  const _LevelDivider({required this.color});

  final Color color;

  @override
  Widget build(BuildContext context) => Container(
    width: 1,
    height: 32,
    margin: const EdgeInsets.symmetric(horizontal: 3),
    color: color,
  );
}

class _SummaryPill extends StatelessWidget {
  const _SummaryPill({
    required this.label,
    required this.value,
    required this.color,
  });

  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
    decoration: BoxDecoration(
      color: color.withValues(alpha: .11),
      borderRadius: BorderRadius.circular(999),
      border: Border.all(color: color.withValues(alpha: .26)),
    ),
    child: Text(
      '$label  $value',
      style: TextStyle(color: color, fontSize: 11, fontWeight: FontWeight.w800),
    ),
  );
}

class _IndicatorSummary extends StatelessWidget {
  const _IndicatorSummary({required this.read});

  final _Reader read;

  @override
  Widget build(BuildContext context) {
    final rsi = read(['rsi14'], fallback: read(['indicators', 'rsi']));
    final volume = read([
      'volume_context',
      'relative_ratio',
    ], fallback: read(['indicators', 'volume_ratio']));
    final trend = read(['trend'], fallback: read(['indicators', 'trend']));
    final strength = read(['trend_strength']);
    final support = read([
      'levels',
      'support',
    ], fallback: read(['indicators', 'support']));
    final resistance = read([
      'levels',
      'resistance',
    ], fallback: read(['indicators', 'resistance']));

    return Container(
      padding: const EdgeInsets.all(15),
      decoration: BoxDecoration(
        color: OryntraPalette.navy,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: OryntraPalette.rule),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Market indicators',
            style: TextStyle(fontWeight: FontWeight.w900),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: _IndicatorPill(
                  label: 'RSI 14',
                  value: rsi,
                  color: OryntraPalette.blue,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _IndicatorPill(
                  label: 'Volume',
                  value: volume == '—' ? '—' : '$volume×',
                  color: OryntraPalette.blueBright,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: _IndicatorPill(
                  label: 'Trend',
                  value: trend,
                  color: OryntraPalette.green,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _IndicatorPill(
                  label: 'Strength',
                  value: strength,
                  color: OryntraPalette.blueBright,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: _IndicatorPill(
                  label: 'Support',
                  value: support == '—' ? '—' : '\$$support',
                  color: OryntraPalette.blue,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _IndicatorPill(
                  label: 'Resistance',
                  value: resistance == '—' ? '—' : '\$$resistance',
                  color: OryntraPalette.danger,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _IndicatorPill extends StatelessWidget {
  const _IndicatorPill({
    required this.label,
    required this.value,
    required this.color,
  });

  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 10),
      decoration: BoxDecoration(
        color: color.withValues(alpha: .08),
        borderRadius: BorderRadius.circular(13),
        border: Border.all(color: color.withValues(alpha: .18)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: TextStyle(fontSize: 10, color: color.withValues(alpha: .82)),
          ),
          const SizedBox(height: 3),
          Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontWeight: FontWeight.w900),
          ),
        ],
      ),
    );
  }
}

class _PatternCard extends StatelessWidget {
  const _PatternCard({required this.patterns});

  final List<Map<String, dynamic>> patterns;

  Color _biasColor(String bias) {
    final upper = bias.toUpperCase();
    if (upper.contains('BULL') || upper.contains('LONG')) {
      return OryntraPalette.green;
    }
    if (upper.contains('BEAR') || upper.contains('SHORT')) {
      return OryntraPalette.danger;
    }
    return OryntraPalette.blueBright;
  }

  String _confidence(Map<String, dynamic> pattern) {
    final raw =
        pattern['confidence'] ?? pattern['score'] ?? pattern['strength'];
    final value = raw is num
        ? raw.toDouble()
        : double.tryParse(raw?.toString() ?? '');
    if (value == null) return '—';
    final percentage = value <= 1 ? value * 100 : value;
    return '${percentage.round()}%';
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(15),
      decoration: BoxDecoration(
        color: OryntraPalette.navy,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: OryntraPalette.rule),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(
                Icons.hub_rounded,
                color: OryntraPalette.blueBright,
                size: 20,
              ),
              const SizedBox(width: 9),
              const Expanded(
                child: Text(
                  'Detected patterns',
                  style: TextStyle(fontWeight: FontWeight.w900),
                ),
              ),
              Text(
                '${patterns.length}',
                style: const TextStyle(
                  color: OryntraPalette.blueBright,
                  fontWeight: FontWeight.w900,
                ),
              ),
            ],
          ),
          const SizedBox(height: 11),
          if (patterns.isEmpty)
            const Text(
              'No qualifying patterns were detected in this scan.',
              style: TextStyle(fontSize: 12, color: OryntraPalette.muted),
            )
          else
            ...patterns.take(5).map((pattern) {
              final name =
                  (pattern['name'] ??
                          pattern['pattern_name'] ??
                          pattern['pattern'] ??
                          pattern['type'] ??
                          'Pattern')
                      .toString()
                      .replaceAll('_', ' ');
              final bias =
                  (pattern['bias'] ??
                          pattern['direction'] ??
                          pattern['signal'] ??
                          'NEUTRAL')
                      .toString();
              final color = _biasColor(bias);
              return Padding(
                padding: const EdgeInsets.only(bottom: 9),
                child: Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      colors: [
                        color.withValues(alpha: .12),
                        OryntraPalette.panelRaised.withValues(alpha: .35),
                      ],
                    ),
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: color.withValues(alpha: .22)),
                  ),
                  child: Row(
                    children: [
                      Container(
                        width: 38,
                        height: 38,
                        decoration: BoxDecoration(
                          color: color.withValues(alpha: .15),
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Icon(
                          Icons.auto_graph_rounded,
                          size: 20,
                          color: color,
                        ),
                      ),
                      const SizedBox(width: 11),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              name,
                              style: const TextStyle(
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                            Text(
                              bias,
                              style: TextStyle(
                                fontSize: 10,
                                color: color,
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                          ],
                        ),
                      ),
                      Text(
                        _confidence(pattern),
                        style: TextStyle(
                          color: color,
                          fontWeight: FontWeight.w900,
                        ),
                      ),
                    ],
                  ),
                ),
              );
            }),
        ],
      ),
    );
  }
}

class _ScannerFeature extends StatelessWidget {
  const _ScannerFeature({
    required this.icon,
    required this.title,
    required this.body,
  });

  final IconData icon;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 13),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 38,
            height: 38,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: Theme.of(
                context,
              ).colorScheme.primary.withValues(alpha: .14),
            ),
            child: Icon(
              icon,
              size: 20,
              color: Theme.of(context).colorScheme.primary,
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(fontWeight: FontWeight.w800),
                ),
                const SizedBox(height: 2),
                Text(
                  body,
                  style: const TextStyle(
                    fontSize: 13,
                    color: OryntraPalette.muted,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
