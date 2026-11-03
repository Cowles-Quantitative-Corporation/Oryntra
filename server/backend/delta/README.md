# Delta engineering research

Canonical source for the checked-integer reported-volume worker, exact Python oracle, unknown-side uncertainty intervals and disabled artifact projector. No production archive ingestion route, trading route, account adapter or model activation is installed. A disabled in-memory Arrow decoder is described below. `model_vector()` remains disabled.

The native worker is the accepted opening-minute engineering implementation. Its fixed profile hash and caps describe one engineering scope; they do not qualify arbitrary data or a production capacity. Existing pinned private artifacts remain where they were created. The projector retains their exact acceptance identities and accepts invented test fixtures only through its explicit synthetic flag. No artifact payload is bundled.

Native source currently targets macOS CommonCrypto with C++17 and ASan/UBSan. Build outputs belong in a temporary build directory. Run Python tests from `server` with `PYTHONPATH=.` and `python -m pytest tests/research/delta`. Compile `tests/research/delta/native_cap_tests.cpp` and `native_parser_tests.cpp` using `clang++ -std=c++17 -O0 -g -fsanitize=address,undefined -fno-omit-frame-pointer -I backend/delta/native`, placing each output in a temporary directory and executing it there.

Portability changes replace dated-directory import bootstraps with explicit package imports and isolate the fixed oracle caps from raw-runner profiles. The test that compared bytes to a private historical v32 copy is excluded; repaired-clock, future-lineage, incomplete-result, tampering, disabled-default and unknown-mass behavior tests remain. `source_origins.json` records source identities without absolute machine paths.

Raw vendor files, price/model/prediction caches, raw-source launchers and private audit records remain local. The artifact-specific archive decoder/native transport is not yet a portable canonical runtime. Disabled owned-child custody and sampled-supervision primitives are available in `runtime`, without a source runner or model consumer. Source packaging and synthetic checks do not establish improved forecasts, Sharpe, live readiness or licensed redistribution rights.

The `journal` package retains synthetic-only durable, deduplicated reconciliation evidence. It has no broker integration and does not migrate legacy journals implicitly. Concurrency, post-commit failure and recovery tests use temporary invented journals.

The `diagnostics` package provides explicit decimal arithmetic, covariance/envelope diagnostics, a synthetic transaction ledger and admission checks. Policies in its tests are invented fixtures. No private account receipt adapter is installed, and diagnostic success does not authorize execution.

## Disabled portable source authentication

`volume.source_reader.authenticate_source` is a byte-authentication boundary, not
an archive decoder or native runner. It opens only a regular non-symlink file,
keeps descriptor custody through bounded EOF hashing, checks exact manifest size,
SHA-256 and final file/path identity, and returns metadata only after closing the
owned descriptor and passing the final guard. It never returns source payloads.

Callers must explicitly enable it and provide a fresh `ResourceSample` probe for
the complete owning process tree. Missing, stale, future or malformed samples
fail closed. Existing ceilings remain: 600 seconds cooperative wall time,
512 MiB sampled combined RSS, 2 GiB disk reserve and at most 1 MiB per read.
The library validates samples; it does not implement/attest an OS sampler, reserve
disk, preempt blocking reads, or guarantee unsampled RSS peaks. A blocking read
or probe can exceed a deadline before the next check rejects. No native child is
started or supervised by this function. Process-tree custody, decoding, staging
and atomic output publication remain separate integration work; this reader does
not replace the accepted private supervisor or enable its source profile.

Tests use temporary invented bytes only:
`PYTHONPATH=. .venv/bin/python -m pytest tests/research/delta/test_source_reader.py`.
No private artifact paths, vendor data, entitlement or accepted-run hashes are
bundled. All historical source permissions and protected cohorts remain separate.

The final in-custody resource callback is followed by an fd identity check. After
close and the last resource callback, pathname identity is checked immediately
before receipt construction, with no further caller callbacks. Replacement,
mutation or unlink during either callback rejects. This is a snapshot at the
last validation, not a race-free guarantee of future pathname contents. A future
decoder must authenticate its own held descriptor or use a separately verified
custody handoff; it cannot treat this closed-reader receipt as perpetual access
authority. This reader does not hand off an open descriptor.

