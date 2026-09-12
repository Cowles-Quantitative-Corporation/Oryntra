import 'dart:convert';
import 'dart:math' as math;
import 'package:http/http.dart' as http;
import '../app_config.dart';
import 'background_task_service.dart';
import 'provider_key_store.dart';
import 'session_store.dart';

class ApiException implements Exception {
  ApiException(this.message, {this.statusCode, this.code});
  final String message;
  final int? statusCode;
  final String? code;

  @override
  String toString() => message;
}

class ApiService {
  ApiService({SessionStore? sessionStore, ProviderKeyStore? providerKeyStore})
    : _sessionStore = sessionStore ?? SessionStore(),
      _providerKeyStore = providerKeyStore ?? ProviderKeyStore();

  final SessionStore _sessionStore;
  final ProviderKeyStore _providerKeyStore;
  final _backgroundTaskService = BackgroundTaskService();
  bool _previewSignedIn = false;
  int _previewScanCount = 20383;

  final List<Map<String, dynamic>> _previewWatchlist = [
    {'ticker': 'AAPL', 'notes': 'Quality momentum setup'},
    {'ticker': 'NVDA', 'notes': 'Watching resistance breakout'},
    {'ticker': 'MSFT', 'notes': 'Long-term trend remains constructive'},
  ];

  final List<Map<String, dynamic>> _previewPaperTrades = [
    {
      'id': 1,
      'ticker': 'AAPL',
      'status': 'OPEN',
      'direction': 'LONG',
      'size': 12,
      'entry_price': 221.40,
      'current_price': 228.16,
      'current_pnl': 81.12,
      'current_pnl_pct': 3.05,
      'success_label': 'IN PROGRESS',
    },
    {
      'id': 2,
      'ticker': 'NVDA',
      'status': 'OPEN',
      'direction': 'LONG',
      'size': 8,
      'entry_price': 171.25,
      'current_price': 176.92,
      'current_pnl': 45.36,
      'current_pnl_pct': 3.31,
      'success_label': 'IN PROGRESS',
    },
    {
      'id': 3,
      'ticker': 'TSLA',
      'status': 'CLOSED',
      'direction': 'SHORT',
      'size': 5,
      'entry_price': 334.10,
      'current_price': 329.45,
      'close_price': 326.80,
      'pnl': 36.50,
      'pnl_pct': 2.18,
      'success': true,
      'success_label': 'YES',
    },
  ];

  Uri _uri(String path) => Uri.parse('${AppConfig.apiBaseUrl}$path');
  Uri _authUri(String path) => Uri.parse('${AppConfig.authBaseUrl}$path');

  Future<void> _previewPause([int milliseconds = 280]) =>
      Future<void>.delayed(Duration(milliseconds: milliseconds));

  Future<Map<String, String>> _headers({bool jsonBody = false}) async {
    final token = await _sessionStore.readToken();
    return {
      'Accept': 'application/json',
      if (jsonBody) 'Content-Type': 'application/json',
      if (token != null && token.isNotEmpty) 'Authorization': 'Bearer $token',
    };
  }

  dynamic _decode(http.Response response) {
    dynamic data;
    try {
      data = jsonDecode(response.body);
    } catch (_) {
      data = response.body;
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      var message = 'Request failed (${response.statusCode}).';
      String? code;
      if (data is Map && data['detail'] != null) {
        final detail = data['detail'];
        if (detail is Map) {
          message = detail['message']?.toString() ?? detail.toString();
          code = detail['code']?.toString();
        } else {
          message = detail.toString();
        }
      }
      throw ApiException(message, statusCode: response.statusCode, code: code);
    }
    return data;
  }

  Map<String, dynamic> _previewUser({String? email, String? displayName}) => {
    'user': {
      'display_name': displayName?.isNotEmpty == true
          ? displayName
          : 'Oryntra Preview',
      'email': email?.isNotEmpty == true ? email : 'preview@oryntra.local',
    },
    'token': 'oryntra-preview-token',
  };

