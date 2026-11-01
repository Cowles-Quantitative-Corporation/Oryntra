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


## Delta portable reader increment — local review pending

Added disabled byte-authentication and file-descriptor custody in
`server/backend/delta/volume/source_reader.py`, with temporary synthetic tests in
`server/tests/research/delta/test_source_reader.py`. Exact size/hash/EOF and final
identity are checked under mandatory sampled resource guards; descriptor-close
and final-guard failures cannot return a completion receipt. The reader emits
metadata only, not decoded feed events. No process supervisor, raw-source runner,
forecast/trading activation or historical workload is installed or executed.

Bounds remain 600 seconds cooperative wall, 512 MiB sampled combined RSS, 2 GiB
free-space reserve and 1 MiB reads. OS probe authenticity and unsampled peaks are
not guaranteed; full process-tree supervision remains unfinished. Review and
synthetic tests precede publication of this increment. Existing dataset and
immutable research artifacts remain at their local paths. No public legal or
business terms changed.


Reader prepublication repair F-SRC-01 revalidates the held descriptor after the
last in-custody probe and the pathname after the final post-close probe. Late
replacements, in-place writes/truncation, unlink and symlink swaps now reject;
primary I/O errors retain secondary close evidence. Receipt identity is a
last-check snapshot, never a guarantee against later external changes. Focused
tests pass; the full Delta checks use the repository pytest environment plus the
existing research environment for its installed sklearn comparison. No new
dependency, source run or publication accompanies the local repair.

Final deadline repair F-SRC-02 adds a monotonic-clock check after final path stat,
with no additional resource callback. A controlled-clock regression covers both
normal completion and a stat returning at the exact deadline; expired completion
rejects while preserving the final identity snapshot semantics.


## Delta owned-worker runtime — local review pending

Ported accepted unreaped-leader/group cleanup and parent-plus-owned-child sampled
RSS supervision into `server/backend/delta/runtime`. The macOS C helper remains
byte-identical; Python now uses explicit helper path/hash rather than a dated
artifact-directory dylib. Spawning remains disabled by default. Limits cannot
exceed the existing 512 MiB sampled RSS, 25 ms polling or 250 ms uncertainty bounds.

Focused synthetic tests cover successful completion, child failure, a surviving
forked descendant after exit zero, external reaping without signaling, uncertain
group release, library pins, sample identity/gap/RSS boundaries and watchdog stop.
No raw data, native capacity run, new prediction or scoring occurred. Protocol
transport, durable telemetry and archive-decoder integration remain separate;
these primitives are not a live consumer or hard resource-enforcement service.
This uncommitted increment awaits independent review before publication.