A clock-only deadline check follows final pathname validation, before receipt
construction. This rejects a late-returning filesystem call without adding a
resource callback that could invalidate the final identity snapshot.

## Owned native-worker custody and sampled supervision

`runtime.owned_process.OwnedProcess` preserves an unreaped direct session leader
until its group is confirmed clear. Construction is disabled unless explicitly
enabled with a group inspector and a new session. External reaping is custody
uncertainty: do not signal that potentially reused identity. Descendants surviving
an otherwise successful exit prevent release/completion; cleanup targets only the
still-owned group. Uncertain group inspection or failed cleanup remains an error.

`runtime.supervision.OwnedReader` takes an explicit path and SHA-256 for a locally
built `runtime/owned_rss.c` helper. The helper is unchanged from the accepted
implementation and uses macOS libproc; this is package portability, not Linux or
Windows qualification. Build into a temporary/build directory, not into source:
`clang -dynamiclib -Wall -Wextra -Werror owned_rss.c -o /tmp/owned_rss.dylib`.
Record the actual library hash before constructing the reader. File hash pinning
is not a claim of protection against a hostile helper-path replacement race.

`Monitor` samples parent plus its attested direct child, rejects unexpected
processes, identity changes, missing readings, excessive read spans/gaps and RSS
above 512 MiB. At most 25 ms polling and 250 ms uncertainty bounds are retained;
callers may tighten but not enlarge them. Child disappearance is accepted only
for a confirmed zero exit with final completion pending and no surviving group.
First failure remains sticky even if the caller's cleanup callback also fails.
The failure callback owns cleanup policy; stop/join the watchdog explicitly.
The monitor alone does not write a durable incident, wire a native protocol or
promise an automatic rollback of consumer state.

These are sampled/cooperative checks, not hard peak-memory or wall-time limits.
Group inspection covers the still-owned session group; it does not claim custody
of a process that deliberately escapes that group or complete global process
observability. No global process inventory is used by this helper. `OwnedProcess`
requires explicit cleanup/release; do not substitute ordinary `Popen.wait()` or
external reaping. Tests compile only the small helper in a temporary directory
and exercise invented children, descendant-on-exit, failure and watchdog cases.

Next integration is a reviewed native-protocol client binding these primitives
to the worker's final acknowledgment and sticky failure telemetry. Archive decode,
raw feeds, model activation and live trading remain absent. No default source
profile or empirical workload is enabled by importing this package.

## Disabled ACK/state-adoption client and bounded failure evidence

`runtime.client.SupervisedClient` now connects the accepted bounded transport to
owned-worker supervision. It is disabled by default, requires an explicit
hash-checked native executable, reader, `FailureRecorder` and declared runtime
manifest identity. The fixed engineering source/profile/lanes/snapshot schedule
match the existing native worker. Those dormant caps are not newly qualified
full-session limits or authority to feed raw data. No data path or decoder is
installed. Tests use sparse invented frames with the existing source namespace
as a protocol constraint, not a claim of recorded observations.

Each I/P/S/F ACK binds global sequence, lane, request-frame SHA, cumulative stream
prefix, expected lane digest and aggregate root. S also compares the full oracle
snapshot. Pending Python state is adopted only after validation. Missing,
unsolicited, reordered, mismatched or uncertain ACKs poison the entire client;
there is no partial restart or replay queue. `finish()` needs matching F, clean
exit/group custody, final monitor checks and a clear telemetry latch. There is
no successful completion merely because the worker exited zero.

First cause and last attempted/ACK/adopted state are captured before cleanup.
Foreground failure or context exit attempts one exclusive, bounded 4 KiB failure
record after cleanup. Capture itself has no callback I/O; a watchdog failure is
only in memory until foreground abort/exit persists it. The accepted recorder
reports `WRITTEN_NO_FSYNC_OR_ATOMIC_FAILURE_GUARANTEE`: this is best-effort saved
evidence, not guaranteed crash durability. Persist failure retains the primary
cause and `UNAVAILABLE_OR_PARTIAL_WRITE`, never a completion claim. Repeated abort
does not replace the first record. The caller must provide a dedicated existing
output directory; no raw bytes, prices or credentials belong in this telemetry.