  Future<Map<String, dynamic>> signup(
    String email,
    String password,
    String displayName,
    bool acceptLegal,
  ) async {
    if (AppConfig.previewMode) {
      await _previewPause();
      _previewSignedIn = true;
      await _sessionStore.saveToken('oryntra-preview-token');
      return _previewUser(email: email, displayName: displayName);
    }
    final response = await http
        .post(
          _authUri('/api/auth/signup'),
          headers: await _headers(jsonBody: true),
          body: jsonEncode({
            'email': email,
            'password': password,
            'display_name': displayName,
            'accept_legal': acceptLegal,
          }),
        )
        .timeout(const Duration(seconds: 20));
    final data = Map<String, dynamic>.from(_decode(response) as Map);
    final token = data['token']?.toString();
    if (token != null && token.isNotEmpty) await _sessionStore.saveToken(token);
    return data;
  }

  Future<void> deleteAccount(String password) async {
    if (AppConfig.previewMode) {
      await _previewPause();
      _previewSignedIn = false;
      await _sessionStore.clear();
      return;
    }
    final response = await http
        .delete(
          _authUri('/api/auth/account'),
          headers: await _headers(jsonBody: true),
          body: jsonEncode({'password': password}),
        )
        .timeout(const Duration(seconds: 20));
    _decode(response);
    await _sessionStore.clear();
  }

  Future<Map<String, dynamic>> login(String email, String password) async {
    if (AppConfig.previewMode) {
      await _previewPause();
      _previewSignedIn = true;
      await _sessionStore.saveToken('oryntra-preview-token');
      return _previewUser(email: email);
    }
    final response = await http
        .post(
          _authUri('/api/auth/login'),
          headers: await _headers(jsonBody: true),
          body: jsonEncode({'email': email, 'password': password}),
        )
        .timeout(const Duration(seconds: 20));
    final data = Map<String, dynamic>.from(_decode(response) as Map);
    final token = data['token']?.toString();
    if (token != null && token.isNotEmpty) await _sessionStore.saveToken(token);
    return data;
  }

  Future<Map<String, dynamic>?> me() async {
    if (AppConfig.previewMode) {
      await _previewPause(120);
      return _previewSignedIn ? _previewUser() : null;
    }
    final response = await http
        .get(_authUri('/api/auth/me'), headers: await _headers())
        .timeout(const Duration(seconds: 15));
    if (response.statusCode == 401) return null;
    return Map<String, dynamic>.from(_decode(response) as Map);
  }

  Future<void> logout() async {
    if (AppConfig.previewMode) {
      await _previewPause(120);
      _previewSignedIn = false;
      await _sessionStore.clear();
      return;
    }
    try {
      await http.post(_authUri('/api/auth/logout'), headers: await _headers());
    } finally {
      await _sessionStore.clear();
    }
  }

  Future<Map<String, dynamic>> intelligenceStatus() async {
    if (AppConfig.previewMode) {
      await _previewPause(160);
      return {
        'service': 'oryntra_market_intelligence',
        'status': 'ready',
        'policy': {
          'analysis_permitted': true,
          'owner_access': true,
          'license_mode': 'preview',
          'public_derived_analysis_enabled': false,
          'daily_limit': 100,
          'market_history_included': false,
          'chart_provider': 'TradingView',
        },
        'quota': {'used': 12, 'limit': 100, 'remaining': 88},
        'chart_provider': 'TradingView',
      };
    }
    final response = await http
        .get(_uri('/api/intelligence/status'), headers: await _headers())
        .timeout(const Duration(seconds: 20));
    return Map<String, dynamic>.from(_decode(response) as Map);
  }

  List<Map<String, dynamic>> _previewPatterns(String ticker, int quality) {
    final names = [
      ['Ascending channel', 'BULLISH'],
      ['Descending channel', 'BEARISH'],
      ['Bull flag', 'BULLISH'],
      ['Bear flag', 'BEARISH'],
      ['Higher-low structure', 'BULLISH'],
      ['Lower-high structure', 'BEARISH'],
      ['Volume expansion', 'NEUTRAL'],
      ['Support retest', 'BULLISH'],
      ['Resistance rejection', 'BEARISH'],
      ['Rounded base', 'BULLISH'],
      ['Momentum compression', 'NEUTRAL'],
      ['Range breakout', 'BULLISH'],
    ];
    final seed = ticker.codeUnits.fold<int>(0, (a, b) => a * 31 + b);
    return List.generate(3, (index) {
      final item = names[(seed.abs() + index * 4) % names.length];
      return {
        'name': item[0],
        'bias': item[1],
        'confidence': math.max(
          52,
          math.min(94, quality - index * 8 + (seed.abs() % 7)),
        ),
      };
    });
  }

