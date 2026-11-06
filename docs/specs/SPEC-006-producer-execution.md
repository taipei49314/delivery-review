# SPEC-006 â€” isolated pytest producer execution

Status: **fixed `3d3e545` head/merge workflows qualified after actual hosted controls and independent review**.
This implements the test/signer separation required by
[SPEC-004](SPEC-004-artifact-provenance.md). Implementation and publication alone
do not establish producer qualification or E01-E18 product acceptance. The verifier
never invokes this producer against a reviewed project. Only an explicitly
selected, approved producer workflow may execute project tests in the guest.

## Scope and inputs

The first execution profile is `pytest-isolated-container-v1`, Python 3.11,
pytest 8.4.2, `pytest-8.4.2-xunit2`, with no extra project packages. Two separate
fixed workflows select `pr_head` or `github_merge`; each exact workflow path,
full revision and permitted source mode needs its own qualification.
Project install hooks, dependency manifests and caller-supplied command lines
are not used. The published protocol fixes the test selector to literal `tests`; the reusable
workflow accepts no caller inputs. The generic local qualification driver may
select one existing ASCII relative file or directory, validated as one argument.
Pytest options, traversal, absolute paths and node-ID syntax are unsupported.
Changing the standard selector requires a versioned profile and qualification.

`producer.host.execute_pytest(snapshot, *, runtime, image_digest, wheelhouse,
test_path, limits)` takes a verified `SourceSnapshot`, a hash-pinned local Docker
CLI, an explicit local Unix daemon socket and an empty Docker config directory.
The immutable local image configuration ID is required. Published OCI digest
resolution and approval are separate release work; a local ID does not claim a
reviewed registry publication. The daemon, kernel, image and producer scripts
are trusted operator inputs. These are containers sharing a kernel, not a VM
boundary against a compromised kernel or daemon.

`producer/requirements.txt` and `producer/wheels.json` approve exactly six
universal wheels: pytest 8.4.2, iniconfig 2.1.0, packaging 25.0, pluggy 1.6.0,
Pygments 2.19.2 and colorama 0.4.6. The downloaded wheel bytes are independently
SHA-256 checked against these pins. No wheels are committed. The existing
verifier/development locks are unchanged. Replacing these producer pins requires
another profile qualification, including pytest's native XML dialect.

## Execution boundary

The host validates the snapshot before execution and again afterward. Snapshot
files are intentionally owner-readable only. The host therefore stages only
inventory-selected, size/hash-checked source bytes with read-only permissions;
it never changes original snapshot permissions and never copies `.git` into the
guest's source. The guest independently checks these staged hashes and copies
the files into its private working volume before running tests. Source staging
does not authenticate GitHub repository membership; that remains a separate
platform/provenance check.

Both the test container and a separate fixed keeper use:

| Control | Fixed/default value; callers may only lower numeric caps |
| --- | --- |
| User/group | `65532:65532` |
| Network / IPC | `none` / `none` |
| Root filesystem | read-only |
| Linux capabilities | all dropped |
| Privilege escalation | `no-new-privileges` |
| Seccomp | Explicit hash-pinned Moby default profile; no daemon-default or unconfined fallback |
| Processes | 64 |
| Memory / memory+swap | 512 MiB / 512 MiB |
| CPU | one CPU |
| Open files | 256 |
| Per-file size | 8 MiB |
| Private work volume | local-driver tmpfs, 256 MiB, UID/GID 65532, mode 0700 |
| Setup / test / each control request | 60 / 120 / 15 seconds |
| Combined stdout+stderr per phase | 1 MiB |
| JUnit raw bytes | 2 MiB |
| Docker persistent logging | `none` |

The tmpfs capacity is an aggregate kernel-enforced size cap, independent of
individual file limits. The work volume contains the copied source, virtual
environment, temporary files and output. A smaller cap may make setup fail;
there is no retry using a larger, unbounded writable location.