The runtime-manifest digest binds a declared identity through the wire/root; the
client does not attest every file referenced by that manifest. Binary hash checks
are local snapshot checks, not a hostile concurrent replacement defense. New
executables need independently reviewed identities. Native supervisor callbacks,
ACK and adopted state are timestamped views, not a single atomic global snapshot.
No new score, policy, source subscription or forecast/trade consumer is enabled.


Cancellation repair: protocol-F completion is provisional (`protocol_finished`),
not authoritative `finished`. The client sets its verified-completion flag only
after final supervision, custody release and result readback. `finish()` catches
`BaseException` for synchronous cleanup and then re-raises the original interrupt
or system exit. Context exit requires verified release rather than trusting a
provisional flag. A second interruption of cleanup is recorded as secondary;
this is not a guarantee of cleanup under repeated interruption or process crash.
No further resource callback is introduced after verification to manufacture a
completion. Failure remains fail-closed even after the leader was already reaped.

Constructor cancellation repair: initialization uses the same cleanup-and-reraise
path as finalization. `OwnedProcess` protects a known created child if Popen or
registration is interrupted, and invokes `on_created` before returning so the
client retains custody across the assignment/configuration handoff. The monitor
already registers through `on_created` before its initial sample/thread start.
If low-level creation or a second interruption leaves custody uncertain, cleanup
uncertainty is retained; no unrelated identity is signaled and no success is
claimed. Neither interruption nor system exit is converted into normal success.
The low-level registration hook is needed by callers that require a cleanup
reference before the constructor result is assigned.

## Disabled in-memory Arrow decoder and native bridge

`decoding.decoder.ArrowDecoder` reuses accepted full-depth exact-price validators,
per-symbol F_LAST grouping and permanent source flags14 quality latch. It accepts
only supplied all-string RecordBatches with the fixed 74-column April MBP10 schema;
it never opens a file, decompresses an archive or fetches data. `types` extracts the
accepted compact-book/Trade/Group shapes without importing any forecast producer.
The adapter uses exact canonical type identity rather than dated-file inspection.
Physical indices and true row counts remain separate across interleaved symbols;
unknown N mass is never classified. Raw C actions are book cancellation, not an
invented trade-bust notice. Failure/cancellation latches the decoder; partial EOF
or unmatched occurrences cannot be silently resumed or called complete.

`decoding.bridge.DecodedVolumeBridge` feeds supplied batches of at most 512 rows
to an already acquired fixed-profile supervised client. It samples resources
before/after decoding and sends only wholly contained opening-minute groups;
pre/open-straddling and out-of-slice groups are excluded, not clipped. Snapshots
include equal-clock completions and fire on the first strictly later completion.
Caller-declared end of batches flushes the schedule only after group/occurrence
conservation checks. Use the client context around bridge creation/operations;
the bridge does not acquire or transfer ownership of a child. Any bridge error
aborts the borrowed client and re-raises; cleanup uncertainty is secondary evidence.

Completion qualifies **provided batches only**, not a real archive EOF/hash,
capture completeness, original availability or correction lineage. The returned
`source_bytes_authenticated` is false. There is no file-descriptor handoff from
`authenticate_source`, raw CSV/Zstandard loader, inherited archive certificate or
accepted v31 right-censored prefix reader here. No full-session/source run or
model influence is enabled. The current calendar/schema/profile is April1
engineering scope, not general April–July qualification. Full-batch validation is
source rejection behavior, not a new predictive-availability rule.

PyArrow is an optional decoding dependency already present in the local research
environment; it is not imported by the base package. No dependency was installed.
The repository pytest environment lacks it and explicitly skips Arrow tests. Run
those tests in the existing Arrow environment using unittest discovery with
`-p test_arrow_decoder.py` and `-p test_decoded_bridge.py`; record this separately
from the repository test result. Tests create invented RecordBatches and temporary
ASan/UBSan native/helper builds, never read raw source data. Optional Arrow lazy
initialization occurs before child acquisition, preserving the 250ms sample gap
rather than loosening it.