  Map<String, dynamic> _normalizeScanResult(Map<String, dynamic> data) {
    final tradePlan = data['trade_plan'];
    final setup = data['setup'];
    if (tradePlan is Map) {
      data['signal'] ??= tradePlan['signal'];
      data['quality_score'] ??= tradePlan['quality_score'];
      data['entry'] ??= tradePlan['entry_ideal'] ?? tradePlan['entry'];
      data['stop'] ??= tradePlan['stop'];
      data['target'] ??= tradePlan['target'];
    }
    if (setup is Map) {
      data['setup_type'] ??= setup['setup_type'] ?? setup['type'];
    }
    data['scan_count'] ??= data['search_counter'];
    return data;
  }

  Future<Map<String, dynamic>> scan(
    String ticker, {
    String period = '6mo',
  }) async {
    if (AppConfig.previewMode) {
      await _previewPause(650);
      final normalized = ticker.trim().toUpperCase();
      final seed = normalized.codeUnits.fold<int>(
        0,
        (sum, value) => sum * 37 + value,
      );
      final price = 55 + (seed.abs() % 330) + ((seed.abs() % 100) / 100);
      final quality = 61 + (seed.abs() % 35);
      final bullish = seed.isEven;
      final entry = price * (bullish ? 1.002 : .998);
      final stop = price * (bullish ? .956 : 1.044);
      final target = price * (bullish ? 1.094 : .906);
      _previewScanCount += 1;
      return {
        'ticker': normalized,
        'company_name': '$normalized Holdings',
        'period': period,
        'signal': bullish ? 'BULLISH' : 'BEARISH',
        'quality_score': quality,
        'setup': {
          'setup_type': bullish ? 'TREND_CONTINUATION' : 'REVERSAL_ATTEMPT',
          'direction': bullish ? 'LONG' : 'SHORT',
        },
        'trade_plan': {
          'signal': bullish ? 'BUY' : 'SELL',
          'quality_score': quality,
          'quality_grade': quality >= 90
              ? 'A'
              : quality >= 80
              ? 'B'
              : 'C',
          'entry_ideal': double.parse(entry.toStringAsFixed(2)),
          'stop': double.parse(stop.toStringAsFixed(2)),
          'target': double.parse(target.toStringAsFixed(2)),
          'risk_reward': 2.1 + (seed.abs() % 12) / 10,
        },
        'search_counter': _previewScanCount,
        'patterns': {'recent': _previewPatterns(normalized, quality)},
        'rsi14': double.parse((38 + (seed.abs() % 34) + .4).toStringAsFixed(1)),
        'trend': bullish ? 'UPTREND' : 'DOWNTREND',
        'trend_strength': 40 + seed.abs() % 58,
        'volume_context': {
          'relative_ratio': double.parse(
            (.72 + (seed.abs() % 105) / 100).toStringAsFixed(2),
          ),
          'trend': seed.isEven ? 'RISING' : 'MIXED',
          'price_divergence': 'NONE',
        },
        'levels': {
          'support': double.parse((price * .965).toStringAsFixed(2)),
          'resistance': double.parse((price * 1.052).toStringAsFixed(2)),
        },
        'chart': {
          'provider': 'tradingview',
          'symbol': 'NASDAQ:$normalized',
          'interval': 'D',
        },
        'quota': {'used': 13, 'limit': 100, 'remaining': 87},
        'data_policy': {
          'analysis_location': 'server_side',
          'market_history_included': false,
          'ohlcv_arrays_included': false,
          'chart_provider': 'TradingView',
        },
      };
    }
    final connection = await _providerKeyStore.readConnection();
    if (connection == null) {
      throw ApiException(
        'Connect a Polygon / Massive or Twelve Data key in API settings before scanning.',
      );
    }
    final bars = await _fetchDirectDailyBars(
      ticker.trim().toUpperCase(),
      period,
      connection,
    );
    final response = await http
        .post(
          _uri('/api/intelligence/scan-upload'),
          headers: await _headers(jsonBody: true),
          body: jsonEncode({
            'ticker': ticker,
            'period': period,
            'provider': connection.provider,
            'bars': bars,
          }),
        )
        .timeout(const Duration(seconds: 60));
    final data = Map<String, dynamic>.from(_decode(response) as Map);
    return _normalizeScanResult(data);
  }

