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

  static String get privacyUrl => '$authBaseUrl/legal/privacy';
  static String get termsUrl => '$authBaseUrl/legal/terms';
  static String get riskUrl => '$authBaseUrl/legal/risk-disclaimer';
  static String get billingUrl => '$authBaseUrl/legal/refund';
  static String get methodologyUrl => '$authBaseUrl/legal/methodology';
  static String get contactUrl => '$authBaseUrl/legal/contact';
  static String get turnstileUrl => '$authBaseUrl/api/auth/turnstile/mobile';
}