F-DEC-01 repair: before any conversion or group mutation, every expected column
must have exact Arrow `string` type and actual null_count zero. Nullable schema
metadata alone is not a rejection; present Arrow null values are. Empty strings
and the documented numeric missing-price sentinel remain explicit source
encodings, subject to normal book/trade validation. No aggregate all/any operation
may silently skip a null into an emitted group. Invalid batches latch the decoder
and abort the bridge before any P adoption from that batch.

## Disabled tiny-archive authentication/completion integration

`decoding.archive.decode_small_archive` is the next bounded engineering helper:
one known-content-size Zstandard frame, at most 1 MiB compressed / 2 MiB decoded /
4096 rows, all explicit before consumption. It rejects concatenated/trailing or
truncated frames, unknown/oversized content declarations, quotes, invalid headers,
row widths/counts and decoder failures. It is not the full vendor archive reader
or permission to process real exports. Existing multi-frame archives exceed this
narrow scope; no full-session capacity or source-completeness qualification follows.

The accepted byte authenticator is reused first. A new held regular non-symlink
file is then identity-checked and its exact consumed bytes independently hashed
before decoding. The immutable compressed buffer, not a reopened pathname receipt,
feeds decompression and CSV/Arrow conversion. The descriptor stays held through
native completion. Final descriptor and post-close pathname checks, after the
last resource callbacks, plus a final clock-only deadline check precede return.
The receipt is a validation-time snapshot, never perpetual pathname authority.

No native F completion is requested until frame/CSV/group/row checks pass. A late
identity/resource error still aborts the client and returns no archive result,
even if its native F was previously acknowledged. Models/trades remain disabled.
The existing synchronous cleanup/propagation and best-effort telemetry limits
apply; no crash or repeated-interruption cleanup guarantee is added.

A mandatory external source-resource probe and the native client's own sampled
checks remain distinct. The source probe must attest its current ownership phase
(including parent-only after verified child release); this helper does not supply
or attest that OS sampler. Shared `_checked_resource_sample` preserves the old
512 MiB / 2 GiB reserve / 250 ms uncertainty checks. Wall limits remain cooperative,
at most 600 seconds. The old byte-authenticator call counts and final checks are
covered unchanged by its 36 tests. Tiny-buffer bounds are not a peak-RSS guarantee.

Arrow/Zstandard tests use only freshly created temporary synthetic archives in
the existing research environment. No dependency installed, actual vendor file,
reserved target, new prediction, score or source profile activation. The returned
source hash authenticates supplied bytes, not upstream capture/PIT or valid use
rights. This local increment awaits independent review before publication.


## Synthetic pending fee compatibility

The disabled `diagnostics.transaction_adapter.admit_synthetic` bridge supports
unresolved unfilled parents only when their stored fee semantics match the new
profile: aggregation, rate, minimum and quantum. Numeric spelling, profile ID
and unrelated metadata do not define compatibility. Partial-parent mapping
remains unqualified. Terminal parents are excluded; their existing holdings and
payables remain in risk valuation.

Existing mapping blockers and pure-gate `NOT_EVALUABLE` results retain precedence.
A different or invalid stored fee yields a fresh `NOT_EVALUABLE` with empty
metrics before any reservation. Incompatible valuation may be computed internally
by the pure gate, but is neither exposed nor committed. This does not implement
heterogeneous fee valuation, change tariffs, establish broker applicability or
claim improved economic performance. Direct pure-gate calls have no stored-ledger
fee context and are unchanged.


## Disabled reference-own-risk prerequisite

`research.reference_ledger.derive_reference_ledger` is a pure, disabled-by-default
validator/deriver for supplied byte-pinned source bundles. It has no file reader,
portfolio runner, model fit or admission path. Source closure, full-record and
per-asset attempt identities, adjusted OPEN-to-terminal-close basis, modeled
maturity, missingness and numeric crosschecks must validate before immutable
reference rows are returned. Invalid inputs raise `ValueError` with a
`NOT_EVALUABLE` reason; no partial ledger is returned.

Reference identities differ from full-model identities. Exact rational arithmetic
uses the preserved native decimal rendering of decoded numeric inputs; the fixed
legacy-error validation tolerance is 1e-12. This does not recover unrounded prices.
Due-but-unavailable labels remain missing at each cutoff, even when the global
source contains every outcome. No historical input has been qualified by the
synthetic tests. Recorded publication remains unattested, terminal labels are
not execution returns, and the original full-model guards remain unchanged.
This component is a prerequisite for a separately reviewed forecast-plus-own-risk
policy comparison, not evidence of improved forecasting, net value or Sharpe.

