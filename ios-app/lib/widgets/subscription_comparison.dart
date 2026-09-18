import 'package:flutter/material.dart';
import '../app_config.dart';
import 'glass.dart';

class SubscriptionComparisonSheet extends StatelessWidget {
  const SubscriptionComparisonSheet({super.key, this.currentPlan});

  final String? currentPlan;

  static const _plans = <_PlanDetails>[
    _PlanDetails(
      code: 'base',
      label: 'BASE',
      name: 'Research essentials',
      description: 'Official scanner and bounded historical research.',
    ),
    _PlanDetails(
      code: 'pro',
      label: 'PRO',
      name: 'Oryntra Pro',
      description:
          'More research workspace capacity; no personalized portfolio service.',
      featured: true,
    ),
    _PlanDetails(
      code: 'cqc_max',
      label: 'CQC MAX',
      name: 'Oryntra Pro + Rule Mirror Pro',
      description: 'One bundle for expanded software access in both products.',
    ),
  ];

  static const _features = <_ComparisonFeature>[
    _ComparisonFeature(
      'Official scanner & evidence cards',
      'Included',
      'Included',
      'Included',
    ),
    _ComparisonFeature(
      'Daily scanner reviews',
      '10 / day',
      '200 / day',
      'Unlimited',
    ),
    _ComparisonFeature(
      'Watchlist & paper journal',
      '20 symbols',
      'Unlimited',
      'Unlimited',
    ),
    _ComparisonFeature(
      'Historical research demonstrations',
      'Included',
      'Included',
      'Included',
    ),
    _ComparisonFeature(
      'Frozen public Quant profile',
      'Included',
      'Included',
      'Included',
    ),
    _ComparisonFeature(
      'Personalized portfolio construction',
      'Not offered',
      'Not offered',
      'Not offered',
    ),
    _ComparisonFeature(
      'Saved research presets & exports',
      'Not included',
      'Included',
      'Included',
    ),
    _ComparisonFeature(
      'Rule Mirror Pro',
      'Not included',
      'Not included',
      'Included',
    ),
  ];

  String get _normalizedPlan => currentPlan?.trim().toLowerCase() ?? '';

  bool _isCurrent(String code) {
    if (code == 'cqc_max') {
      return {'cqc_max', 'cqc-max', 'max', 'max_bundle', 'max-bundle'}
          .contains(_normalizedPlan);
    }
    return _normalizedPlan == code ||
        (code == 'pro' && _normalizedPlan == 'plus');
  }

