# Oryntra V1.0 Release Readiness

Last documentation review: 2026-09-03

## Status

- Repository product version: `1.0.0`.
- Flutter package/build: `1.0.0+18`.
- App Store Connect state: **Waiting for Review**, reported by the product owner on 2026-09-02. This repository cannot live-verify Apple’s current state.
- Public scanner model label: **V1.0 Official Momentum** (`official` internally; older source text also calls the policy V7).
- V8, VAI 1.0, and VAI 2.2 remain research candidates, not public-scanner replacements.
- Quant Lab is a separate V1.0 historical portfolio-research system.

## Submitted mobile feature set

The current Flutter source contains account onboarding, browser-direct provider connection, Scanner, Watchlist, Paper, Quant Lab, Account, TradingView chart presentation, notification preferences, tracked-symbol alerts, local stored Quant Lab reports, optional consent-gated ads, background time for Quant Lab, and the latest-scan iOS widget.

The source does not prove every capability is active in the reviewed binary or production environment. Provider requests require a compatible account/plan and browser/mobile network access. Notifications require user permission plus configured native/server push infrastructure. Ads require consent and explicit unit configuration. Quant Lab requires the matching backend route and analysis policy.

## Verification required for a release claim

| Layer | Required evidence | What it proves |
| --- | --- | --- |
| Backend static | Python compile, full tests, JavaScript syntax, documentation regeneration | Source contracts and parsability |
| Backend runtime | Start intended mode and receive a valid `/health` response | Actual local process and route composition |
| Browser | Signed-in provider setup, scan, watchlist, paper trade, backtest, Quant Lab, settings, narrow/desktop states | User-visible web behavior |
| Flutter static | `flutter analyze`, Flutter tests, plist/privacy lint | Dart/native configuration consistency |
| iOS build | Unsigned release build, then signed Xcode archive | Compilation and signing/archive readiness |
| Device | Provider setup, scan, notification permission/delivery, background Quant Lab, TradingView, widget refresh, account deletion | Real iOS integration behavior |
| Production | Health, auth, provider/upload paths, durable database preservation, legal/support URLs | Deployed environment behavior |
| App Store | App Store Connect status and Apple review result | Submission/approval/publication state |

Passing a lower row is not implied by passing a higher row. In particular, backend tests do not prove a device build, and a successful archive does not prove App Store approval.

## Release boundaries

- No brokerage connection, real-money orders, autonomous execution, or individualized investment advice.
- Browser/mobile direct provider keys remain on the user’s device; Oryntra receives normalized bars for the requested calculation, not the key.
- Public scan payloads exclude raw OHLCV history.
- Private research, server-provider, cache, Pattern Lab, VAI training, and Pro routes must remain behind explicit operating-mode controls.
- Market-data display, storage, and redistribution rights must be evaluated against the actual provider plan.
- Database, market cache, and trained-model artifacts are persistent operational state and must not be overwritten by a source deployment.
- Public ownership copy must remain in the pre-formation configuration until a real CQC formation and signed Oryntra IP assignment are confirmed. This source change does not establish either fact.
- Public users cannot access the configurable Portfolio Lab directive ledger. It is disabled by default and requires a server-provisioned internal operator record; public Quant demonstrations use a frozen profile rather than user-tuned portfolio settings.

## Controlled security-audit workstream

Run this workstream before a material production release or after a material authentication, deployment, upload, or public-route change. It is a release-readiness audit, not permission to conduct intrusive testing. Create and verify a recoverable backup of the deployment configuration and persistent operational state before any live check.

| Work area | Repository/deployment review | Owner-authorized public-site confirmation | Required verification if changed |
| --- | --- | --- | --- |
| Access boundaries | Review authentication, route mounting, ownership checks, private/public flags, and account-state handling. | Use only the owner’s test accounts to confirm cross-account reads/writes are rejected. | Regression test the denied and allowed paths; verify no other account data was accessed. |
| Sessions | Review expiry, logout, reset, cookie/token storage, and error paths. | Confirm logout/revocation and reset behavior with controlled accounts at a low request rate. | Exercise expiry/logout/reset paths and confirm old credentials no longer work. |
| Uploads and inputs | Review CSV/browser-bar validation, size limits, MIME/format handling, parser failures, and route schemas. | Submit only harmless, deliberately malformed small fixtures at a low rate. | Add a regression fixture for each fixed validation failure. |
| Exposure controls | Review CORS, rate limits, debug settings, secret loading, error disclosure, security headers, and public/private route separation. | Inspect ordinary responses and intended public/private endpoints; check Cloudflare/origin exposure only with the owner’s approved hostname/IP scope. | Confirm headers/configuration on the deployed response and recheck intended route visibility. |
| Deployment boundary | Review reverse-proxy/Cloudflare configuration and confirm backups exclude secrets from source control. | Make low-rate, authenticated health and route checks only. | Record exact environment, timestamp, paths checked, and whether evidence was static or live. |

### Safety limits

- Do not brute-force credentials, enumerate accounts, test another user’s account, flood endpoints, or run high-rate scanners against the home server.
- Do not use destructive payloads, attempt to bypass Cloudflare, or probe hosts outside the owner-approved public scope.
- Treat static findings, local runtime findings, and live production findings as separate evidence. A repository review does not prove the deployed configuration; a successful low-rate check does not prove the absence of vulnerabilities.
- Patch each confirmed issue narrowly, add a regression test where feasible, rerun the affected release checks, and record the remediation and evidence before declaring the workstream complete.

### Current static remediation record

- 2026-09-05: added a per-email login-failure lockout (default: eight failures, 15 minutes), uniform password-hash work for unknown accounts, baseline anti-framing/content-sniffing/referrer/permissions headers, and no-store handling for API responses. Local regression tests passed for the lockout and headers, plus authenticated provider/public Quant paths. This is source and local-runtime evidence only; it does not verify the deployed Cloudflare/origin configuration or public hostname.
- 2026-09-05 live low-rate check of `https://oryntraai.com`: root, health, and unauthenticated account-status endpoints responded through Cloudflare; private docs/developer/analysis paths returned `404`; an untrusted-origin CORS preflight returned `400`. The deployed responses did **not** include the newly added baseline security headers, so the source fix is not yet live. Do not close this finding until the intended build is deployed and the same header checks pass.

## Documentation gate

Before a new build or model label is released:

1. Update `docs/FEATURES_MODELS_AND_ARCHITECTURE.md` for behavioral changes.
2. Update `server/QUANT_LAB.md` for research-mechanics changes.
3. Regenerate `docs/Oryntra_AI_Master_Technical_Documentation.txt`.
4. Record the exact static, runtime, browser, device, deployment, and App Store checks actually performed.
5. Do not convert a research candidate, local model promotion, or favorable historical result into a public-performance claim.
