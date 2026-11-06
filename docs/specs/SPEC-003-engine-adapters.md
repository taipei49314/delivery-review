# SPEC-003: pinned public engine adapters

Status: **adapter and isolated runner version 1 implemented; installed-CLI
controls observed on Windows/Python 3.12 and Linux/Python 3.11;
product/release qualification incomplete**. This is part of
the public 1.0 implementation plan. It neither relaxes the frozen contract-v1
release gates nor substitutes local scanner controls for E01–E18 integration.

The pure implementation is `src/delivery_review/adapters/engines.py`; the runner
is `src/delivery_review/engine_runner.py`. Execution, source acquisition and
final report composition are separate boundaries. No
adapter imports a detector's private Python functions, installs subject
dependencies, runs subject tests, or changes an engine rule to obtain a pass.

## 1. Exact candidate identities and redistribution

The authoritative byte lock remains `locks/engine-candidates.json`. Do not
silently select a newer package because a project page recommends it.

| Engine | Wheel version | Public JSON identity | License in verified wheel |
| --- | --- | --- | --- |
| Checkwash | 0.10.0 | `checkwash_findings_version: 2`; separate `checkwash_coverage_version: 1` | Apache-2.0 |
| Pinwash | 0.1.1 | `pinwash_findings_version: 1`, `run.spec_version: 29` | MIT |
| Ledgerwash | 0.5.0 | `ledgerwash_findings_version: 2`, `run.spec_version: 5`, `coverage.version: 1` | Apache-2.0 |

All three verified wheels require Python >=3.11 and have no mandatory runtime
package dependencies. Development extras are not installed by the product.
Retain the packages' license files and required notices if distributing their
bytes. This specification does not change the engines' own release claims or
declare them 1.0 products.

