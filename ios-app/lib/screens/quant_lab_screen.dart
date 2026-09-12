import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../services/quant_lab_store.dart';
import '../widgets/common.dart';
import '../widgets/glass.dart';

class QuantLabScreen extends StatefulWidget {
  const QuantLabScreen({super.key, required this.api});

  final ApiService api;

  @override
  State<QuantLabScreen> createState() => QuantLabScreenState();
}

class QuantLabScreenState extends State<QuantLabScreen> {
  final _tickers = TextEditingController(text: 'SPY, QQQ, IWM, TLT');
  bool _running = false;
  String _progress = '';
  String? _error;
  Map<String, dynamic>? _report;
  final _savedResults = QuantLabStore();

  @override
  void dispose() {
    _tickers.dispose();
    super.dispose();
  }

  void showStoredReport(Map<String, dynamic> report) {
    setState(() {
      _report = report;
      _error = null;
      _progress = 'Loaded a report saved on this device.';
    });
  }

  Future<void> _run() async {
    final symbols = _tickers.text.split(',');
    setState(() {
      _running = true;
      _error = null;
      _report = null;
      _progress =
          'Feel free to take a break and switch apps, this scan may take a minute.';
    });
    try {
      final report = await widget.api.runQuantResearch(
        tickers: symbols,
        onProgress: (message) async {
          if (mounted) setState(() => _progress = message);
        },
      );
      if (mounted) {
        setState(() => _report = report);
      }
      try {
        await _savedResults.save(report: report, tickers: symbols);
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text('Quant Lab result saved on this device.'),
            ),
          );
        }
      } catch (_) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text('Report finished, but could not be saved locally.'),
            ),
          );
        }
      }
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      if (mounted) {
        setState(() {
          _running = false;
          _progress = '';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = OryntraColors.of(context);
    return ListView(
      padding: const EdgeInsets.fromLTRB(0, 8, 0, 130),
      children: [
        const InstitutionalSectionLabel(label: 'Systematic research desk'),
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 3, 20, 10),
          child: Text(
            'Quant Lab',
            style: Theme.of(context).textTheme.headlineSmall,
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Text(
            'Run the published frozen historical research profile. It does not build your portfolio, execute orders, or use personal financial information.',
            style: TextStyle(color: colors.muted, height: 1.45),
          ),
        ),
        const SizedBox(height: 10),
        if (_report != null) _QuantReport(report: _report!),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'Published research profile',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _tickers,
                textCapitalization: TextCapitalization.characters,
                autocorrect: false,
                decoration: const InputDecoration(
                  labelText: 'Universe',
                  hintText: 'SPY, QQQ, IWM, TLT',
                ),
              ),
              const SizedBox(height: 12),
              const SizedBox(height: 10),
              const Text('Fixed profile: V1.0 corporate quant system · weekly rebalancing · long-only · fixed implementation assumptions. You may choose a research universe, but cannot tune this public model.'),
            ],
          ),
        ),
        const InstitutionalSectionLabel(label: 'Frozen methodology'),
        AppCard(
          child: Column(
            children: [
              const Text('The public profile uses fixed, documented sleeves and fixed exposure constraints. Internal research controls are not available in the mobile app.'),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 10, 20, 0),
          child: Text(
            'Mobile runs request daily bars directly from your saved provider key. The default four-symbol universe fits Polygon / Massive Basic’s five-calls-per-minute allowance.',
            style: TextStyle(fontSize: 11, color: colors.muted),
          ),
        ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 14, 20, 0),
            child: Text(
              _error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
        if (_running)
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 14, 20, 0),
            child: Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: colors.blueBright.withValues(alpha: .09),
                borderRadius: BorderRadius.circular(14),
                border: Border.all(
                  color: colors.blueBright.withValues(alpha: .28),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const LinearProgressIndicator(),
                  const SizedBox(height: 11),
                  const Text(
                    'Feel free to take a break and switch apps, this scan may take a minute.',
                    style: TextStyle(fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 5),
                  Text(
                    _progress,
                    style: TextStyle(fontSize: 11, color: colors.muted),
                  ),
                ],
              ),
            ),
          ),
        Padding(
          padding: const EdgeInsets.all(20),
          child: FilledButton.icon(
            onPressed: _running ? null : _run,
            icon: _running
                ? const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.science_outlined),
            label: Text(
              _running
                  ? 'Building research report…'
                  : 'Generate research report',
            ),
          ),
        ),
      ],
    );
  }

}

class _QuantReport extends StatelessWidget {
  const _QuantReport({required this.report});
  final Map<String, dynamic> report;

  String _number(dynamic value, {String suffix = '', int digits = 1}) {
    final number = value is num
        ? value.toDouble()
        : double.tryParse(value?.toString() ?? '');
    return number == null
        ? '—'
        : '${number >= 0 ? '+' : ''}${number.toStringAsFixed(digits)}$suffix';
  }

