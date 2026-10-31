import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../widgets/common.dart';
import '../widgets/glass.dart';

class PaperScreen extends StatefulWidget {
  const PaperScreen({
    super.key,
    required this.api,
    required this.signedIn,
    required this.onCreateAccount,
  });

  final ApiService api;
  final bool signedIn;
  final VoidCallback onCreateAccount;

  @override
  State<PaperScreen> createState() => PaperScreenState();
}

class PaperScreenState extends State<PaperScreen> {
  List<dynamic> _trades = const [];
  bool _loading = false;
  String? _error;
  int _loadGeneration = 0;

  @override
  void initState() {
    super.initState();
    if (widget.signedIn) _load();
  }

  @override
  void didUpdateWidget(covariant PaperScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!oldWidget.signedIn && widget.signedIn) {
      _load();
    } else if (oldWidget.signedIn && !widget.signedIn) {
      setState(() => _trades = const []);
    }
  }

  Future<void> _load() async {
    final generation = ++_loadGeneration;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final trades = await widget.api.paperTrades();
      if (!mounted || generation != _loadGeneration) return;
      setState(() => _trades = trades);
    } catch (_) {
      if (!mounted || generation != _loadGeneration) return;
      if (_trades.isEmpty) {
        setState(() {
          _error =
              'Paper trades could not be refreshed. Pull down to try again.';
        });
      }
    } finally {
      if (mounted && generation == _loadGeneration) {
        setState(() => _loading = false);
      }
    }
  }

  Future<void> refresh() => _load();

  double? _number(dynamic value) {
    if (value is num) return value.toDouble();
    if (value == null) return null;
    final cleaned = value
        .toString()
        .replaceAll(r'$', '')
        .replaceAll(',', '')
        .replaceAll('%', '')
        .trim();
    return double.tryParse(cleaned);
  }

  String _price(dynamic value) {
    final number = _number(value);
    if (number == null) return value?.toString() ?? '—';
    return '\$${number.toStringAsFixed(2)}';
  }

  String _pnl(dynamic value) {
    final number = _number(value);
    if (number == null) return value?.toString() ?? '—';
    if (number > 0) return '+\$${number.toStringAsFixed(2)}';
    if (number < 0) return '-\$${number.abs().toStringAsFixed(2)}';
    return '\$0.00';
  }

  String _successLabel(Map<String, dynamic> trade, dynamic pnl) {
    final supplied = trade['success_label']?.toString().trim();
    if (supplied != null && supplied.isNotEmpty) return supplied.toUpperCase();
    final status = trade['status']?.toString().toUpperCase() ?? '';
    if (status != 'CLOSED') return 'IN PROGRESS';
    final number = _number(pnl);
    if (number == null) return 'UNKNOWN';
    return number > 0 ? 'YES' : 'NO';
  }

  Color _successColor(BuildContext context, String success) {
    switch (success) {
      case 'YES':
        return Colors.greenAccent;
      case 'NO':
        return Theme.of(context).colorScheme.error;
      default:
        return Theme.of(context).colorScheme.primary;
    }
  }

  Future<void> _openNewTrade() async {
    final opened = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _PaperTradeComposer(api: widget.api),
    );
    if (opened != true || !mounted) return;
    await _load();
    if (mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('Simulated trade opened.')));
    }
  }

  Future<void> _manageTrade(Map<String, dynamic> trade) async {
    final action = await showModalBottomSheet<_PaperTradeAction>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _PaperTradeDetailSheet(trade: trade),
    );
    if (action == null || !mounted) return;
    final tradeId = _number(trade['id'])?.round();
    if (tradeId == null) return;
    try {
      if (action.delete) {
        final confirmed = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('Delete simulated trade?'),
            content: const Text(
              'This removes the paper-trade record from your account. This cannot be undone.',
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
        await widget.api.deletePaperTrade(tradeId);
      } else if (action.closePrice != null) {
        await widget.api.closePaperTrade(
          tradeId: tradeId,
          closePrice: action.closePrice!,
        );
      }
      await _load();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              action.delete
                  ? 'Simulated trade deleted.'
                  : 'Simulated trade closed.',
            ),
          ),
        );
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'Could not update this simulated trade. Please try again.',
            ),
          ),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!widget.signedIn) {
      return AccountRequiredState(
        feature: 'Paper Trades',
        onCreateAccount: widget.onCreateAccount,
      );
    }
    if (_loading && _trades.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    final colors = OryntraColors.of(context);
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.only(bottom: 130),
        children: [
          InstitutionalSectionLabel(
            label: 'Paper portfolio',
            trailing: Text(
              '${_trades.length} POSITIONS',
              style: const TextStyle(
                fontSize: 10,
                letterSpacing: .8,
                fontWeight: FontWeight.w800,
                color: Color(0xFF9CB0C7),
              ),
            ),
          ),
          AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(
                      Icons.science_outlined,
                      color: Theme.of(context).colorScheme.primary,
                    ),
                    const SizedBox(width: 12),
                    const Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'Simulated paper trades',
                            style: TextStyle(fontWeight: FontWeight.w800),
                          ),
                          SizedBox(height: 4),
                          Text(
                            'Plan entries, stops, targets, and risk. Oryntra never connects to a brokerage or places orders.',
                            style: TextStyle(
                              fontSize: 12,
                              color: Color(0xFF9CB0C7),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                FilledButton.icon(
                  onPressed: _openNewTrade,
                  icon: const Icon(Icons.add_chart_rounded),
                  label: const Text('Open simulated trade'),
                ),
              ],
            ),
          ),
          if (_error != null)
            AppCard(
              child: Row(
                children: [
                  Icon(Icons.info_outline, color: colors.muted),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(_error!, style: TextStyle(color: colors.muted)),
                  ),
                ],
              ),
            ),
          if (_trades.isEmpty)
            const EmptyState(
              icon: Icons.receipt_long_outlined,
              title: 'No paper trades',
              body:
                  'Create a simulation here, or add a scanner setup to your paper portfolio.',
            )
          else
            ..._trades.map((item) {
              final trade = Map<String, dynamic>.from(item as Map);
              final status = trade['status']?.toString().toUpperCase() ?? '—';
              final pnl = trade['current_pnl'] ?? trade['pnl'];
              final success = _successLabel(trade, pnl);
              final successColor = _successColor(context, success);
              return AppCard(
                padding: EdgeInsets.zero,
                child: InkWell(
                  borderRadius: BorderRadius.circular(20),
                  onTap: () => _manageTrade(trade),
                  child: Padding(
                    padding: const EdgeInsets.all(18),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Expanded(
                              child: Text(
                                trade['ticker']?.toString() ?? '—',
                                style: Theme.of(context).textTheme.titleLarge,
                              ),
                            ),
                            Chip(label: Text(status)),
                          ],
                        ),
                        Text(
                          '${trade['direction'] ?? '—'} • ${trade['size'] ?? '—'} simulated shares',
                          style: const TextStyle(fontWeight: FontWeight.w600),
                        ),
                        const SizedBox(height: 12),
                        _TradeMetric('Entry', _price(trade['entry_price'])),
                        _TradeMetric('Current', _price(trade['current_price'])),
                        if (status == 'CLOSED' && trade['close_price'] != null)
                          _TradeMetric('Exit', _price(trade['close_price'])),
                        _TradeMetric('P&L', _pnl(pnl)),
                        _TradeMetric(
                          'Success',
                          success,
                          valueColor: successColor,
                        ),
                        const SizedBox(height: 7),
                        Text(
                          status == 'OPEN'
                              ? 'Tap to review the plan, close, or delete this simulation.'
                              : 'Tap to review or delete this completed simulation.',
                          style: TextStyle(fontSize: 11, color: colors.muted),
                        ),
                      ],
                    ),
                  ),
                ),
              );
            }),
        ],
      ),
    );
  }
}