  Future<Map<String, dynamic>> runQuantResearch({
    required List<String> tickers,
    Future<void> Function(String message)? onProgress,
  }) async {
    final universe = <String>[];
    for (final ticker in tickers) {
      final clean = ticker.trim().toUpperCase();
      if (clean.isNotEmpty && !universe.contains(clean)) universe.add(clean);
    }
    if (universe.length < 2) {
      throw ApiException('Choose at least two unique ticker symbols.');
    }
    if (universe.length > 8) {
      throw ApiException(
        'Mobile Quant Lab supports up to eight symbols per run.',
      );
    }
    if (AppConfig.previewMode) {
      return _previewQuantReport(universe, 'v1_corporate_quant_system');
    }
    final connection = await _providerKeyStore.readConnection();
    if (connection == null) {
      throw ApiException(
        'Connect a data provider in API settings before running Quant Lab.',
      );
    }
    if (connection.provider == 'polygon' && universe.length > 4) {
      throw ApiException(
        'Polygon / Massive Basic Quant Lab runs support up to four symbols at once. Use four or fewer symbols, or select Twelve Data for a larger universe.',
      );
    }
    final backgroundTaskStarted = await _backgroundTaskService
        .beginQuantLabRun();
    try {
      await onProgress?.call(
        'Loading ${universe.length} daily histories directly from your provider…',
      );
      // The default four-symbol research universe stays within Polygon / Massive
      // Basic's five-calls-per-minute allowance while independent daily-history
      // requests complete together instead of idling between calls.
      var completed = 0;
      final histories = await Future.wait(
        universe.map((ticker) async {
          final bars = await _fetchDirectDailyBars(ticker, '2y', connection);
          completed += 1;
          await onProgress?.call(
            'Loaded $completed of ${universe.length} daily histories…',
          );
          return {'ticker': ticker, 'bars': bars};
        }),
      );
      await onProgress?.call(
        'Building the research report with costs, regimes, and risk controls…',
      );
      final response = await http
          .post(
            _uri('/api/quant/run-upload'),
            headers: await _headers(jsonBody: true),
            body: jsonEncode({
              'tickers': universe,
              'provider': connection.provider,
              'histories': histories,
            }),
          )
          .timeout(const Duration(seconds: 150));
      return Map<String, dynamic>.from(_decode(response) as Map);
    } on ApiException catch (error) {
      if (error.statusCode == 404) {
        throw ApiException(
          'Quant Lab is unavailable on this server. Restart the updated Oryntra server and try again.',
          statusCode: error.statusCode,
        );
      }
      rethrow;
    } finally {
      if (backgroundTaskStarted) await _backgroundTaskService.endQuantLabRun();
    }
  }