Reference label timestamps accept explicit ISO seconds with up to nine fractional
digits and `Z` or a whole-minute numeric UTC offset. Nanoseconds are parsed
exactly; unsupported precision is rejected. Expected-open equality is exact,
so a label declared one nanosecond later cannot become available early.


## Disabled one-shot reference source qualifier

`research.qualify_reference` is one script calling the pure reference component.
Import/help/config-only modes perform no historical source I/O. An explicit exact
authority receipt must bind operation, scope, config and code before source reads,
stats or hashes. Receipts are reviewed scope records, not cryptographic authority.
No historical execution receipt or source payload is bundled.

The script authenticates the exact supplied bytes and rehashes before completion.
It compares every R46 asset/cutoff membership and missing-ID set through ORIGINAL
attempt identities, not counts alone. Actuals, residuals and rows belong only in
ignored local outputs; no economic values are emitted in summaries. Historical
outputs must be confined to `local-artifacts`; synthetic tests use temporary paths.

Fixed guards are 60 seconds cooperative wall, 256 MiB sampled process-peak readings,
5 MiB aggregate logical output (including retained pending/final hardlink bytes
and failure headroom), and a 2 GiB disk reserve. Blocking operations may overrun
between checks. Resource claims end at reported samples; final hardlink completion
may occur later. Confirmed completion is not relabeled failed. Publication identity
uncertainty is reported separately, with best-effort bounded evidence and no
automatic retry. These are neither hard peak limits nor crash/repeated-interruption
durability guarantees. Only invented bundles have been exercised; actual source
qualification, policy integration and economic evaluation remain unapproved.

Qualifier publication handles short raw writes until the complete payload is
written, rejects zero/invalid progress, and verifies staged size and SHA-256 against
the encoded payload before terminal linking. This is a staged-content check, not
a guarantee against arbitrary later external filesystem mutation.

## Disabled isolated recorded-reference consumer

`research.reference_consumer` is a separate, explicitly enabled research consumer;
there is no historical launcher or market reader. It authenticates an accepted
qualification and its source-record/calendar bytes, restores exact `Fraction`
residuals, and checks original-attempt membership and missing masks at each cutoff.
Recorded-reference forecasts, candidates, plans and reports have distinct identities.
The full-model consumer and its identity guards are unchanged.

Explicit hash-checked preserved code bindings reuse the original anchor validator,
proportional economic precheck, empirical tail/second-moment sizing, strict whole-share
fee wrapper and underlying unified account loop. The scenario builds reference
candidates itself instead of invoking the full-model scenario's candidate/attribution
builder. Admission uses a fresh empty shadow funded by **free settled cash minus
reservations**, preserving the original denominator and 2% shadow loss budget;
actual portfolio post-cost caps, partial fills, retries and required exits remain
in the existing account machinery. No cheaper fees or full-fill assumption is added.

