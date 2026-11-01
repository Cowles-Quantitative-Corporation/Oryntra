# Delta engineering research

Canonical source for the checked-integer reported-volume worker, exact Python oracle, unknown-side uncertainty intervals and disabled artifact projector. No decoder/data ingestion route, trading route, account adapter or model activation is installed. `model_vector()` remains disabled.

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