  Future<void> verifyProviderKey(String provider, String apiKey) async {
    final connection = ProviderConnection(
      provider: provider == 'polygon' ? 'polygon' : 'twelvedata',
      apiKey: apiKey.trim(),
    );
    if (connection.apiKey.isEmpty) {
      throw ApiException('Paste a valid provider API key first.');
    }
    final now = DateTime.now().toUtc();
    final start = now.subtract(const Duration(days: 14));
    String date(DateTime value) => value.toIso8601String().substring(0, 10);
    final Uri endpoint;
    if (connection.provider == 'polygon') {
      endpoint = Uri.parse(
        'https://api.polygon.io/v2/aggs/ticker/SPY/range/1/day/${date(start)}/${date(now)}?adjusted=true&sort=desc&limit=5&apiKey=${Uri.encodeQueryComponent(connection.apiKey)}',
      );
    } else {
      endpoint = Uri.https('api.twelvedata.com', '/time_series', {
        'symbol': 'SPY',
        'interval': '1day',
        'outputsize': '5',
        'apikey': connection.apiKey,
      });
    }
    late http.Response response;
    try {
      response = await http
          .get(endpoint, headers: const {'Accept': 'application/json'})
          .timeout(const Duration(seconds: 20));
    } catch (_) {
      throw ApiException(
        '${connection.provider == 'polygon' ? 'Polygon / Massive' : 'Twelve Data'} could not be reached. Check your connection and try again.',
      );
    }
    dynamic payload;
    try {
      payload = jsonDecode(response.body);
    } catch (_) {
      payload = const <String, dynamic>{};
    }
    if (response.statusCode < 200 ||
        response.statusCode >= 300 ||
        (payload is Map &&
            (payload['status'] == 'error' || payload['code'] != null))) {
      final message = payload is Map
          ? payload['message']?.toString() ?? payload['error']?.toString()
          : null;
      throw ApiException(
        message?.isNotEmpty == true
            ? message!
            : '${connection.provider == 'polygon' ? 'Polygon / Massive' : 'Twelve Data'} rejected that API key.',
      );
    }
    final rows = connection.provider == 'polygon'
        ? (payload is Map ? payload['results'] : null)
        : (payload is Map ? payload['values'] : null);
    if (rows is! List ||
        rows.isEmpty ||
        !_hasValidOhlcv(rows.first, connection.provider)) {
      throw ApiException(
        '${connection.provider == 'polygon' ? 'Polygon / Massive' : 'Twelve Data'} did not return a completed SPY daily OHLCV candle. Check the key and plan.',
      );
    }
  }

  bool _hasValidOhlcv(dynamic row, String provider) {
    if (row is! Map) return false;
    final open = _finiteNumber(provider == 'polygon' ? row['o'] : row['open']);
    final high = _finiteNumber(provider == 'polygon' ? row['h'] : row['high']);
    final low = _finiteNumber(provider == 'polygon' ? row['l'] : row['low']);
    final close = _finiteNumber(
      provider == 'polygon' ? row['c'] : row['close'],
    );
    final volume = _finiteNumber(
      provider == 'polygon' ? row['v'] : row['volume'],
    );
    return open != null &&
        high != null &&
        low != null &&
        close != null &&
        volume != null &&
        open > 0 &&
        high >= low &&
        low > 0 &&
        close > 0 &&
        volume >= 0;
  }

  Map<String, dynamic> _previewQuantReport(List<String> tickers, String model) {
    return {
      'ok': true,
      'data_provider': 'browser_preview',
      'dataset_fingerprint': 'preview-research-fingerprint-v1',
      'universe': {
        'start': '2024-01-02',
        'end': '2026-08-28',
        'symbols': tickers,
      },
      'configuration': {'model': model},
      'portfolio_risk': {
        'latest_gross_exposure': .94,
        'latest_net_exposure': .42,
        'effective_number_of_positions': tickers.length.toDouble(),
        'largest_name_weight_pct': 28.4,
        'average_abs_correlation_126_sessions': .41,
        'latest_positions': tickers
            .map(
              (ticker) => {
                'symbol': ticker,
                'weight_pct': 100 / tickers.length,
              },
            )
            .toList(),
      },
      'validation': {
        'holdout': {
          'total_return_pct': 8.4,
          'max_drawdown_pct': -5.7,
          'observations': 126,
        },
        'development': {'total_return_pct': 18.7, 'observations': 504},
      },
      'regime_breakdown': [
        {
          'regime': 'UPTREND / NORMAL VOL',
          'sessions': 302,
          'total_return_pct': 15.2,
          'annualized_volatility_pct': 11.8,
        },
        {
          'regime': 'STRESSED',
          'sessions': 74,
          'total_return_pct': -2.8,
          'annualized_volatility_pct': 17.6,
        },
      ],
      'strategy_health': [
        {
          'strategy': 'Trend',
          'recent_mean_daily_bps': 4.2,
          'alpha_decay_daily_bps': -1.1,
          'status': 'MONITOR',
        },
        {
          'strategy': 'Relative strength',
          'recent_mean_daily_bps': 5.8,
          'alpha_decay_daily_bps': .4,
          'status': 'HEALTHY',
        },
      ],
    };
  }