Portable tests exercise disabled/plan guards. Optional pinned-code integration tests
use only invented forecasts, labels and quotes; set `DELTA_FROZEN_CODE_ROOT` to the
separately preserved code root and run, from the repository:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=server:server/tests/research/delta python -m unittest test_reference_consumer -v
```

No preserved source payload is bundled or loaded by those fixtures. This is synthetic
consumer qualification pending independent review, **not** historical R48 replay
parity or a paired policy result. Availability remains modeled/publication-unattested;
adjusted open-to-terminal-close residuals are not execution fill returns. A separate
bounded replay decision and exact full-control parity are required before economic
interpretation. Live and external-paper authorization remain false.

## Disabled reference-only replay preparation

`research.reference_replay` supplies one explicitly disabled runner for the
recorded-reference forecast **plus its own risk calibration**, using the accepted
consumer and original account/lifecycle. It does not rerun the accepted full
control, refit models, alter fees, force exposure or authorize trading.

A reviewed exact one-attempt receipt binds code/config/source identities before
historical path operations. The fixed real scope accepts only the previously
qualified reference bank and accepted April control/cache identities. Private
configuration and all result/price/model data stay outside published source.
Import/help/config validation do not read configured historical files.

The runner uses the original readonly SQLite query and decoder, rejects WAL,
SHM/rollback sidecars or non-DELETE journal mode, validates strict source ordering
and complete sessions, preserves transition/final closes, and checks every one
of the 147 underlying source anchors against the accepted control. Raw-row and
decoded-event digests are **new run provenance**, not a preexisting historical
control digest. Source authentication is a checked snapshot, not perpetual
pathname or concurrent-writer custody.

Native economic abstention (including zero orders) is a valid complete outcome.
Missing held marks retain all expected-day rows and make comparison NOT_EVALUABLE;
no partial-period aggregate performance is published. Source/resource failure and
uncertain publication have separate statuses. Independent state means independent
cash, holdings, pending orders and displayed capacity; exposure differences are
outcomes, not forced controls or alpha evidence.

Fixed proposed limits are 360 cooperative seconds, 512 MiB sampled process-peak
RSS, 128 MiB logical output and a 2 GiB disk reserve. Logical bytes count both
retained hardlink names plus failure headroom. Existing guarded write/commit
helpers preserve confirmed completion and distinguish uncertain links. Blocking
operations can overrun between checks; these are not hard peak/deadline or crash
and repeated-interruption guarantees. No child processes are spawned.

Only invented archives are tested here. Optional code-bound tests reuse
`DELTA_FROZEN_CODE_ROOT` as above and exercise the actual native decoder, consumer
and account with synthetic labels/quotes; benchmark and control values in those
fixtures are invented. Real source compatibility, exact-run authority and result
audit remain separate gates. No historical reference replay is qualified by this
implementation, and no net-value, alpha or Sharpe improvement is claimed.

## Disabled reference-mean / full-risk sensitivity adapter

`research.mean_sensitivity` implements one separate artificial cross-policy
adapter. It reads the recorded reference mean, while retaining the **full model's**
original native residual observations, as-of identities, missing masks and exact
tail/uncentered-moment inputs. Full errors are not relabeled as reference errors.
Mean source, residual-bank source and consumer identities are distinct; every
proposal/report says `artificial_uncertainty_proxy=True` and
`calibrated_reference_risk=False`. Both accepted own-risk consumers remain
unchanged and reject the cross wrapper.

`FullResidualBank` takes supplied bytes and explicit context, authenticates the
full seven-source closure and R46 evidence, restores the original typed full
ledger, and compares every declared cutoff's exact residual tuple, original IDs
and missing counts. The real scope binds the previously accepted R46 result and
audit hashes. It performs no market-file reads, new fitting or reference-error
rederivation. Preserved code bindings are required; test fixtures are explicitly
synthetic. No historical risk-bank qualification or replay follows from them.

The reference mean enters **before** the proportional precheck and economic
limit, native mean/lower receipt and sizing, and strict mean/lower-exit price and
price-dependent fee calculations. A fixed bank does not imply a fixed lower-exit
price, exposure, cash or realized portfolio risk. Original zero-missing and
30-observed gates, strict whole-share costs, free-cash shadow denominator and
account lifecycle are preserved. As-of selection uses the forecast information
cutoff, even if a label is known by later entry; future unavailable labels cannot
change earlier order instructions. Retrospective archive hashes are separately
labeled and excluded from causal forecast identity.

Optional pinned-code tests use the same `DELTA_FROZEN_CODE_ROOT` environment as
above. They exercise equal-input native/strict and account-trace parity through
partial fill, expiry, required exit and settlement; mean and bank perturbations;
valid gapped-source future-label cases; exact missing/availability boundaries;
both own-risk guard rejections; price/support/zero-moment domains and quantity
ties. All prices, labels and coefficients in those tests are invented.

This is disabled synthetic implementation only, not a historical runner. The
planned single-cell primary contrast is all-period liquidation-mark wealth
versus the preserved full/full policy. It is a conditional mean intervention
under an artificial uncertainty proxy, not calibrated risk or general alpha.
A second cross-cell would be needed for interaction/order sensitivity; Sharpe
contrasts are not additive accounting. No historical execution, adoption,
reserved-data release or live/external-paper activation is authorized here.