Source, approved helpers, wheelhouse and host-generated configuration are
read-only bind mounts. Recursive source submount inclusion is disabled. There
is no writable host bind, Docker socket, host PID/IPC namespace or host
credential environment. Docker CLI environment is allowlisted and does not
inherit GitHub/OIDC credentials, Docker context overrides, loader injection or
proxy settings. The daemon socket is used only by the trusted host driver.
Container inspection must confirm the requested image, non-root user, resource
caps, restricted mounts, fixed command and hardening before tests can start.
The bundled `producer/seccomp/moby-default.json` is unchanged from Moby commit
`4b8214fc4971e06172938a1f1554ccff6647ee80`, SHA-256
`6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6`. Its
Apache-2.0 license and source inventory are included. The host checks the bytes,
passes a private read-only copy and requires the daemon inspection to report
the exact JSON profile. A daemon default that merely reports seccomp mode 2
cannot satisfy this check. Before setup and tests, the trusted guest checks
Linux amd64, UID/GID, effective/permitted/inheritable/ambient capabilities,
NoNewPrivs, seccomp mode, actual cgroup v2 memory/swap/PID/CPU limits, tmpfs
capacity and filesystem/file-descriptor resource limits.

The fixed setup command creates a fresh venv and invokes pip with
`--no-index --no-deps --require-hashes --only-binary=:all:` against only the
approved wheelhouse and trusted requirements. A separate fixed exec starts
pytest, preserving its native process exit independently of setup. Python
isolated mode, disabled plugin autoload and the explicit trusted pytest config
prevent caller environment/config discovery from selecting plugins or changing
the JUnit output mode. Project `conftest.py` and tests remain untrusted executable
code; their execution is the purpose of the guest, not something the verifier
does. The producer does not claim tests prove correctness or prevent project
code from fabricating its own test assertions/results.

The pytest command is fixed except the validated relative selector:

```text
/work/venv/bin/python -I -m pytest -c /trusted/pytest.ini
  --rootdir=/work/source --junitxml=/work/output/junit.xml --strict-markers TEST_PATH
```

The host captures output into bounded byte buffers. It never prints raw guest
stdout/stderr, so strings resembling GitHub workflow commands are not submitted
to the Actions log command processor. Docker logging is disabled to prevent a
second, unbounded daemon log sink. Timeout/output-limit observations carry a
null native exit, not a fabricated pytest result. The host kills the entire
Docker-client process group even after the direct client exits; remaining
descendants are an explicit error. A killed Docker client does
not imply the guest stopped: the host explicitly kills and inspects the guest.

## Stop, copy and cleanup

The keeper executes only a fixed isolated Python sleep command, reads no guest
data and mounts the private tmpfs read-only. Its active mount keeps the tmpfs
available after stopping the test container. This is necessary because normal
container tmpfs mounts disappear when the container stops. The keeper has no
source, wheelhouse, host configuration or credentials mounted.

Only after Docker reports the test container stopped, PID zero, unpaused and
not restarting does the host request
`docker cp KEEPER:/work/output/junit.xml -`. No `-L` link-following option is used.
The bounded in-memory reader accepts exactly one regular USTAR member named
`junit.xml`, verifies its header checksum and size, rejects privileged mode
bits, links, extra members, extra nonzero bytes, prefixes, alternate paths and
unsupported TAR extensions, and returns the original file bytes. It never
extracts into the host filesystem. Parser resource errors or inconsistent
native counts cannot be changed into successful coverage.

The host removes both containers and the tmpfs volume in a `finally` path.
Unconfirmed stop prevents copying. Unconfirmed cleanup remains an explicit
error. A daemon crash or unresponsive privileged daemon cannot be repaired by
claiming cleanup succeeded; the operator must remove remaining resources before
the runner can be reused. An approved hosted implementation must isolate or
discard such a runner rather than signing a successful producer result.