  Future<List<Map<String, dynamic>>> _fetchDirectDailyBars(
    String ticker,
    String period,
    ProviderConnection connection,
  ) async {
    final range = _providerDateRange(period);
    final Uri endpoint;
    if (connection.provider == 'polygon') {
      endpoint = Uri.parse(
        'https://api.polygon.io/v2/aggs/ticker/${Uri.encodeComponent(ticker)}/range/1/day/${range.$1}/${range.$2}?adjusted=true&sort=asc&limit=50000&apiKey=${Uri.encodeQueryComponent(connection.apiKey)}',
      );
    } else {
      endpoint = Uri.https('api.twelvedata.com', '/time_series', {
        'symbol': ticker,
        'interval': '1day',
        'start_date': range.$1,
        'end_date': range.$2,
        'outputsize': '5000',
        'apikey': connection.apiKey,
      });
    }
    late http.Response response;
    try {
      response = await http
          .get(endpoint, headers: const {'Accept': 'application/json'})
          .timeout(const Duration(seconds: 30));
    } catch (_) {
      throw ApiException(
        '${connection.provider == 'polygon' ? 'Polygon / Massive' : 'Twelve Data'} could not be reached. Check your connection and try again.',
      );
    }
    dynamic payload;
    try {
      payload = jsonDecode(response.body);
    } catch (_) {
      payload = const <String, dynamic>{};
    }
    if (response.statusCode < 200 ||
        response.statusCode >= 300 ||
        (payload is Map &&
            (payload['status'] == 'error' || payload['code'] != null))) {
      final message = payload is Map
          ? payload['message']?.toString() ?? payload['error']?.toString()
          : null;
      throw ApiException(
        message?.isNotEmpty == true
            ? message!
            : '${connection.provider == 'polygon' ? 'Polygon / Massive' : 'Twelve Data'} rejected the market-data request.',
      );
    }
    final rows = connection.provider == 'polygon'
        ? (payload is Map ? payload['results'] : null)
        : (payload is Map ? payload['values'] : null);
    if (rows is! List) {
      throw ApiException('The provider returned no completed daily bars.');
    }
    final bars = <Map<String, dynamic>>[];
    for (final row in rows) {
      if (row is! Map) continue;
      final timestamp = connection.provider == 'polygon'
          ? _polygonTimestamp(row['t'])
          : row['datetime']?.toString() ?? row['timestamp']?.toString();
      final open = _finiteNumber(
        connection.provider == 'polygon' ? row['o'] : row['open'],
      );
      final high = _finiteNumber(
        connection.provider == 'polygon' ? row['h'] : row['high'],
      );
      final low = _finiteNumber(
        connection.provider == 'polygon' ? row['l'] : row['low'],
      );
      final close = _finiteNumber(
        connection.provider == 'polygon' ? row['c'] : row['close'],
      );
      final volume =
          _finiteNumber(
            connection.provider == 'polygon' ? row['v'] : row['volume'],
          ) ??
          0;
      if (timestamp == null ||
          open == null ||
          high == null ||
          low == null ||
          close == null ||
          open <= 0 ||
          high <= 0 ||
          low <= 0 ||
          close <= 0 ||
          volume < 0) {
        continue;
      }
      bars.add({
        'timestamp': timestamp,
        'open': open,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume,
      });
    }
    bars.sort(
      (a, b) => a['timestamp'].toString().compareTo(b['timestamp'].toString()),
    );
    if (bars.length < 320) {
      throw ApiException(
        'The provider returned fewer than 320 completed daily bars. Choose a longer window or verify your provider plan.',
      );
    }
    return bars.length > 2000 ? bars.sublist(bars.length - 2000) : bars;
  }