class _PaperTradeAction {
  const _PaperTradeAction.close(this.closePrice) : delete = false;
  const _PaperTradeAction.delete() : closePrice = null, delete = true;

  final double? closePrice;
  final bool delete;
}

class _PaperTradeComposer extends StatefulWidget {
  const _PaperTradeComposer({required this.api});
  final ApiService api;

  @override
  State<_PaperTradeComposer> createState() => _PaperTradeComposerState();
}

class _PaperTradeComposerState extends State<_PaperTradeComposer> {
  final _ticker = TextEditingController(text: 'AAPL');
  final _entry = TextEditingController();
  final _stop = TextEditingController();
  final _target = TextEditingController();
  final _shares = TextEditingController(text: '1');
  final _notes = TextEditingController();
  String _direction = 'LONG';
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    for (final controller in [_entry, _stop, _target, _shares]) {
      controller.addListener(_refresh);
    }
  }

  @override
  void dispose() {
    _ticker.dispose();
    _entry.dispose();
    _stop.dispose();
    _target.dispose();
    _shares.dispose();
    _notes.dispose();
    super.dispose();
  }

  void _refresh() {
    if (mounted) setState(() {});
  }

  double? _read(TextEditingController controller) =>
      double.tryParse(controller.text.trim());

  Future<void> _save() async {
    final ticker = _ticker.text.trim().toUpperCase();
    final entry = _read(_entry);
    final stop = _read(_stop);
    final target = _read(_target);
    final shares = _read(_shares);
    final validOrder = _direction == 'LONG'
        ? stop != null &&
              entry != null &&
              target != null &&
              stop < entry &&
              entry < target
        : stop != null &&
              entry != null &&
              target != null &&
              target < entry &&
              entry < stop;
    if (ticker.isEmpty ||
        entry == null ||
        stop == null ||
        target == null ||
        shares == null ||
        shares <= 0 ||
        !validOrder) {
      setState(() {
        _error = _direction == 'LONG'
            ? 'For a long simulation, use stop < entry < target.'
            : 'For a short simulation, use target < entry < stop.';
      });
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await widget.api.openPaperTrade(
        ticker: ticker,
        direction: _direction,
        entryPrice: entry,
        stopPrice: stop,
        targetPrice: target,
        size: shares,
        notes: _notes.text.trim(),
        setupType: 'Manual paper-trade plan',
      );
      if (mounted) Navigator.pop(context, true);
    } catch (_) {
      if (mounted) {
        setState(
          () =>
              _error = 'Could not save this simulated trade. Please try again.',
        );
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final entry = _read(_entry);
    final stop = _read(_stop);
    final target = _read(_target);
    final shares = _read(_shares);
    final risk = entry == null || stop == null || shares == null
        ? null
        : (entry - stop).abs() * shares;
    final reward = entry == null || target == null || shares == null
        ? null
        : (target - entry).abs() * shares;
    final ratio = risk == null || risk == 0 || reward == null
        ? null
        : reward / risk;
    return SafeArea(
      child: Material(
        color: Theme.of(context).colorScheme.surface,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
        child: DraggableScrollableSheet(
          expand: false,
          initialChildSize: .88,
          minChildSize: .55,
          maxChildSize: .96,
          builder: (context, scrollController) => ListView(
            controller: scrollController,
            padding: const EdgeInsets.fromLTRB(20, 13, 20, 28),
            children: [
              Center(
                child: Container(
                  width: 42,
                  height: 4,
                  decoration: BoxDecoration(
                    color: Theme.of(context).dividerColor,
                    borderRadius: BorderRadius.circular(99),
                  ),
                ),
              ),
              const SizedBox(height: 18),
              Text(
                'Open simulated trade',
                style: Theme.of(context).textTheme.headlineSmall,
              ),
              const SizedBox(height: 5),
              const Text(
                'Build a private trade plan with entry, invalidation, target, and position risk. No order can be sent from Oryntra.',
                style: TextStyle(fontSize: 12),
              ),
              const SizedBox(height: 18),
              TextField(
                controller: _ticker,
                textCapitalization: TextCapitalization.characters,
                autocorrect: false,
                decoration: const InputDecoration(labelText: 'Ticker symbol'),
              ),
              const SizedBox(height: 14),
              Text('Direction', style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                children: ['LONG', 'SHORT']
                    .map(
                      (direction) => ChoiceChip(
                        label: Text(direction),
                        selected: _direction == direction,
                        onSelected: _saving
                            ? null
                            : (_) => setState(() => _direction = direction),
                      ),
                    )
                    .toList(),
              ),
              const SizedBox(height: 16),
              _priceField(_entry, 'Planned entry'),
              const SizedBox(height: 11),
              _priceField(_stop, 'Stop / invalidation'),
              const SizedBox(height: 11),
              _priceField(_target, 'Target'),
              const SizedBox(height: 11),
              _priceField(_shares, 'Simulated shares'),
              const SizedBox(height: 16),
              _PlanSummary(
                risk: risk,
                reward: reward,
                ratio: ratio,
                entry: entry,
                shares: shares,
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _notes,
                maxLines: 3,
                maxLength: 240,
                decoration: const InputDecoration(
                  labelText: 'Research notes (optional)',
                ),
              ),
              if (_error != null) ...[
                const SizedBox(height: 8),
                Text(
                  _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ],
              const SizedBox(height: 12),
              FilledButton.icon(
                onPressed: _saving ? null : _save,
                icon: _saving
                    ? const SizedBox.square(
                        dimension: 17,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.add_chart_rounded),
                label: Text(
                  _saving ? 'Saving simulation…' : 'Open simulated trade',
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _priceField(TextEditingController controller, String label) =>
      TextField(
        controller: controller,
        keyboardType: const TextInputType.numberWithOptions(decimal: true),
        decoration: InputDecoration(labelText: label),
      );
}

class _PaperTradeDetailSheet extends StatefulWidget {
  const _PaperTradeDetailSheet({required this.trade});
  final Map<String, dynamic> trade;

  @override
  State<_PaperTradeDetailSheet> createState() => _PaperTradeDetailSheetState();
}

class _PaperTradeDetailSheetState extends State<_PaperTradeDetailSheet> {
  final _closePrice = TextEditingController();
  String? _error;

  @override
  void dispose() {
    _closePrice.dispose();
    super.dispose();
  }

  double? _number(dynamic value) => value is num
      ? value.toDouble()
      : double.tryParse(value?.toString() ?? '');
  String _price(dynamic value) {
    final number = _number(value);
    return number == null ? '—' : '\$${number.toStringAsFixed(2)}';
  }

  void _close() {
    final price = _number(_closePrice.text);
    if (price == null || price <= 0) {
      setState(() => _error = 'Enter a valid simulated exit price.');
      return;
    }
    Navigator.pop(context, _PaperTradeAction.close(price));
  }

  @override
  Widget build(BuildContext context) {
    final trade = widget.trade;
    final status = trade['status']?.toString().toUpperCase() ?? 'OPEN';
    final entry = _number(trade['entry_price']);
    final stop = _number(trade['stop_price']);
    final target = _number(trade['target_price']);
    final size = _number(trade['size']);
    final risk = entry == null || stop == null || size == null
        ? null
        : (entry - stop).abs() * size;
    final reward = entry == null || target == null || size == null
        ? null
        : (target - entry).abs() * size;
    return SafeArea(
      child: Material(
        color: Theme.of(context).colorScheme.surface,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
        child: DraggableScrollableSheet(
          expand: false,
          initialChildSize: .76,
          minChildSize: .48,
          maxChildSize: .94,
          builder: (context, scrollController) => ListView(
            controller: scrollController,
            padding: const EdgeInsets.fromLTRB(20, 13, 20, 28),
            children: [
              Center(
                child: Container(
                  width: 42,
                  height: 4,
                  decoration: BoxDecoration(
                    color: Theme.of(context).dividerColor,
                    borderRadius: BorderRadius.circular(99),
                  ),
                ),
              ),
              const SizedBox(height: 18),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      trade['ticker']?.toString() ?? 'Paper trade',
                      style: Theme.of(context).textTheme.headlineSmall,
                    ),
                  ),
                  Chip(label: Text(status)),
                ],
              ),
              Text(
                '${trade['direction'] ?? '—'} · ${trade['size'] ?? '—'} simulated shares',
              ),
              const SizedBox(height: 18),
              _DetailRow('Entry', _price(trade['entry_price'])),
              _DetailRow('Stop / invalidation', _price(trade['stop_price'])),
              _DetailRow('Target', _price(trade['target_price'])),
              _DetailRow(
                'Current / exit',
                _price(trade['current_price'] ?? trade['close_price']),
              ),
              _DetailRow(
                'Planned risk',
                risk == null ? '—' : '\$${risk.toStringAsFixed(2)}',
              ),
              _DetailRow(
                'Planned reward',
                reward == null ? '—' : '\$${reward.toStringAsFixed(2)}',
              ),
              if ((trade['notes']?.toString().trim() ?? '').isNotEmpty) ...[
                const SizedBox(height: 16),
                Text(
                  'Research notes',
                  style: Theme.of(context).textTheme.titleSmall,
                ),
                const SizedBox(height: 5),
                Text(trade['notes'].toString()),
              ],
              if (status == 'OPEN') ...[
                const SizedBox(height: 20),
                TextField(
                  controller: _closePrice,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(
                    labelText: 'Simulated exit price',
                  ),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 7),
                  Text(
                    _error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                ],
                const SizedBox(height: 12),
                FilledButton.icon(
                  onPressed: _close,
                  icon: const Icon(Icons.check_circle_outline),
                  label: const Text('Close simulated trade'),
                ),
              ],
              const SizedBox(height: 10),
              TextButton.icon(
                onPressed: () =>
                    Navigator.pop(context, const _PaperTradeAction.delete()),
                icon: Icon(
                  Icons.delete_outline,
                  color: Theme.of(context).colorScheme.error,
                ),
                label: Text(
                  'Delete trade',
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _PlanSummary extends StatelessWidget {
  const _PlanSummary({
    required this.risk,
    required this.reward,
    required this.ratio,
    required this.entry,
    required this.shares,
  });
  final double? risk;
  final double? reward;
  final double? ratio;
  final double? entry;
  final double? shares;

  String _money(double? value) =>
      value == null ? '—' : '\$${value.toStringAsFixed(2)}';

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.all(14),
    decoration: BoxDecoration(
      color: Theme.of(context).colorScheme.primary.withValues(alpha: .08),
      borderRadius: BorderRadius.circular(16),
    ),
    child: Wrap(
      spacing: 20,
      runSpacing: 12,
      children: [
        _PlanValue(
          'Notional',
          _money(entry == null || shares == null ? null : entry! * shares!),
        ),
        _PlanValue('Planned risk', _money(risk)),
        _PlanValue('Planned reward', _money(reward)),
        _PlanValue(
          'Reward / risk',
          ratio == null ? '—' : '${ratio!.toStringAsFixed(2)}×',
        ),
      ],
    ),
  );
}

class _PlanValue extends StatelessWidget {
  const _PlanValue(this.label, this.value);
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => SizedBox(
    width: 122,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(fontSize: 10, fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 3),
        Text(value, style: const TextStyle(fontWeight: FontWeight.w900)),
      ],
    ),
  );
}

class _DetailRow extends StatelessWidget {
  const _DetailRow(this.label, this.value);
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 5),
    child: Row(
      children: [
        SizedBox(
          width: 142,
          child: Text(
            label,
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
        ),
        Expanded(child: Text(value)),
      ],
    ),
  );
}

class _TradeMetric extends StatelessWidget {
  const _TradeMetric(this.label, this.value, {this.valueColor});

  final String label;
  final String value;
  final Color? valueColor;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          SizedBox(
            width: 112,
            child: Text(label, style: const TextStyle(color: Colors.white70)),
          ),
          Expanded(
            child: Text(
              value,
              style: TextStyle(color: valueColor, fontWeight: FontWeight.w700),
            ),
          ),
        ],
      ),
    );
  }
}