Docker documents the local volume driver and tmpfs options in its
[volume-create reference](https://docs.docker.com/reference/cli/docker/volume/create/).
The [copy reference](https://docs.docker.com/reference/cli/docker/container/cp/)
documents TAR streaming to `-` and explicit symlink-following behavior. These
capabilities were also exercised locally; they are not inferred only from flags.

## Observations and receipt

`ProducerExecution` keeps immutable source inventory bytes, image ID, exact
command/selector, separate setup/test process observations, original JUnit
bytes, parser observation bytes, explicit error codes and stopped/cleaned flags.
The parser retains native suite counts, case counts and native failure/error
records. Zero tests and all skips remain explicit. Duplicate case identities or
contradictory native counts remain `valid: false`; there is no repair to match an
expected green result.

`producer.receipt.build_receipt(execution, *, source_claim, producer_identity,
recorded_at, receipt_id)` runs on the host after execution. It checks the source
claim against the measured checkout snapshot, hashes the original JUnit bytes
and preserves setup/test exit observations. `collection_exit` is null because
no independent collect-only process ran. The builder refuses unconfirmed stop
or cleanup, any boundary error, unsuccessful setup, absent native test exit,
missing JUnit, invalid parser observations or mismatched raw/parsed bytes.
Out-of-dialect native test exits (outside pytest's 0â€“5 range) and observed
container OOM termination are also explicit incomplete-execution errors.
These incomplete measurements remain in `ProducerExecution`; they cannot be
laundered into an uploadable receipt that omits the boundary errors. Complete
native pytest failure, zero-test and all-skip observations can be recorded and
retain their original exits/counts. This is not a readiness decision.

The result remains `producer_declared`. Supplied repository/PR/run/attempt/job
IDs are claims until the independent platform and signed-provenance checks in
SPEC-004 bind them. The receipt cannot alone prove authentic upload artifact ID,
ZIP digest, job identity or reusable workflow SHA. The GitHub upload/attestation
stage must refuse any infrastructure, stop, cleanup, missing-output or parser
invalid error as complete evidence. A real measured pytest failure can still be
retained as a failed execution, never rewritten as unavailable success.

## Local development qualification

The local Docker candidate uses the fixed base
`python@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b`
(Python 3.11.16, Linux amd64) and a dedicated 3 KiB build context containing only
the producer Dockerfile. Its local image ID is
`sha256:3c3f4f8134ac77cff7853438f5bb826189457bdf5cadfd73fa65cf2fd80abc3f`.
Docker CLI 29.9.3 and its download/binary hashes are retained under ignored
`work/docker-bootstrap/`. Local input sources are entirely authored fixtures,
materialized from authored Git objects into Linux temporary directories.
DrvFS snapshots were correctly refused by the snapshot executable-file gate;
the gate was not weakened to make that environment pass.

The probe record is under ignored `work/producer-qualification/`, with original
phase stdout/stderr bytes, XML, source inventory digest and native observations.
The reproducible driver is `producer.qualification.qualify(destination, *,
runtime, image_digest, wheelhouse, git_executable, variants=None)`. It accepts
only its built-in authored fixtures, creates a new destination, saves raw logs
without printing them and records producer source hashes. It makes no automatic
release PASS decision and is not invoked by default unit-test discovery.
The normal result and boundary control both completed with setup/test exit 0,
valid one-test XML, confirmed stop and cleanup. The boundary fixture observed
UID 65532, no host GitHub/OIDC variables or Docker socket, rejected writes to
source/helpers/wheels/rootfs, blocked external networking, zero effective
capabilities, `NoNewPrivs: 1` and seccomp mode 2. The subsequent boundary run
also read actual cgroup `memory.max=536870912`, `pids.max=64`,
`cpu.max=100000 100000`, and a 256 MiB tmpfs filesystem capacity.

| Authored control | Observed native result |
| --- | --- |
| Assertion failure | setup 0, pytest 1, one retained failure |
| All skipped | setup 0, pytest 0, one retained skip and no passed tests |
| Zero tests | setup 0, pytest 5, retained `zero_tests` observation |
| Test timeout | null native test exit, `test_execution_timeout`, no report invented |
| Excessive stdout | null native test exit, `test_output_limit`, bounded retained prefix |
| 64 MiB tmpfs fill | kernel `ENOSPC` observed; authored negative control removed its files and passed |
| Replace JUnit with symlink at process exit | native pytest 0 retained; `unsafe_junit_tar`, no target bytes retained |
| Passing call with teardown error | pytest 1; native tests 2 versus one case retained as invalid |
| Failing call with teardown error | pytest 1; duplicate case identities and native count disagreement retained as invalid |

All listed executions confirmed the guest stopped and all resources cleaned.
The detailed second probe run is `run-1793942380883803765`; the earlier happy
and boundary run is `run-1793942303626425490`; kernel-limit and native teardown
observations are in `run-1793942541238422744`. The final host adds the explicit
`invalid_junit_observation` error alongside those preserved native records;
the actual rerun is `run-1793942654270188804`. After the descendant-cleanup and
receipt gates changed, the normal, kernel-boundary and timeout executions were
repeated successfully in `run-1793942773937665585`.
The repository qualification driver itself was exercised for normal, boundary
and report-symlink controls in `repro-1793942951334715197`.
No raw guest output was forwarded
to the host console. Focused unit controls additionally cover TAR checksums,
truncation, extra members, unsafe paths, hard links, special files, output caps,
credential environment stripping and receipt source/exit/count preservation.

These observations cover only the recorded local development slice. Subsequent
public workflow, artifact and attestation observations are recorded separately
below; the local records do not substitute for those hosted controls.


## Hosted reusable workflow candidate

`.github/workflows/pytest-producer.yml` and `pytest-producer-merge.yml` are
`workflow_call` only and preserve the
separate EC product CI route. It accepts no caller commands, source refs, test
selectors, job IDs, artifact identities, images or secrets. It targets an
Ubuntu 24.04 GitHub-hosted Linux amd64 runner and Python 3.11.16. The supported
event is a same-repository `pull_request`. Forks, Dependabot's distinct OIDC
`dynamic` event, `pull_request_target`, custom merges and other execution modes
are refused. Publication and attempted hosted executions are recorded separately;
neither makes an exact candidate revision hosted-qualified without its evidence.

The inline bootstrap is byte-checked against `producer/bootstrap.py`. Before
checking out any repository, it requests OIDC from the trusted runner's fixed
GitHub Actions HTTPS endpoint, using the runner token only in the HTTP header.
It disables proxies/redirects, bounds bytes/time, validates issuer, audience,
expiry, hosted runner, event, repository/run/attempt/check identities and exact
`job_workflow_ref`/`job_workflow_sha`. The reusable workflow must be called at a
full SHA, and the producer repository must still have numeric ID 1438021135.
Only that SHA is exported for checkout of `taipei49314/delivery-review`.
Decoded OIDC is an observation of an authenticated platform response, not an
independent JWT signature verifier or transferable verifier proof. The JWT,
request bearer and endpoint locator are never saved or printed. The downstream
verifier independently verifies the Sigstore certificate and signed statement.

The host environment is created outside any reviewed source. Its dependencies
are exact wheel hashes from `uv.lock` in `producer/host-requirements.txt`; pip
uses isolated mode, no dependency resolution, binary-only and required hashes.
Only these trusted host packages are installed. The trusted host downloads the
pinned Docker 29.9.3 distribution and pytest wheelhouse, verifies bytes, then
builds the fixed Dockerfile using only a trusted one-file build context and
`--network=none`. The base image is digest-pinned; the resulting local image ID
is measured for each run. It is not claimed to be a published/reproducible image.

`producer.platform_identity` preserves original bounded GitHub API bodies and
cross-checks repository, PR, run, exact attempt, complete job pagination and
check metadata. It matches the OIDC check ID through the job's explicit
`check_run_url`; job and check IDs need not be equal. It verifies GitHub Actions
App ID, suite, exact job URL, full reusable workflow SHA and same-repository
head/base identity. Under [ADR-004](../amendments/ADR-004-github-merge-reference.md),
the host additionally reads only the fixed
`/repos/{repository}/git/ref/pull/{pr_number}/merge` route and retains its original
body. The exact returned ref, commit type, full SHA and repository URLs must
match the OIDC event identity; a missing field in the newer PR payload is not
replaced by a guessed source. The deprecated PR merge field is ignored, even if
present; reference/OIDC conflicts still refuse execution. The event's OIDC SHA
is separately obtained as a canonical
Git object and must have exactly `[base_tip, pr_head]` parents. The head workflow
tests `pr_head`; the fixed merge workflow tests that actual merge snapshot.
Original canonical commit bytes and independently measured source inventory
bind the receipt's checkout and ordered parents. Neither a supplied parent list
nor the certificate event SHA alone establishes what the guest tested. The
existing execution profile and custom predicate remain version 1; the bundled
registry must separately qualify the exact path/revision and permitted mode.

After the fixed guest stops and cleanup is confirmed, the host writes only
`receipt.json` and the original `junit.xml` into a fresh upload directory. The
artifact name is `delivery-review-{run_id}-{run_attempt}-{check_run_id}`.
The pinned upload action uses no overwrite and compression level zero. The
latter keeps the two small evidence members within the qualified archive ratio
bound even for repetitive XML. Actual upload ID/digest outputs are passed through
environment variables to the host, never interpolated into shell source.

Before signing, the host obtains a new OIDC observation and rechecks platform
identity, including the exact merge reference. A moved reference or incompatible
base/head relationship refuses signing of the earlier measurement. It downloads the actual upload ID, independently hashes its ZIP and
checks platform digest plus exact receipt/JUnit bytes against the pre-upload
inventory. Only then does it write the strict custom predicate and invoke pinned
`actions/attest` with the actual artifact URL and ZIP digest. A fully measured
pytest failure is uploaded and signed, then the last fixed host step preserves
the failure in job status. Incomplete boundary/parser/cleanup observations cannot
produce an uploadable receipt. Raw guest output is captured as bytes and never
sent to the Actions command processor.

Platform acquisition preserves bounded original response bytes and safe metadata
before rejecting a failed HTTP observation. Host diagnostics expose only fixed
stage/route labels, allowlisted error codes and validated HTTP status or its absence;
they do not print API bodies, credentials, arbitrary exceptions or guest text.
These local failure captures are not uploaded as successful test evidence.

The pinned action sources and the OIDC claims used by this candidate are
[GitHub's OIDC reference](https://docs.github.com/en/actions/reference/security/oidc),
[upload-artifact action interface](https://github.com/actions/upload-artifact/blob/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a/action.yml), and
[attest action interface](https://github.com/actions/attest/blob/daf44fb950173508f38bd2406030372c1d1162b1/action.yml).

## Explicit-profile development rerun

The original task-local WSL daemon disappeared before the next turn. A rerun
retained four unavailable/control-failure observations under
`work/producer-qualification/repro-1793943610901207820`; none is a pass. A new
private daemon and fixed trusted build produced local image
`sha256:704807b81581d84512da5d503fdf8df19a62e79b4ea1ea1c3a746c0ef8e8f8d5`.
With the explicit seccomp profile and guest resource preflight, the normal and
boundary controls returned setup/test 0 and confirmed stop/cleanup. Symlink
JUnit was refused despite native pytest 0. Abrupt exit 137 retained that exit
and incomplete-output reasons. Original bytes are under
`work/producer-qualification/repro-1793943745833474528`. This remains a local
uncommitted-source feasibility record, not hosted or stable-release acceptance.

A subsequent boundary-only run also invoked the Linux amd64 `io_uring_setup`
syscall and observed the pinned profile's `EPERM` denial, together with the
existing UID/cgroup/tmpfs checks. It returned setup/test 0 with confirmed stop
and cleanup; its expanded raw inventory, timestamps and recursive producer
source hashes are in `work/producer-qualification/repro-1793944527488450750`.
Twenty-eight focused producer controls pass on Windows/Python 3.12 and
Linux/Python 3.11.16. Actionlint 1.7.12 accepted the reusable workflow with no
diagnostics; its downloaded executable/archive hashes and original output are
under ignored `work/actionlint-1.7.12`. These are local checks, not a real OIDC,
upload or signing run of this candidate.

Independent review tightened three contradictory-identity cases: an explicitly
reported job attempt must match the selected attempt, an in-progress check may
not carry a completed conclusion, and duplicate references to the producer path
are refused before comparing the approved SHA. Focused controls for these
cases pass on both local platforms. The fixed static Docker client also built
the trusted one-file context successfully through the production driver's
minimal environment without an installed buildx plugin; raw observations are
in `work/producer-qualification/host-build-1793944680905740552`. That local
legacy-build result did not establish compatibility with the hosted Buildx backend.

## Actual public hosted controls at 3d3e545

Exact producer revision `3d3e54599066c98f52384c66953b130a01eaaff8` was exercised
in the public first-party
[reference repository](https://github.com/taipei49314/delivery-review-reference).
Each control uses a separate immutable base/head pair; earlier failures remain
unchanged. Its build uses a separate Docker configuration directory because an
actual reproduction showed that successful Buildx 0.38.1 builds leave state
there. The execution directory is newly created and must still pass the
unchanged empty-configuration gate.

| Public control | Actual observation |
| --- | --- |
| [PR4 head](https://github.com/taipei49314/delivery-review-reference/pull/4), [PR8 merge](https://github.com/taipei49314/delivery-review-reference/pull/8) | Native pytest 0, actual upload binding and custom signing succeeded. Merge retains its distinct checkout SHA and ordered base/head parents. |
| [PR5 zero](https://github.com/taipei49314/delivery-review-reference/pull/5), [PR6 all skipped](https://github.com/taipei49314/delivery-review-reference/pull/6), [PR7 failure](https://github.com/taipei49314/delivery-review-reference/pull/7) | Signed original measurements retain native exits 5, 0 and 1 and their original counts. Zero/failure jobs finish failed after preserving evidence. |
| [PR9 collection error](https://github.com/taipei49314/delivery-review-reference/pull/9), [PR10 partial skip](https://github.com/taipei49314/delivery-review-reference/pull/10) | Genuine collection-error XML retains exit 2; partial skip retains one passing and one skipped case with exit 0. Tests are identical in each base/head pair. |
| [PR11 head boundary](https://github.com/taipei49314/delivery-review-reference/pull/11), [PR12 merge boundary](https://github.com/taipei49314/delivery-review-reference/pull/12) | Signed passing assertions exercise UID, exact runtime, seccomp syscall denial, capabilities, cgroups/tmpfs, read-only mounts, absent host credentials/files/socket and blocked external networking. |
| [PR14 symlink](https://github.com/taipei49314/delivery-review-reference/pull/14), [PR15 abrupt exit](https://github.com/taipei49314/delivery-review-reference/pull/15), [PR16 output cap](https://github.com/taipei49314/delivery-review-reference/pull/16), [PR17 timeout](https://github.com/taipei49314/delivery-review-reference/pull/17) | Each reached isolated execution and then receipt refusal; complete artifact inventories contain no upload. Timeout took approximately 125 seconds through the fixed 120-second test deadline and cleanup. |
| [PR18 tmpfs exhaustion](https://github.com/taipei49314/delivery-review-reference/pull/18) | The kernel returned ENOSPC; the authored test removed its files and produced a signed passing boundary observation. |
| [PR26 forged output/background writer](https://github.com/taipei49314/delivery-review-reference/pull/26) | Native 0 and one signed passing case. Forged workflow-command, fake upload/private-receipt and delayed-writer markers are absent from the Actions log and uploaded members. Earlier PR13 stopped at source acquisition before this test ran. |
| [PR19 artifact replacement](https://github.com/taipei49314/delivery-review-reference/pull/19) | A separate caller job preserved and downloaded original evidence before deleting only its own new run's artifact. Reupload produced the same name and exact ZIP bytes with a new artifact ID. |
| [PR20 artifact precreation](https://github.com/taipei49314/delivery-review-reference/pull/20) | Original inventories retain caller-created and producer-created artifacts under the same name with different IDs. The immediate caller metadata read returned 404 and remains recorded. The later producer upload succeeded: overwrite:false did not establish cross-job name uniqueness. |

The lifecycle controls leave PR4/PR8 positive artifacts untouched. A name or
matching ZIP digest cannot replace authenticated artifact ID and unambiguous
platform association; a new ID must not inherit an old signed predicate.

Original APIs, safe transport metadata, logs, ZIP/member bytes and signed
bundles are retained under `work/hosted-reference/run-<id>/`. The consolidated
mapping is `work/hosted-reference/3d3e545-controls-summary.json`; authoring and
publication receipts are under `work/reference-authoring-3d3e545*`. Independent
official-CLI verification, wrong-identity controls and blocked-network replay
are separate dossier records. Controlled binding checks are not hosted attacks.

Closed hosted refusal logs do not expose runner-local execution details.
Hosted stop/cleanup and GID checks are enforced by the reviewed fixed producer
before signing; no separate retained hosted post-cleanup Docker inspection is
claimed. Direct local stop/cleanup observations remain labeled local. Independent
review of the complete dossier admitted only the two exact fixed paths at this
revision. The [digest-bound receipts and original public archive](../../qualification/README.md)
retain the review scope and failures. Fresh complete-product acceptance and
stable-release qualification remain separate.