  (String, String) _providerDateRange(String period) {
    final days = switch (period) {
      '5y' => 1900,
      'all' => 3650,
      '2y' => 780,
      '1y' => 540,
      _ => 540,
    };
    final now = DateTime.now().toUtc();
    final start = now.subtract(Duration(days: days));
    String date(DateTime value) => value.toIso8601String().substring(0, 10);
    return (date(start), date(now));
  }

  double? _finiteNumber(dynamic value) {
    final number = value is num
        ? value.toDouble()
        : double.tryParse(value?.toString() ?? '');
    return number != null && number.isFinite ? number : null;
  }

  String? _polygonTimestamp(dynamic value) {
    final milliseconds = value is num
        ? value.toInt()
        : int.tryParse(value?.toString() ?? '');
    return milliseconds == null
        ? null
        : DateTime.fromMillisecondsSinceEpoch(
            milliseconds,
            isUtc: true,
          ).toIso8601String();
  }

  Future<List<dynamic>> watchlist() async {
    if (AppConfig.previewMode) {
      await _previewPause();
      return _previewWatchlist
          .map<Map<String, dynamic>>((item) => Map<String, dynamic>.from(item))
          .toList();
    }
    final response = await http
        .get(_uri('/api/watchlist/'), headers: await _headers())
        .timeout(const Duration(seconds: 15));
    return List<dynamic>.from(_decode(response) as List);
  }

  Future<void> addWatchlist(String ticker) async {
    if (AppConfig.previewMode) {
      await _previewPause(180);
      final normalized = ticker.trim().toUpperCase();
      final exists = _previewWatchlist.any(
        (item) => item['ticker']?.toString().toUpperCase() == normalized,
      );
      if (!exists) {
        _previewWatchlist.insert(0, {
          'ticker': normalized,
          'notes': 'Added during browser preview',
        });
      }
      return;
    }
    final response = await http
        .post(
          _uri('/api/watchlist/add'),
          headers: await _headers(jsonBody: true),
          body: jsonEncode({'ticker': ticker, 'notes': ''}),
        )
        .timeout(const Duration(seconds: 15));
    _decode(response);
  }

  Future<void> removeWatchlist(String ticker) async {
    if (AppConfig.previewMode) {
      await _previewPause(120);
      _previewWatchlist.removeWhere(
        (item) =>
            item['ticker']?.toString().toUpperCase() == ticker.toUpperCase(),
      );
      return;
    }
    final response = await http
        .delete(
          _uri('/api/watchlist/${Uri.encodeComponent(ticker)}'),
          headers: await _headers(),
        )
        .timeout(const Duration(seconds: 15));
    _decode(response);
  }

  Future<List<dynamic>> paperTrades() async {
    if (AppConfig.previewMode) {
      await _previewPause();
      if (!_previewSignedIn) return const [];
      return _previewPaperTrades
          .map<Map<String, dynamic>>(
            (trade) => Map<String, dynamic>.from(trade),
          )
          .toList();
    }
    final response = await http
        .get(_uri('/api/paper/trades/all'), headers: await _headers())
        .timeout(const Duration(seconds: 30));
    return List<dynamic>.from(_decode(response) as List);
  }

  Future<Map<String, dynamic>> openPaperTrade({
    required String ticker,
    required String direction,
    required double entryPrice,
    required double stopPrice,
    required double targetPrice,
    required double size,
    String notes = '',
    String? setupType,
    double? qualityScore,
  }) async {
    final payload = {
      'ticker': ticker.trim().toUpperCase(),
      'direction': direction.trim().toUpperCase(),
      'entry_price': entryPrice,
      'stop_price': stopPrice,
      'target_price': targetPrice,
      'size': size,
      'notes': notes,
      'setup_type': setupType,
      'quality_score': qualityScore,
    };
    if (AppConfig.previewMode) {
      await _previewPause(240);
      if (!_previewSignedIn) {
        throw ApiException(
          'Create an account before opening a paper trade.',
          statusCode: 401,
        );
      }
      final trade = <String, dynamic>{
        'id': DateTime.now().microsecondsSinceEpoch,
        ...payload,
        'status': 'OPEN',
        'current_price': entryPrice,
        'current_pnl': 0.0,
        'current_pnl_pct': 0.0,
        'success_label': 'IN PROGRESS',
      };
      _previewPaperTrades.insert(0, trade);
      return Map<String, dynamic>.from(trade);
    }
    final response = await http
        .post(
          _uri('/api/paper/open'),
          headers: await _headers(jsonBody: true),
          body: jsonEncode(payload),
        )
        .timeout(const Duration(seconds: 20));
    final decoded = _decode(response);
    return decoded is Map
        ? Map<String, dynamic>.from(decoded)
        : <String, dynamic>{'status': 'ok'};
  }

