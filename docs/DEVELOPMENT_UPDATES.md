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


## Delta protocol acknowledgment and failure telemetry — local review pending

Added a disabled supervised I/P/S/F client around the published owned-child
primitives. It reuses accepted lane/frame/prefix/root checks, exact Python oracle,
state adoption after ACK and bounded first-cause telemetry. Tests exercise the
actual native worker with two invented groups and 14 snapshots, plus adversarial
ACKs and persistence faults. No raw source/target values or capacity trial.

Failure evidence distinguishes attempted, acknowledged and adopted progress;
persistence is bounded best effort, without fsync/atomic crash-durability claims.
Missing or uncertain final ACK cannot complete. Explicit executable/runtime/helper
identities replace dated-directory imports. Archive decoder wiring and new model
influence remain absent. This new behavior is uncommitted pending independent
review and must not be included in the previously approved runtime publication.


Prepublication F-PROT-01 repair separates provisional protocol completion from
verified custody completion and handles KeyboardInterrupt/SystemExit during final
validation with synchronous cleanup and propagation. Synthetic live-watchdog
fixtures inject cancellation across ACK/wait, supervision, stop, group check,
telemetry, release and readback boundaries. This does not add crash durability or
activate raw decoding, forecasts or live trading. Prior rejected scope preserved.


F-PROT-02 repair handles initialization cancellation after valid I ACK and checks
adjacent acquisition boundaries: child creation, registration and monitor thread
startup. Owned child construction cleans known resources on interruption; client
registration occurs before return and outer initialization catches BaseException
for cleanup followed by propagation. Tests cover KeyboardInterrupt and SystemExit
without source data. Monitor/helper/recorder constructors were inspected for
owned-resource handoff; no broader crash-durability claim is added.


## Delta in-memory decoder integration — local review pending

Added the disabled exact Arrow decoder, compact typed group adapter and bridge to
the published supervised native client. Reused full-book validation, physical-row
lineage, F_LAST grouping, quality taint, reset/count and reported-side semantics.
Synthetic partition tests traverse Arrow batches through actual native ACKs and
prove identical snapshots/stream digests. Partial EOF, invalid batches and
cancellation abort the owned client with no completed result.

This is supplied-batch engineering, not archive qualification: no raw loader,
file-descriptor transfer, real data/target access, forecast/model activation or
new score. PyArrow tests run separately in the existing research environment;
repository tests explicitly skip them when unavailable. No package installation,
new subscription or capacity expansion. This new diff awaits independent review
before publication; prior protocol repair was published separately.


F-DEC-01 prepublication repair adds full physical-column string/null validation
before casts or group mutation. Regressions cover mixed-null and large-string
wrong-type inputs across all 74 columns, representative numeric/bool/null types,
prior partial-state preservation, explicit missing-price strings and native bridge
cleanup before P adoption. Source price arithmetic and grouping behavior remain
unchanged for valid inputs; no new acquisition or empirical run.


## Delta tiny-archive authentication/completion — local review pending

Connected accepted byte authentication to the decoder/native bridge for tiny
single-frame synthetic archives. Consumed compressed bytes are independently
hashed under descriptor custody and decoded immutably; exact frame/CSV/row checks
precede native F. Final identity/deadline checks reject late changes before an
archive result can return. Shared resource-sample validation leaves the existing
byte authenticator's tested behavior unchanged. No real archive was read.

The helper is deliberately not a general multi-frame/raw export runner: 1 MiB
compressed, 2 MiB decoded and 4096 rows, with unchanged sampled/cooperative guards.
Tests cover corruption/truncation/trailing frames, identity mutation at final
callbacks, clock latency, I/O failure, cancellation and owned cleanup. This diff
is separate from the already published exact Arrow decoder and awaits review.

## Delta forecasting reassessment — decision draft, no empirical execution

The accepted tiny-archive increment is published separately. Further raw/native
capacity work is deferred: the canonical opening-minute worker cannot itself
produce 5m/30m forecasting contexts, while the earlier accepted research pipeline
already produced and scored them with a failed 0.2894116% screen. Existing cached
completed bars retain activity counts with causal closure checks. See
[the forecasting gap and proposed existing-data study](DELTA_FORECASTING_GAP.md).
This is a reviewable development-only activity/magnitude question, not permission
to read targets, fit a new variant or unlock reserved periods. No new code layer
or raw replay was added merely to continue engineering.


### Delta fixed activity/magnitude study: TRAIN freeze, EVAL paused

Implemented the approved exposed-April cached-bar comparison in
`backend.delta.research`, with exact identity/clock checks, fixed three-versus-seven
inputs, fourteen models and paired balanced losses. Ten synthetic tests pass.
TRAIN completed 4560 magnitudes / fourteen reopened models, retaining 4994 EVAL
feature keys. A pre-value enum failure is preserved. EVAL scoring is paused:
schema inspection parsed one existing EVAL file before freeze, although only
field names/counts were output. This deviation needs explicit review; no clean
access-order claim, fresh-holdout or trading-economics conclusion is made. See
[the decision and execution checkpoint](DELTA_FORECASTING_GAP.md).

### Delta magnitude continuation completed: descriptive screen passes

One independently allowed continuation reused the exact 14 frozen models and
4994 EVAL targets. Balanced magnitude MSE reduction is 9.9765546%; all seven
names, eleven dates and both fixed halves improve, with zero negative forecasts.
This remains contaminated reused-April development because a full EVAL JSON was
parsed before freeze. Keys/counts-only and no influence are author assertions,
not independently proven history. The scorer preserves this limitation. There
is no directional, economic, Sharpe or trading qualification. Author checked
19,976 exact errors and 77 cells; 11 focused / 190 broader tests pass, optional
skips explicit. Independent result audit is next; no parameter search or model
activation. See [the complete result and limits](DELTA_FORECASTING_GAP.md).

### Delta magnitude arithmetic independently accepted; qualified publication

Independent result review verified 4994 identities, 19,976 exact errors, 77
balanced cells and fourteen frozen models. The magnitude comparison's 9.976554629%
MSE reduction is distinct from the earlier signed-return 0.2894116% **FAIL**.
Baseline/challenger improvements against the TRAIN-mean magnitude reference are
0.40243%/10.33883%. This publication includes source, synthetic tests and the
[sanitized findings and limitations](DELTA_FORECASTING_GAP.md), without targets,
fitted models, cache exports, private-path receipts or vault content.
The complete April 16 EVAL JSON was parsed before freeze: evidence remains
contaminated descriptive development, with no clean-validation, directional
alpha, profit or Sharpe implication. The next dependency is a reviewed genuinely
unconsumed out-of-time cohort for the unchanged magnitude question, not another
April parameter search or immediate release of reserved data. No model is enabled.
