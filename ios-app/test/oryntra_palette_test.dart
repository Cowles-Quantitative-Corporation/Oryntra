import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oryntra_ai/app_config.dart';
import 'package:oryntra_ai/screens/auth_gate_screen.dart';
import 'package:oryntra_ai/screens/quant_lab_screen.dart';
import 'package:oryntra_ai/services/api_service.dart';
import 'package:oryntra_ai/widgets/common.dart';
import 'package:oryntra_ai/widgets/glass.dart';
import 'package:oryntra_ai/widgets/subscription_comparison.dart';

void main() {
  test('institutional palette keeps a dark readable surface system', () {
    expect(OryntraPalette.navy.computeLuminance(), lessThan(.02));
    expect(OryntraPalette.ink.computeLuminance(), greaterThan(.8));
    expect(OryntraPalette.blueBright.computeLuminance(), greaterThan(.4));
  });

  test('account traffic defaults to the main Oryntra domain', () {
    expect(AppConfig.authBaseUrl, 'https://oryntraai.com');
    expect(AppConfig.apiBaseUrl, 'https://oryntraai.com');
  });

  test('iOS ad mode keeps banner and interstitial inventory aligned', () {
    debugDefaultTargetPlatformOverride = TargetPlatform.iOS;
    try {
      expect(
        AppConfig.bannerAdUnitId,
        AppConfig.useTestAds
            ? AppConfig.iosTestBannerAdUnitId
            : AppConfig.iosBannerAdUnitId,
      );
      expect(
        AppConfig.interstitialAdUnitId,
        AppConfig.useTestAds
            ? AppConfig.iosTestInterstitialAdUnitId
            : AppConfig.iosInterstitialAdUnitId,
      );
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('section labels retain institutional hierarchy', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: InstitutionalSectionLabel(label: 'Market intelligence'),
        ),
      ),
    );

    expect(find.text('MARKET INTELLIGENCE'), findsOneWidget);
  });

  testWidgets('sign-in gate has a Material surface for form controls', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: AuthGateScreen(api: ApiService(), onAuthenticated: () async {}),
      ),
    );

    expect(find.text('Sign in to continue.'), findsOneWidget);
    expect(find.byType(TextField), findsNWidgets(2));
    expect(tester.takeException(), isNull);
  });

  testWidgets('startup screen presents the animated workspace intro', (
    tester,
  ) async {
    await tester.pumpWidget(const MaterialApp(home: AppStartupScreen()));

    expect(find.text('ORYNTRA AI'), findsOneWidget);
    expect(find.text('Opening your research workspace'), findsOneWidget);
    await tester.pump(const Duration(milliseconds: 500));
    expect(tester.takeException(), isNull);
  });

  testWidgets('Quant Lab uses stable portfolio control chips', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: QuantLabScreen(api: ApiService())),
      ),
    );

    await tester.drag(find.byType(ListView), const Offset(0, -700));
    await tester.pump();
    expect(find.text('Target annual volatility'), findsOneWidget);
    expect(find.text('Conservative · 8%'), findsOneWidget);
    expect(find.byType(Slider), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('membership comparison shows the published plan limits', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: SubscriptionComparisonSheet(currentPlan: 'plus')),
      ),
    );

    expect(find.text('Research essentials'), findsOneWidget);
    expect(find.text('Oryntra Pro'), findsOneWidget);
    expect(find.text('Oryntra Pro + Rule Mirror Pro'), findsOneWidget);
    expect(find.text('CURRENT'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('10 / day'), 240);
    expect(find.text('10 / day'), findsOneWidget);
    expect(find.text('200 / day'), findsOneWidget);
    expect(find.text('Unlimited'), findsWidgets);
    await tester.scrollUntilVisible(
      find.text('Purchases under maintenance'),
      240,
    );
    expect(find.text('Purchases under maintenance'), findsOneWidget);
  });

  testWidgets('saved Quant Lab report can be reopened on device', (
    tester,
  ) async {
    final key = GlobalKey<QuantLabScreenState>();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: QuantLabScreen(key: key, api: ApiService()),
        ),
      ),
    );

    key.currentState!.showStoredReport({
      'dataset_fingerprint': 'saved-device-report',
      'portfolio_risk': {'latest_gross_exposure': 0.8},
      'validation': {
        'holdout': {'total_return_pct': 2.1, 'max_drawdown_pct': -1.4},
      },
    });
    await tester.pumpAndSettle();

    expect(find.text('RESEARCH REPORT', skipOffstage: false), findsOneWidget);
    expect(
      find.text('saved-device-report', skipOffstage: false),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });
}