  @override
  Widget build(BuildContext context) {
    final colors = OryntraColors.of(context);
    return SafeArea(
      child: FractionallySizedBox(
        heightFactor: .92,
        child: Material(
          color: colors.panel,
          borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 12, 12, 8),
                child: Row(
                  children: [
                    const Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'ORYNTRA MEMBERSHIP',
                            style: TextStyle(
                              fontSize: 10,
                              fontWeight: FontWeight.w900,
                              letterSpacing: 1.2,
                              color: OryntraPalette.blueBright,
                            ),
                          ),
                          SizedBox(height: 4),
                          Text(
                            'Compare research access',
                            style: TextStyle(
                              fontSize: 22,
                              fontWeight: FontWeight.w900,
                            ),
                          ),
                        ],
                      ),
                    ),
                    IconButton(
                      tooltip: 'Close membership comparison',
                      onPressed: () => Navigator.pop(context),
                      icon: const Icon(Icons.close_rounded),
                    ),
                  ],
                ),
              ),
              const Divider(height: 1),
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(16, 18, 16, 32),
                  children: [
                    const Text(
                      'Subscriptions are under maintenance. This comparison explains planned software access only; it is not an offer of CQC equity, profits, trading participation, or a managed strategy.',
                      style: TextStyle(
                        fontSize: 12,
                        color: OryntraPalette.muted,
                        height: 1.45,
                      ),
                    ),
                    const SizedBox(height: 16),
                    ..._plans.map(
                      (plan) => Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: _PlanCard(
                          plan: plan,
                          current: _isCurrent(plan.code),
                        ),
                      ),
                    ),
                    const SizedBox(height: 16),
                    const Text(
                      'FEATURE COMPARISON',
                      style: TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.w900,
                        letterSpacing: 1.15,
                        color: OryntraPalette.blueBright,
                      ),
                    ),
                    const SizedBox(height: 9),
                    ..._features.map(
                      (feature) => _FeatureRow(feature: feature),
                    ),
                    const SizedBox(height: 16),
                    const Text(
                      'Research candidates remain gated by validation and are never unlocked merely by payment. Oryntra does not connect to a brokerage or place orders.',
                      style: TextStyle(
                        fontSize: 11,
                        color: OryntraPalette.muted,
                        height: 1.45,
                      ),
                    ),
                    const SizedBox(height: 12),
                    OutlinedButton.icon(
                      onPressed: () => Navigator.pop(context),
                      icon: const Icon(Icons.construction_outlined),
                      label: const Text('Purchases under maintenance'),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      'Oryntra AI v${AppConfig.appVersion}',
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        fontSize: 10,
                        color: OryntraPalette.muted,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _PlanDetails {
  const _PlanDetails({
    required this.code,
    required this.label,
    required this.name,
    required this.description,
    this.featured = false,
  });
  final String code;
  final String label;
  final String name;
  final String description;
  final bool featured;
}

class _PlanCard extends StatelessWidget {
  const _PlanCard({required this.plan, required this.current});
  final _PlanDetails plan;
  final bool current;

  @override
  Widget build(BuildContext context) {
    final colors = OryntraColors.of(context);
    final accent = plan.featured ? OryntraPalette.blueBright : colors.rule;
    return Container(
      padding: const EdgeInsets.all(15),
      decoration: BoxDecoration(
        color: plan.featured
            ? OryntraPalette.blue.withValues(alpha: .12)
            : colors.panelRaised,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: current ? colors.green : accent,
          width: current || plan.featured ? 1.35 : 1,
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  plan.label,
                  style: TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.w900,
                    letterSpacing: 1,
                    color: plan.featured
                        ? OryntraPalette.blueBright
                        : colors.muted,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  plan.name,
                  style: const TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w900,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  plan.description,
                  style: TextStyle(
                    fontSize: 11,
                    color: colors.muted,
                    height: 1.35,
                  ),
                ),
              ],
            ),
          ),
          if (current)
            const Padding(
              padding: EdgeInsets.only(left: 10),
              child: Chip(
                label: Text(
                  'CURRENT',
                  style: TextStyle(fontSize: 9, fontWeight: FontWeight.w900),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _ComparisonFeature {
  const _ComparisonFeature(this.name, this.base, this.plus, this.max);
  final String name;
  final String base;
  final String plus;
  final String max;
}

class _FeatureRow extends StatelessWidget {
  const _FeatureRow({required this.feature});
  final _ComparisonFeature feature;

  Widget _value(BuildContext context, String title, String value) {
    final colors = OryntraColors.of(context);
    final unavailable = value == 'Not included' || value == 'Not offered';
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: TextStyle(
              fontSize: 9,
              fontWeight: FontWeight.w800,
              color: colors.muted,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            value,
            style: TextStyle(
              fontSize: 11,
              height: 1.25,
              color: unavailable ? colors.muted : colors.ink,
              fontWeight: unavailable ? FontWeight.w500 : FontWeight.w800,
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final colors = OryntraColors.of(context);
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: colors.panelRaised,
        borderRadius: BorderRadius.circular(13),
        border: Border.all(color: colors.rule),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            feature.name,
            style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              _value(context, 'BASE', feature.base),
              _value(context, 'PLUS', feature.plus),
              _value(context, 'MAX', feature.max),
            ],
          ),
        ],
      ),
    );
  }
}
