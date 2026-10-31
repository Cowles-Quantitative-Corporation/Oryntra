# Development map

The current public repository keeps the existing main history and README, then adds coherent source updates separately. Publication does not deploy a service or enable live trading.

| Area | Canonical code and evidence |
| --- | --- |
| Alpha/control | `server/backend/models/alpha/`, `server/backend/control_research.py`, `server/tests/test_alpha_v11.py`, `server/tests/test_alpha_v12.py` |
| Beta | `server/backend/models/beta/`, `server/tests/research/test_beta_availability.py` |
| QOptima/Gamma/risk | `server/backend/qoptima/`, `server/gamma_v1/`, `server/qpo_gamma/`, `server/backend/risk/`, `server/tests/research/`, `server/tests/research_v1/` |
| Delta engineering | [Delta package](../server/backend/delta/README.md), `server/tests/research/delta/` |
| Billing and sign-in | `server/backend/routes/billing.py`, `server/backend/routes/auth.py`, synthetic webhook/challenge tests in `server/tests/` |
| Web workspace | `server/frontend/`, `server/backend/routes/demo.py`, public workspace/API regression tests |
| iOS | `ios-app/`, Flutter analysis and widget tests |
| Public legal pages | `server/frontend/legal/*_canonical.html`, `server/backend/legal_operator.py` |

The optimizer, Gamma and risk integration share imports and are published together to retain a working intermediate revision. Billing, authentication, web workspace, iOS, legal pages and Delta's distinct diagnostic components have separate commits. Historical local Alpha commits are preserved as merge parents; public history is not rewritten.

## Data and artifacts

Raw vendor data, price/prediction/model caches, credentials, private company documents, the Obsidian vault, environments and large backups remain local. The large optional taxonomy seed is also excluded. The small family catalog is source configuration; the IBM planning snapshot is dated public provider metadata, not a credential or permission to execute paid work.

Private dated Delta run directories remain immutable evidence. The public package supplies native source, exact oracle, disabled uncertainty projection, synthetic journal and diagnostic math. Its artifact-specific raw reader/process custody integration remains in the local evidence directories pending a separate portable-runtime review. No private acceptance receipt is replaced with a public path or an invented hash.

## Evidence limits

Synthetic and engineering checks establish the checked behavior, not predictive edge. The saved single-versus-joint forecast comparison improved error by approximately 0.2894%, below its preset 1% screen. This reused April result does not establish a Sharpe improvement or independent holdout success. No new forecasts, fits, holdout scores or market-data purchases were performed for publication.

## Local verification

From `server`, run `PYTHONPATH=. python -m pytest tests`. Optional Gamma model tests use `requirements-research.txt`. Delta native checks retain C++17, ASan and UBSan on macOS; see the package README. From `ios-app`, run `flutter analyze` and `flutter test`. These checks do not sign, deploy or publish a mobile build.
