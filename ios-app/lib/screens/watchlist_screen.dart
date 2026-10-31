import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../widgets/common.dart';

typedef ScanTickerCallback = Future<void> Function(String ticker);

class WatchlistScreen extends StatefulWidget {
  const WatchlistScreen({
    super.key,
    required this.api,
    required this.onScanTicker,
    required this.signedIn,
    required this.onCreateAccount,
  });

  final ApiService api;
  final ScanTickerCallback onScanTicker;
  final bool signedIn;
  final VoidCallback onCreateAccount;

  @override
  State<WatchlistScreen> createState() => _WatchlistScreenState();
}

class _WatchlistScreenState extends State<WatchlistScreen> {
  final _ticker = TextEditingController();
  List<dynamic> _items = const [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    if (widget.signedIn) {
      _refresh();
    } else {
      _loading = false;
    }
  }

  @override
  void didUpdateWidget(covariant WatchlistScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!oldWidget.signedIn && widget.signedIn) {
      _refresh();
    } else if (oldWidget.signedIn && !widget.signedIn) {
      setState(() {
        _items = const [];
        _loading = false;
        _error = null;
      });
    }
  }

  Future<void> _refresh() async {
    if (!widget.signedIn) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final items = await widget.api.watchlist();
      if (mounted) setState(() => _items = items);
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _add() async {
    final ticker = _ticker.text.trim().toUpperCase();
    if (ticker.isEmpty) return;
    await widget.api.addWatchlist(ticker);
    _ticker.clear();
    await _refresh();
  }

  Future<void> _remove(Map<String, dynamic> map, dynamic item) async {
    final ticker = map['ticker']?.toString() ?? '';
    final removed = map;
    final index = _items.indexOf(item);
    setState(() => _items = List<dynamic>.from(_items)..remove(item));
    try {
      await widget.api.removeWatchlist(ticker);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        final restored = List<dynamic>.from(_items);
        final safeIndex = index < 0
            ? 0
            : (index > restored.length ? restored.length : index);
        restored.insert(safeIndex, removed);
        _items = restored;
      });
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(e.toString())));
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!widget.signedIn) {
      return AccountRequiredState(
        feature: 'Watchlist',
        onCreateAccount: widget.onCreateAccount,
      );
    }
    if (_loading) return const Center(child: CircularProgressIndicator());
    final primary = Theme.of(context).colorScheme.primary;
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        padding: const EdgeInsets.only(bottom: 130),
        children: [
          InstitutionalSectionLabel(
            label: 'Watchlist',
            trailing: Text(
              '${_items.length} SYMBOLS',
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
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _ticker,
                        textCapitalization: TextCapitalization.characters,
                        decoration: const InputDecoration(
                          labelText: 'Add ticker',
                        ),
                        onSubmitted: (_) => _add(),
                      ),
                    ),
                    const SizedBox(width: 10),
                    IconButton.filled(
                      onPressed: _add,
                      tooltip: 'Add ticker',
                      icon: const Icon(Icons.add),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                Row(
                  children: [
                    Icon(Icons.swipe_right_rounded, size: 18, color: primary),
                    const SizedBox(width: 7),
                    const Expanded(
                      child: Text(
                        'Swipe right to scan. Swipe left to remove.',
                        style: TextStyle(
                          fontSize: 12,
                          color: Color(0xFF9CB0C7),
                        ),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          if (_error != null) AppCard(child: Text(_error!)),
          if (_items.isEmpty)
            const EmptyState(
              icon: Icons.bookmark_border,
              title: 'No watchlist tickers',
              body: 'Add a symbol above to keep it handy.',
            )
          else
            ..._items.map((item) {
              final map = Map<String, dynamic>.from(item as Map);
              final ticker = map['ticker']?.toString() ?? '';
              final notes = map['notes']?.toString().trim() ?? '';
              return Dismissible(
                key: ValueKey(ticker),
                direction: DismissDirection.horizontal,
                background: Container(
                  margin: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 4,
                  ),
                  padding: const EdgeInsets.only(left: 22),
                  alignment: Alignment.centerLeft,
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(18),
                    color: primary.withValues(alpha: .85),
                  ),
                  child: const Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.analytics_outlined, color: Colors.black87),
                      SizedBox(width: 8),
                      Text(
                        'SCAN',
                        style: TextStyle(
                          color: Colors.black87,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                    ],
                  ),
                ),
                secondaryBackground: Container(
                  margin: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 4,
                  ),
                  alignment: Alignment.centerRight,
                  padding: const EdgeInsets.only(right: 22),
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(18),
                    color: Theme.of(context).colorScheme.error,
                  ),
                  child: const Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        'REMOVE',
                        style: TextStyle(
                          color: Colors.white,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      SizedBox(width: 8),
                      Icon(Icons.delete_outline, color: Colors.white),
                    ],
                  ),
                ),
                confirmDismiss: (direction) async {
                  if (direction == DismissDirection.startToEnd) {
                    await widget.onScanTicker(ticker);
                    return false;
                  }
                  return true;
                },
                onDismissed: (_) => _remove(map, item),
                child: ListTile(
                  contentPadding: const EdgeInsets.only(left: 24, right: 12),
                  onTap: () async => widget.onScanTicker(ticker),
                  leading: CircleAvatar(
                    backgroundColor: primary.withValues(alpha: .24),
                    child: Icon(Icons.show_chart, color: primary),
                  ),
                  title: Text(ticker),
                  subtitle: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(notes.isNotEmpty ? notes : 'Saved ticker'),
                      const SizedBox(height: 3),
                      Text(
                        'Swipe right or tap the arrow to scan',
                        style: TextStyle(
                          fontSize: 11,
                          color: primary.withValues(alpha: .92),
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ],
                  ),
                  trailing: Semantics(
                    button: true,
                    label: 'Scan $ticker',
                    child: IconButton(
                      tooltip: 'Scan $ticker',
                      onPressed: () async => widget.onScanTicker(ticker),
                      icon: Icon(Icons.arrow_forward_rounded, color: primary),
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