  Future<void> closePaperTrade({
    required int tradeId,
    required double closePrice,
    String notes = '',
  }) async {
    if (AppConfig.previewMode) {
      await _previewPause(180);
      final index = _previewPaperTrades.indexWhere(
        (trade) => trade['id'] == tradeId && trade['status'] == 'OPEN',
      );
      if (index < 0) throw ApiException('Open paper trade not found.');
      final trade = _previewPaperTrades[index];
      final entry = (trade['entry_price'] as num).toDouble();
      final size = (trade['size'] as num).toDouble();
      final direction = trade['direction']?.toString().toUpperCase();
      final pnl = direction == 'SHORT'
          ? (entry - closePrice) * size
          : (closePrice - entry) * size;
      _previewPaperTrades[index] = {
        ...trade,
        'status': 'CLOSED',
        'close_price': closePrice,
        'current_price': closePrice,
        'current_pnl': pnl,
        'current_pnl_pct': entry == 0 ? 0 : pnl / (entry * size) * 100,
        'notes': notes.isEmpty ? trade['notes'] : notes,
        'success_label': pnl > 0 ? 'YES' : 'NO',
      };
      return;
    }
    final response = await http
        .post(
          _uri('/api/paper/close'),
          headers: await _headers(jsonBody: true),
          body: jsonEncode({
            'trade_id': tradeId,
            'close_price': closePrice,
            'notes': notes,
          }),
        )
        .timeout(const Duration(seconds: 20));
    _decode(response);
  }

  Future<void> deletePaperTrade(int tradeId) async {
    if (AppConfig.previewMode) {
      await _previewPause(140);
      _previewPaperTrades.removeWhere((trade) => trade['id'] == tradeId);
      return;
    }
    final response = await http
        .delete(_uri('/api/paper/trades/$tradeId'), headers: await _headers())
        .timeout(const Duration(seconds: 20));
    _decode(response);
  }

  Future<void> registerPushDevice({
    required String token,
    required String environment,
  }) async {
    if (AppConfig.previewMode) return;
    final response = await http
        .post(
          _uri('/api/notifications/device'),
          headers: await _headers(jsonBody: true),
          body: jsonEncode({
            'device_token': token,
            'environment': environment,
            'bundle_id': 'com.silascowles.oryntraai.app',
          }),
        )
        .timeout(const Duration(seconds: 15));
    _decode(response);
  }

  Future<void> addStockAlertSubscription(String ticker) async {
    if (AppConfig.previewMode) return;
    final response = await http
        .post(
          _uri('/api/notifications/stocks'),
          headers: await _headers(jsonBody: true),
          body: jsonEncode({'ticker': ticker.trim().toUpperCase()}),
        )
        .timeout(const Duration(seconds: 15));
    _decode(response);
  }

  Future<List<String>> stockAlertSubscriptions() async {
    if (AppConfig.previewMode) return const [];
    final response = await http
        .get(_uri('/api/notifications/stocks'), headers: await _headers())
        .timeout(const Duration(seconds: 15));
    final decoded = _decode(response);
    if (decoded is! List) return const [];
    return decoded
        .whereType<Map>()
        .map((item) => item['ticker']?.toString().trim().toUpperCase() ?? '')
        .where((ticker) => ticker.isNotEmpty)
        .toList();
  }

  Future<void> removeStockAlertSubscription(String ticker) async {
    if (AppConfig.previewMode) return;
    final response = await http
        .delete(
          _uri(
            '/api/notifications/stocks/${Uri.encodeComponent(ticker.trim().toUpperCase())}',
          ),
          headers: await _headers(),
        )
        .timeout(const Duration(seconds: 15));
    _decode(response);
  }
}