  @override
  Widget build(BuildContext context) {
    final risk = report['portfolio_risk'] is Map
        ? Map<String, dynamic>.from(report['portfolio_risk'])
        : <String, dynamic>{};
    final validation = report['validation'] is Map
        ? Map<String, dynamic>.from(report['validation'])
        : <String, dynamic>{};
    final holdout = validation['holdout'] is Map
        ? Map<String, dynamic>.from(validation['holdout'])
        : <String, dynamic>{};
    final regimes = report['regime_breakdown'] is List
        ? List<dynamic>.from(report['regime_breakdown'])
        : const [];
    final health = report['strategy_health'] is List
        ? List<dynamic>.from(report['strategy_health'])
        : const [];
    final ledger = report['assumption_ledger'] is Map
        ? Map<String, dynamic>.from(report['assumption_ledger'])
        : <String, dynamic>{};
    return Column(
      children: [
        const InstitutionalSectionLabel(label: 'Research report'),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'Dataset fingerprint',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 4),
              SelectableText(
                (report['dataset_fingerprint']?.toString() ?? '—').substring(
                  0,
                  (report['dataset_fingerprint']?.toString().length ?? 0).clamp(
                    0,
                    28,
                  ),
                ),
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
              ),
              const SizedBox(height: 14),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  _metric(
                    'Holdout return',
                    _number(holdout['total_return_pct'], suffix: '%'),
                  ),
                  _metric(
                    'Holdout drawdown',
                    _number(holdout['max_drawdown_pct'], suffix: '%'),
                  ),
                  _metric(
                    'Gross exposure',
                    _number(
                      risk['latest_gross_exposure'],
                      suffix: '×',
                      digits: 2,
                    ),
                  ),
                  _metric(
                    'Effective positions',
                    _number(risk['effective_number_of_positions'], digits: 2),
                  ),
                ],
              ),
            ],
          ),
        ),
        if (regimes.isNotEmpty) ...[
          const InstitutionalSectionLabel(label: 'Regime report'),
          AppCard(
            child: Column(
              children: regimes
                  .whereType<Map>()
                  .map(
                    (item) => ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text(
                        item['regime']?.toString() ?? 'Historical state',
                      ),
                      subtitle: Text(
                        '${item['sessions'] ?? '—'} sessions · vol ${_number(item['annualized_volatility_pct'], suffix: '%')}',
                      ),
                      trailing: Text(
                        _number(item['total_return_pct'], suffix: '%'),
                        style: const TextStyle(fontWeight: FontWeight.w900),
                      ),
                    ),
                  )
                  .toList(),
            ),
          ),
        ],
        if (health.isNotEmpty) ...[
          const InstitutionalSectionLabel(label: 'Strategy health'),
          AppCard(
            child: Column(
              children: health
                  .whereType<Map>()
                  .map(
                    (item) => ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text(item['strategy']?.toString() ?? 'Sleeve'),
                      subtitle: Text(
                        'Recent ${_number(item['recent_mean_daily_bps'], suffix: ' bps', digits: 2)} · decay ${_number(item['alpha_decay_daily_bps'], suffix: ' bps', digits: 2)}',
                      ),
                      trailing: Text(
                        item['status']?.toString() ?? '—',
                        style: const TextStyle(fontWeight: FontWeight.w900),
                      ),
                    ),
                  )
                  .toList(),
            ),
          ),
        ],
        if (ledger.isNotEmpty) ...[
          const InstitutionalSectionLabel(label: 'Assumption ledger'),
          AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'What this report assumes',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 8),
                ...['timing', 'portfolio', 'execution', 'evidence'].expand((
                  group,
                ) {
                  final rows = ledger[group] is List
                      ? List<dynamic>.from(ledger[group])
                      : const <dynamic>[];
                  return rows.whereType<Map>().map(
                    (item) => ListTile(
                      contentPadding: EdgeInsets.zero,
                      dense: true,
                      title: Text(item['label']?.toString() ?? 'Assumption'),
                      subtitle: Text(item['value']?.toString() ?? '—'),
                    ),
                  );
                }),
                if (ledger['omissions'] is List) ...[
                  const SizedBox(height: 8),
                  const Text(
                    'Material omissions',
                    style: TextStyle(fontWeight: FontWeight.w900),
                  ),
                  ...List<dynamic>.from(ledger['omissions']).map(
                    (item) => Padding(
                      padding: const EdgeInsets.only(top: 5),
                      child: Text('• ${item.toString()}'),
                    ),
                  ),
                ],
                if (ledger['note'] != null) ...[
                  const SizedBox(height: 10),
                  Text(
                    ledger['note'].toString(),
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
              ],
            ),
          ),
        ],
      ],
    );
  }

  Widget _metric(String label, String value) => Container(
    width: 145,
    padding: const EdgeInsets.all(12),
    decoration: BoxDecoration(
      color: Colors.black.withValues(alpha: .06),
      borderRadius: BorderRadius.circular(13),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(fontSize: 10, fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 4),
        Text(
          value,
          style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w900),
        ),
      ],
    ),
  );
}
