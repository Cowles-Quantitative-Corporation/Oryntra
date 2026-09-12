import 'package:flutter/foundation.dart';

class AppConfig {
  static const appVersion = '1.0.0';

  static const apiBaseUrl = String.fromEnvironment(
    'ORYNTRA_API_URL',
    defaultValue: 'https://oryntraai.com',
  );

  /// Oryntra's main domain serves both authenticated account routes and the
  /// derived market-intelligence API.
  static const authBaseUrl = String.fromEnvironment(
    'ORYNTRA_AUTH_URL',
    defaultValue: 'https://oryntraai.com',
  );

  static const previewMode = bool.fromEnvironment(
    'ORYNTRA_PREVIEW_MODE',
    defaultValue: kIsWeb,
  );

  static const useTestAds = bool.fromEnvironment(
    'ADMOB_TEST_MODE',
    defaultValue: false,
  );

  static const iosAdMobAppId = String.fromEnvironment(
    'ADMOB_IOS_APP_ID',
    defaultValue: 'ca-app-pub-7922098561896578~3105289410',
  );

  static const iosBannerAdUnitId = String.fromEnvironment(
    'ADMOB_IOS_BANNER_ID',
    defaultValue: 'ca-app-pub-7922098561896578/4302252797',
  );

  static const iosTestBannerAdUnitId = 'ca-app-pub-3940256099942544/2934735716';

  static const androidBannerAdUnitId = String.fromEnvironment(
    'ADMOB_ANDROID_BANNER_ID',
    defaultValue: 'ca-app-pub-3940256099942544/6300978111',
  );

  static const iosInterstitialAdUnitId = String.fromEnvironment(
    'ADMOB_IOS_INTERSTITIAL_ID',
    defaultValue: 'ca-app-pub-7922098561896578/8761060912',
  );

  static const iosTestInterstitialAdUnitId =
      'ca-app-pub-3940256099942544/4411468910';

  static const androidInterstitialAdUnitId = String.fromEnvironment(
    'ADMOB_ANDROID_INTERSTITIAL_ID',
    defaultValue: 'ca-app-pub-3940256099942544/1033173712',
  );

  static String get bannerAdUnitId {
    if (kIsWeb) return '';
    if (defaultTargetPlatform == TargetPlatform.iOS) {
      return useTestAds ? iosTestBannerAdUnitId : iosBannerAdUnitId;
    }
    return androidBannerAdUnitId;
  }

  static String get interstitialAdUnitId {
    if (kIsWeb) return '';
    if (defaultTargetPlatform == TargetPlatform.iOS) {
      return useTestAds ? iosTestInterstitialAdUnitId : iosInterstitialAdUnitId;
    }
    return androidInterstitialAdUnitId;
  }

  static String get privacyUrl => '$authBaseUrl/legal/privacy';
  static String get termsUrl => '$authBaseUrl/legal/terms';
  static String get riskUrl => '$authBaseUrl/legal/risk-disclaimer';
  static String get billingUrl => '$authBaseUrl/legal/refund';
  static String get methodologyUrl => '$authBaseUrl/legal/methodology';
  static String get contactUrl => '$authBaseUrl/legal/contact';
  static String get reportAdUrl => '$authBaseUrl/legal/contact?topic=ad-report';
}