Primary package metadata is version-specific:
[Checkwash](https://pypi.org/pypi/checkwash/0.10.0/json),
[Pinwash](https://pypi.org/pypi/pinwash/0.1.1/json), and
[Ledgerwash](https://pypi.org/pypi/ledgerwash/0.5.0/json).
Their description text contains some older release instructions. The observed
interfaces below come from the exact hashed wheels and actual public CLI runs,
not from treating that prose as an execution receipt.

## 2. Isolated execution and source contract

Install only the approved wheel into an application-owned isolated environment.
Verify its archive SHA-256, METADATA identity and RECORD before installation;
record the interpreter, Git version, installation inventory and wheel identity.
The subject must not control the interpreter, environment, executable search
path, installed modules or output directories. Use an argument vector without a
shell. `-I -X utf8 -B -X pycache_prefix=<NEW_OWNED_CACHE>` is required, including
for Pinwash whose CLI uses its cwd as
the subject repository. Do not invoke `doctor`, `qualify`, `review`, hooks or
session commands while evaluating a PR.

The runner supplies an application-owned immutable Git object snapshot. It
verifies the full merge-base, head and base-tip SHA identities independently,
records their provenance, and prevents replacement objects, lazy fetching,
repository-controlled external programs and unsafe Git configuration. A
user-writable arbitrary repository is not established as a trusted execution
environment merely because a scanner describes itself as read-only. In
particular Ledgerwash's Git subprocess layer is not an independent process
sandbox and does not provide the outer timeout/output limits required here.

The supported invocations, after the absolute isolated Python executable and
`-I -X utf8 -B -X pycache_prefix=<NEW_OWNED_CACHE> -m`, are:

```text
checkwash check <MERGE_BASE>..<HEAD> --repo <OWNED_REPO> --format json --coverage-report <NEW_FILE> --fail-on high
pinwash check <MERGE_BASE>..<HEAD>
ledgerwash scan <OWNED_REPO> --at <HEAD> --adapter ec-ledger --fail-on high
```

Ledgerwash may additionally receive `--require-complete` when explicitly
selected by the operator. Pinwash cwd is `<OWNED_REPO>`. Checkwash's coverage
file is new, bounded, and outside the subject. Ref names, worktree comparisons,
three-dot ranges, stdin hooks and user-supplied extra CLI options are not accepted
by adapter version 1. A complete 40-character lowercase SHA is passed even when
the engine's native display abbreviates it.

The runner must stop the scanner and its ordinary child process tree on
timeout/output refusal, wait for its direct child and pipe readers, and record
bounded stdout/stderr prefixes before normalization. It must not infer
success from process creation, output-file existence, or JSON parseability.
Termination is `exited`, `timeout`, `output_limit` or `spawn_error`; the actual
native exit remains separate, including a negative signal exit when applicable.
Windows native exception codes can exceed the frozen report's maximum of 255.
Those remain unchanged in `process.exit_code`; the frozen projection uses null
and `report_exit_limit` with status error, without erasing a native block.

The implementation creates a new stdlib venv using an operator-approved Python
3.11 or 3.12 with `-I -S -m venv --without-pip --copies`. It installs only verified
regular wheel members directly; it runs no installer, build hook or subject
dependency. It rejects unexpected site-packages content, runtime dependencies,
startup `.pth`/customizer files, cached bytecode, unsafe paths, symlinks and
RECORD mismatches. Installed files are checked before and after execution. Each
run gets a new private cache directory and uses `-B`, so prior scanner bytecode
cannot replace verified source. Python and Git installations themselves are
operator trust roots, outside the subject.

The environment is an allowlist: no inherited tokens, proxies, Python options,
loader hooks or Git options. Git uses only the approved executable directory,
disabled global/system configuration, no credentials or interactive prompts,
no replacement objects and no allowed transport. The complete safe snapshot is
verified before and after execution; its operator-owned tree must not be changed
concurrently. Source identity and preserved bytes are not repository membership,
GitHub provenance, a signature, or proof against another process with the same
OS account. The current snapshot does not need a Git index.

Windows assigns a suspended child to a Job Object before its first instruction,
then resumes it. The job disallows breakaway and uses kill-on-close. Linux starts
a new session, kills its process group on refusal, and checks `/proc` until no
non-zombie group member remains; the direct child is reaped locally and orphan
zombies are left to the host init process. Other platforms fail as unsupported.
These controls bound the approved scanners and their Git children; they are not
a sandbox for an arbitrary hostile engine that deliberately escapes a POSIX
session. See the primary [Windows Job Object contract](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
and [Python process API](https://docs.python.org/3.11/library/subprocess.html#popen-constructor).

`run_engine(snapshot, *, engine_name, wheel_path, python_executable,
git_executable, destination, native_policy, limits=EngineRunLimits())` creates an
exclusive execution directory. Its result contains immutable `manifest_bytes`,
`raw_files`, and `observation_bytes`, with defensive parsed `manifest` and
`observation` properties. `execution.json` is written last. It inventories the
original streams, runtime/Git setup observations, verified installation inventory,
source inventory digest, native policy digest and normalized observation bytes.
Full native JSON may contain finite floats and is saved separately from the
integer-only manifest. Preflight or storage failure may leave diagnostic partial
files without a completed execution manifest. A failed post-execution source or
installation check instead seals an error observation with explicit
`controller_integrity_errors` and failed `after` integrity status. Native verdict,
findings, known block and process facts are retained; the frozen engine projection
is downgraded to error/not-judged. It must not continue to claim verified source
or suppress a block because another boundary failed. These local hashes are not
signatures.

Default overall subprocess deadline is 120 seconds, with a hard maximum of 300.
Retained stdout/coverage are each at most 2 MiB, stderr at most 1 MiB. Wheel
archives are at most 8 MiB, expanded members at most 32 MiB and 1,000 entries.
Local limits may only decrease these ceilings. A sidecar is monitored and the
tree is terminated if it exceeds its cap; only the bounded original prefix is
retained. This is not a filesystem disk quota: writes between polls can exceed
the cap temporarily. The deadline cannot interrupt the OS process-creation call
itself; cleanup is separately bounded. An unsupported process-control boundary
does not silently fall back to an unbounded subprocess.

Checkwash evaluates BASE configuration and exemptions; its explicit
`--fail-on high` fixes this profile's threshold without changing detector policy.
Pinwash `check` has no threshold or repository option: its public native
threshold is fixed to high. Its BASE-side allowlist, required contexts and hook
bodies remain authoritative. A requested outer threshold cannot be represented
as though Pinwash accepted it. Version 1 accepts `native_policy.threshold: high`
only. Other outer policy decisions remain separately recorded.

Retain the relevant BASE config/allow blobs, HEAD changes to those files, their
paths, hashes and policy-basis SHA in the source/execution capture. The scanner
comparison basis is merge-base, not the current base tip. A requirement for
newer base-tip policy must produce a policy-basis gap rather than silently
replacing the engine's BASE behavior.

Both engines' exemption handling depends on the evaluation date. The exact
candidate implementations read `CHECKWASH_TODAY` and `PINWASH_TODAY`; Pinwash's
package documentation describes its override. The controlled runs set both to
`2026-11-06`. An execution receipt records the chosen UTC policy date and these
explicit overrides. Do not inherit unrelated host overrides or recompute
exemptions at replay time. Checkwash's override is a version-pinned implementation
behavior and requires a regression check when changing that candidate.

## 3. Pure normalization interface

```python
normalize_engine_result(
    *, engine_name, engine_version, wheel_sha256,
    subject={"merge_base_sha": ..., "head_sha": ..., "base_tip_sha": ...},
    process={"exit_code": ..., "termination": ..., "argv": [...], "duration_ms": ...},
    raw_files={"stdout": b"...", "stderr": b"...", "coverage": b"..."},
    native_policy={"threshold": "high", ...},
) -> dict
```

`coverage` is the Checkwash sidecar and is optional in the byte inventory so that
its absence can be reported as an error. The parser performs no filesystem,
subprocess, clock or network operations. It accepts only the three exact pins
listed above. A caller must preserve raw bytes before calling it.

Controller input errors raise `EngineAdapterError` without echoing input data.
Recorded engine failures return an observation instead of destroying the raw
diagnostics. Reader hard limits are 2 MiB stdout, 1 MiB stderr, 2 MiB sidecar,
64 argv items of at most 4096 characters, JSON depth 32 and 100,000 JSON nodes.
The runner must enforce at least these limits before buffering. UTF-8 BOM,
duplicate JSON keys, non-finite numbers and malformed UTF-8 are rejected.
Finite native floating-point quantities are preserved; fields declared to be
versions, counts, offsets or identities remain strictly typed and do not accept
booleans or floats as integers.

The result contains:

- `engine`: the exact frozen contract-v1 engine shape. Its `evidence_id` starts
  as null and must be linked by the report composer; a standalone projection is
  not a complete report.
- `parsed_native` and `parsed_coverage`: complete parsed documents, or null if
  decoding failed; there is no prose-based verdict parser.
- `findings`: each representable frozen finding plus its complete native row.
  All original rows, including unrepresentable ones, remain in `parsed_native`.
- `raw_inventory`: exact byte sizes and SHA-256 values for each captured stream.
- Original subject/process/native-policy copies, adapter version, reasons,
  `known_block` and `review_required`.

`known_block` records an observed native block or an emitted blocking finding;
it is not source authentication or permission to satisfy a scanner obligation.
Thus a timeout or malformed companion output can coexist with a retained block.
The composer must preserve that observation while leaving required processing
or source obligations incomplete/error. `review_required` does not change a
native pass into a fabricated native `held` result.

The projection uses `completed` only for supported, consistent native
`exit 0 / pass` or `exit 1 / block`. Errors use `error / not_judged`; raw native
verdicts remain visible. An unknown version, source/argv mismatch, contradictory
exit/verdict/counts, missing required sidecar or configuration error cannot be
normalized into pass. A ledger with no supported records is `not_provided /
not_judged`, while its original exit/pass remain retained.

Frozen limits are not silently widened: more than 1000 findings, an overlong
fingerprint, or coverage details not representable by report-v1 result in an
explicit compatibility/limit error. Never shorten a fingerprint, discard native
findings to force acceptance, change a severity or rewrite the original verdict.

## 4. Observed public JSON contracts

### Checkwash 0.10.0

The findings document has `checkwash_findings_version`, `run`, `findings`,
`summary`, `skipped_files`, `config_errors` and `verdict`; `unread` is optional.
`run` names the engine version and abbreviated base/head SHAs. The observed
seven-hex displays are retained verbatim. Full source binding comes from the
fixed invocation and independently verified object snapshot; the adapter checks
that the native displays match those pins, and never promotes an abbreviation
to a new independently observed full SHA.

Native findings retain rule, severity, message, path, unit, before/after evidence,
fingerprint, escalators, deescalators, `allowlisted`, and the nullable
strength/subject/shape fields. The frozen finding projection preserves
fingerprint/rule/severity and derives only its disposition. A valid BASE
exemption was observed as `allowlisted: true`; the native row remains emitted.
HEAD-only exemption additions were observed without exempting the weakening.

The separate sidecar contains its own version/run, scope
`javascript_assertion_candidates`, files, gaps and `no_known_gaps` or
`incomplete` status. It must match the findings run exactly. This is a bounded JS
candidate inventory, not proof of complete JavaScript, Python or whole-program
coverage. A gap can coexist with `exit 0 / pass`. Preserve skipped files, unread
sides and configuration diagnostics independently.

An oversized analyzed test file was observed to exit 2 with stdout containing
only `unread`, including path/side/size/limit. This is deliberately not a findings
document: it has no findings version or verdict. A missing commit produced exit
2 and no JSON. Neither may be parsed as an empty pass.

Checkwash #419 remains a disclosed detector limitation. This adapter does not
repair it, claim semantic correctness from pass, or mark its E18 integration
variant passed from the synthetic controls below.

### Pinwash 0.1.1

The findings-v1 document has `run` with full base/head SHA, engine/spec versions,
`findings`, `summary`, `config_errors`, `unknown_coverage` and `verdict`. Finding
rows preserve rule/severity/message/path/before/after/fingerprint and optional
locator. It evaluates a fixed diff, not complete agent-session history.

A newly unparseable surface was observed as `unknown_coverage` with exit 0/pass.
An invalid-UTF-8 BASE allowlist was observed as a nonempty `config_errors` list
with exit 0/pass. The latter becomes adapter error while its native pass is
retained. Missing commits produce exit 2 with stderr and no JSON.

Pinwash applies valid BASE exemptions by omitting those findings from its public
JSON. The installed CLI control confirmed this; unlike Checkwash, it does not
emit an `allowlisted` row. Preserve the native omission and separately retain the
BASE allowlist bytes. Do not invent the missing pre-exemption finding, import a
private detector, or rerun with a modified policy and call that the original
result. An `EXEMPTION_ADDED` row yields `added_exemption` and an outer human-review
requirement, while the native severity/verdict remain unchanged.

### Ledgerwash 0.5.0

The only candidate adapter is `ec-ledger`. Its schema/layout is optional support,
not an EC runtime dependency or a universal ledger format. A repository without
this format must not be required to adopt it to use the public GitHub product.
An operator-supplied unsupported ledger format remains `unsupported` at the
selection boundary; it must not be rewritten into an empty EC ledger.

Findings v2 includes `run`, `verdict`, `finding_verdict`, `coverage`, `findings`,
`observations`, `residuals` and `summary`. The run retains full epoch/ref SHA,
adapter, target, engine/spec version and `require_complete`. Coverage v1 retains
all checks/counts, scope, limitations and its independent `complete` boolean.
Observed limitations explicitly exclude execution authenticity, trusted time,
legacy/migration binding and evidence outside this adapter's scope.

Default no-ledger and incomplete-task scans were observed as exit 0/pass with
coverage incomplete. With `--require-complete`, those same inputs exited 1/block
while `finding_verdict` remained pass. A malformed ledger record yielded the
native `RECORD_UNPARSEABLE` high finding and block. Preserve all three distinctions;
an empty findings list never proves that a ledger was provided or verified.

## 5. Controls and qualification evidence

The local scratch directory `work/engine-qualification/` is ignored and contains
verified wheels, isolated environments, extracted source used for inspection,
authored Git fixtures, exact stdout/stderr/sidecar bytes and per-stream hashes.
It contains no private customer evidence. `inspect_wheels.py` reused the existing
bounded official-host byte verifier; archive and inventory digests matched the
approved lock for all three candidates. `probe_cli.py` and
`probe_exemptions.py` executed the public installed CLIs without importing engine
internals. `qualify_adapter.py` re-executes those authored controls with the fixed
argv and normalizer, retaining earlier probe attempts unchanged.

Observed host: Windows 11 build 26200, Python 3.12.10, Git
2.55.0.windows.3. The initial probes comprise 28 CLI invocations, including
version/help, clean and blocking diffs, HEAD policy weakening, BASE/HEAD
exemptions, invalid configuration, unknown coverage, oversized input, absent and
malformed ledgers, strict coverage and missing commits. These are constructed
controls learned during implementation, not held-out accuracy measurements or a
qualified public end-to-end delivery chain.

The checked-in unit controls use authored synthetic bytes and verify:

1. Exact candidate/version/argv/source identity, frozen projection compatibility
   and full native preservation.
2. Exit 2, timeout/output-limit/spawn errors, unsupported schemas, malformed UTF-8,
   duplicate keys, non-finite numbers, depth and byte refusal.
3. Blocks retained through other failures; summary/exit/verdict contradictions;
   separate finding, coverage and readiness meanings.
4. Native BASE exemptions and HEAD additions, missing/mismatched coverage
   sidecars, pass-with-gaps, pass-with-config-errors and ledger absence/strictness.
5. No file/network/process IO during pure mapping and no input mutation.
6. Fresh wheel installation and RECORD verification, cwd module-shadow refusal,
   source/self-mutation refusal, exact byte retention, stdout/stderr/sidecar caps,
   process-tree termination after the parent has exited, and clean spawn failure.

The actual `probe_runner.py` controls additionally materialize an authored object
snapshot, execute all three unmodified locked wheels in fresh no-pip environments,
and preserve every raw stream and execution manifest. On Windows/Python 3.12 and
Linux/Python 3.11, assertion removal produced Checkwash block, an unpinned action
produced Pinwash block, and no ledger produced Ledgerwash native pass with
normalized `not_provided`. No PR tests or fixture workflow commands were executed.
Linux controls use native Linux storage: the WSL `/mnt/c` mount did not preserve
the non-executable file mode required by SPEC-005 and was correctly refused.

Before public qualification, record a clean immutable product source and fixture
source, reproduce the locked installation and controls on every declared
platform, validate the bounded runner and source-acquisition boundary with actual
process/object attacks, and run the relevant E05/E11/E12/E13/E15/E18 variants.
The Linux/Python 3.11 local runner controls are still not the frozen end-to-end
integration qualification. Independently reproduce
the candidate rather than treating this author's local scratch results as the
release gate. No frozen expected outcome or integration status is changed here.
