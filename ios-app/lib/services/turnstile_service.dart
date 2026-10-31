import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';

import '../app_config.dart';

Future<String?> requestTurnstileToken(BuildContext context) {
  return showDialog<String>(
    context: context,
    barrierDismissible: true,
    builder: (_) => const _TurnstileDialog(),
  );
}

class _TurnstileDialog extends StatefulWidget {
  const _TurnstileDialog();

  @override
  State<_TurnstileDialog> createState() => _TurnstileDialogState();
}

class _TurnstileDialogState extends State<_TurnstileDialog> {
  late final WebViewController _controller;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(const Color(0xFF07111F))
      ..addJavaScriptChannel(
        'TurnstileBridge',
        onMessageReceived: (message) => _complete(message.message),
      )
      ..setNavigationDelegate(
        NavigationDelegate(
          onPageFinished: (_) {
            if (mounted) setState(() => _loading = false);
          },
          onWebResourceError: (error) {
            if (error.isForMainFrame == true && mounted) {
              setState(() {
                _loading = false;
                _error =
                    'The security check could not load. Check your connection and try again.';
              });
            }
          },
          onNavigationRequest: (request) {
            final uri = Uri.tryParse(request.url);
            if (uri == null) return NavigationDecision.prevent;
            if (uri.scheme == 'oryntra-turnstile' && uri.host == 'success') {
              _complete(uri.queryParameters['token'] ?? '');
              return NavigationDecision.prevent;
            }
            final authHost = Uri.tryParse(AppConfig.authBaseUrl)?.host ?? '';
            final isAllowedHttps =
                uri.scheme == 'https' &&
                (uri.host == authHost ||
                    uri.host == 'challenges.cloudflare.com' ||
                    uri.host.endsWith('.challenges.cloudflare.com'));
            if (uri.scheme == 'about' || isAllowedHttps) {
              return NavigationDecision.navigate;
            }
            return NavigationDecision.prevent;
          },
        ),
      )
      ..loadRequest(Uri.parse(AppConfig.turnstileUrl));
  }

  void _complete(String token) {
    if (!mounted || token.trim().isEmpty) return;
    Navigator.of(context).pop(token.trim());
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      clipBehavior: Clip.antiAlias,
      insetPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 28),
      child: SizedBox(
        width: 420,
        height: 410,
        child: Column(
          children: [
            ListTile(
              title: const Text(
                'Security check',
                style: TextStyle(fontWeight: FontWeight.w800),
              ),
              subtitle: const Text(
                'Required before sign in or account creation.',
              ),
              trailing: IconButton(
                tooltip: 'Cancel',
                icon: const Icon(Icons.close),
                onPressed: () => Navigator.of(context).pop(),
              ),
            ),
            const Divider(height: 1),
            Expanded(
              child: Stack(
                children: [
                  if (_error == null) WebViewWidget(controller: _controller),
                  if (_loading && _error == null)
                    const Center(child: CircularProgressIndicator()),
                  if (_error != null)
                    Center(
                      child: Padding(
                        padding: const EdgeInsets.all(24),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(_error!, textAlign: TextAlign.center),
                            const SizedBox(height: 14),
                            FilledButton.tonal(
                              onPressed: () {
                                setState(() {
                                  _error = null;
                                  _loading = true;
                                });
                                _controller.loadRequest(
                                  Uri.parse(AppConfig.turnstileUrl),
                                );
                              },
                              child: const Text('Try again'),
                            ),
                          ],
                        ),
                      ),
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
