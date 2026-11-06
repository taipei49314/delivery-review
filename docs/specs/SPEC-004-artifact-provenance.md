# SPEC-004 — artifact/job provenance through an isolated approved producer

Status: **design selected; bounded official-verifier adapter implemented and
upstream signed-fixture controls observed on Windows/Linux; producer and product
qualification NOT_RUN**. This extends [SPEC-002](SPEC-002-pytest-producer.md)
without relaxing contract-v1 `workflow_bound` or changing frozen receipt/report
schemas. Research was read-only, using public data and upstream test vectors.
No workflow, attestation or release was published; no reviewed PR code ran.

The selected route is an official Sigstore attestation from a pinned, isolated
reusable producer. It signs the actual upload's artifact ID and ZIP digest,
together with an authenticated producer assertion of job/check identity. The
verifier independently checks platform metadata and the exact signed certificate
identity. A successful `gh attestation verify` result alone is insufficient:
the product-specific associations and approved execution contract must also
match. Numeric job identity is **not** represented as a native Fulcio claim.

## 1. Why platform log text is not the selected proof

The read-only probe downloaded
`GET /repos/taipei49314/checkwash/actions/runs/41913448234/attempts/1/logs`.
The public response was a 41,288-byte ZIP with SHA-256
`c3bf27c339ec677e946592c697282e21058a9a431e85b444a9dacc9c57d8fd26`.
Its complete member list was:

```text
0_publish-pju-checks.txt
publish-pju-checks/system.txt
1_intake.txt
intake/system.txt
2_verify-pju-receipt.txt
verify-pju-receipt/system.txt
```

There were no platform-separated upload-step files. `intake/system.txt` contained
platform job scheduling and a workflow-definition locator, not a step log index.
The individual numeric-job log endpoint for job `126866778294` returned combined
text containing both the test/command output and upload output. It included
artifact ID `13362804193` and digest
`bfcb19ee6693e634ddf3119181b6a1276a7478a4292d08f3e26ed8f5b9b20b46`.
Those lines are observable data, not sufficient proof of which executable
emitted them. The saved probe is under ignored
`work/artifact-proof-feasibility/logs-probe.json`, beside the original ZIP.
[Observed public run](https://github.com/taipei49314/checkwash/actions/runs/41913448234)

The runner internally associates child execution contexts and log uploads with
timeline record IDs. That internal separation does not establish that the public
REST log representation exposes a safe, stable step selector. The inspected
runner source binds logger setup to a record ID and uploads step/job log records
separately; the observed downloadable archive still combined steps.
[Runner execution contexts](https://github.com/actions/runner/blob/cf65f3ec8100ee0046d735ac5d647e00f4b8e817/src/Runner.Worker/ExecutionContext.cs),
[runner log upload queue](https://github.com/actions/runner/blob/cf65f3ec8100ee0046d735ac5d647e00f4b8e817/src/Runner.Common/JobServerQueue.cs)

Do not split combined logs at `##[group]`, display step names, timestamps or
lookalike upload messages and call the selected text trusted. Arbitrary stdout
can contain such text. A future log-based proof needs a separately qualified
platform boundary exposed to the verifier, including adversarial spoofing,
duplicate job names, log truncation and rerun controls. It cannot be assumed from
an older ZIP layout or the runner's private implementation. Current log-based
artifact/job proof is **unsupported**.

## 2. What the official attestation verifier actually authenticates

The installed GitHub CLI was `2.98.0 (2026-08-20)`, corresponding to source commit
`a255baf71d13fe5947a4eb7ad521ffd412d64cee`. Its observed Windows executable SHA-256
was `a1701e3c806a805981554ace7df04812772c6449bdcfd1a77675013d97786401`.
That is a local tool fingerprint, not a claim that this investigation independently
qualified the CLI distribution/signing chain.

The official CLI supports exact certificate identity, signer workflow digest,
source digest/ref, hosted-runner restriction, saved bundles, saved trusted roots
and machine-readable verified results. Its documentation warns that signed
predicate metadata is workflow-controlled; certificate fields and verified
timestamps are a different trust boundary. Product policy must check both.
[CLI verification reference](https://cli.github.com/manual/gh_attestation_verify)

Use exact `--cert-identity`, including `@<approved full SHA>`, plus
`--signer-digest`. Do not rely solely on `--signer-workflow`: the inspected CLI
version constructs a prefix matcher for that option. This specification does
not claim that a prefix match is an exact workflow identity.
[CLI 2.98.0 policy implementation](https://github.com/cli/cli/blob/a255baf71d13fe5947a4eb7ad521ffd412d64cee/pkg/cmd/attestation/verify/policy.go)

The actual public reusable-workflow test vector verified here contains:

| Verified certificate field | Observed value or meaning |
| --- | --- |
| `issuer` | `https://token.actions.githubusercontent.com` |
| `subjectAlternativeName`, `buildSignerURI` | `https://github.com/github/artifact-attestations-workflows/.github/workflows/attest.yml@09b495c3f12c7881b3cc17209a327792065c1a1d` |
| `buildSignerDigest` | `09b495c3f12c7881b3cc17209a327792065c1a1d` |
| `sourceRepositoryIdentifier` | `804070735` |
| `sourceRepositoryURI` | `https://github.com/malancas/attest-demo` |
| `sourceRepositoryDigest` | `95baf27389e83e6a5c48f42e190d48d7abcea19e` |
| `runInvocationURI` | `https://github.com/malancas/attest-demo/actions/runs/9228858953/attempts/1` |
| `runnerEnvironment` | `github-hosted` |

The artifact digest is cryptographically checked against the signed statement's
subject by the official verifier. The certificate supplies run **and attempt**;
the product must enforce their exact expected URI rather than reading an
unverified predicate's invocation string. Neither this observed certificate nor
the documented Fulcio GitHub mapping provides a numeric job ID.
[Fulcio certificate extension mapping](https://github.com/sigstore/fulcio/blob/main/docs/oid-info.md)

GitHub's OIDC provider does supply `check_run_id`, documented as the current job's
check identity and present in the live discovery document on the probe date.
This creates a possible trusted-producer-to-platform job relation, but it does
not mean the field survives into a Sigstore certificate.
[GitHub OIDC claims](https://docs.github.com/en/actions/reference/security/oidc),
[live OIDC discovery](https://token.actions.githubusercontent.com/.well-known/openid-configuration)

The live API for Fulcio issue #2204 showed it closed as `not_planned` on
2026-10-18; this was not evidence of implementation. The closure comment invited
renewed interest rather than reporting added certificate support. Do not infer
job binding from a closed issue or a parsed JWT body.
[Fulcio check-run claim request](https://github.com/sigstore/fulcio/issues/2204)

## 3. Narrow approved producer design

The initial scope is one fixed producer job on an approved ephemeral Linux
GitHub-hosted runner, with untrusted Python work confined to a restricted
container. This is a proposed qualification target; the earlier Windows pytest
parser experiment does not qualify this runner/container design. Forks or events
that cannot obtain the required scoped permissions remain unsupported. No
`pull_request_target` workaround executes PR code with host credentials.

Merely placing pytest and signing in different steps is **not isolation**.
Untrusted tests or dependency installation sharing the host can steal OIDC or
runtime credentials, alter `GITHUB_ENV`/`GITHUB_OUTPUT` or action code, leave a
background process, replace receipt files, and race the upload. Approval requires
all of the following properties to be implemented and negatively tested:

1. The host executes only full-SHA-pinned producer code and actions. It resolves
   the source snapshot and job identity using its own clean environment and
   platform responses. No PR shell, Python import, Git hook/filter or project
   installation runs on the host. Caller inputs are typed source selectors,
   never commands, image names, Docker flags, artifact IDs/digests or predicates.
2. The container image is pinned by digest. Untrusted setup and tests run as a
   non-root user, with capabilities dropped, no-new-privileges, enforced seccomp,
   bounded CPU/memory/process/time limits and no network. No Docker socket,
   devices, host PID/IPC namespace, credential endpoint environment, host home,
   runner directory or writable host mount is exposed. Source/configuration may
   be supplied read-only; mutable test files remain container-private. Initial
   dependency inputs must be approved and available offline. Requirements that
   need arbitrary network access are outside this first contract.
3. The host captures untrusted stdout/stderr as bounded data without feeding it
   back into the Actions workflow-command processor. Printing `::set-output`,
   `::add-mask`, `::stop-commands`, fake upload lines or control characters must
   not change host state. Rendering it later requires escaping.
4. The host terminates the entire container process tree and confirms it has
   stopped before collecting output. It reads only explicitly approved regular
   files through a bounded transfer with safe member checks; it never follows a
   symlink, extracts a guest-controlled path or executes guest output. A Docker
   copy/export stream, if chosen, is itself an untrusted archive requiring such
   validation. It does not become trusted merely because Docker produced it.
5. The host creates a fresh private staging directory inaccessible to the guest.
   It records real setup/test exit observations and writes contract-v1 receipt
   JSON after hashing the retained report bytes. The guest cannot modify the
   staging files, producer identity, source binding, upload action or predicate.

Docker documents digest references, namespace/filesystem isolation, networking,
users, capabilities and resource controls; the specific configuration above is
our required product profile, not a claim that default containers meet it.
Containers share a host kernel, so this contract does not promise resistance to
kernel/runtime escape or a compromised runner.
[Docker run reference](https://docs.docker.com/engine/containers/run/)

The trusted host obtains its own OIDC token from the platform's credentialed
endpoint, reads `check_run_id` in that context, and discards the token rather than
persisting a live bearer credential. It matches that check to the exact attempt's
job API record and saves the resulting numeric `job_id`, `check_run_id`, run and
attempt. Missing or ambiguous relations refuse publication. This identity
acquisition is approved code behavior; the verifier later receives a signed
assertion of the result, not an independently verified copy of the OIDC token.

The approved host uploads only its immutable staged `receipt.json`/`junit.xml` using a pinned
upload action with `overwrite: false` and missing-files-as-error. It captures the
action's actual `artifact-id` and `artifact-digest` outputs in a host-controlled
record. It never rediscovers an artifact by name to decide what to sign.
The profile's `delivery_review_run_attempt_check_v1` locator rule produces
`delivery-review-<run_id>-<attempt>-<check_run_id>`. This name locates candidates;
only signed actual artifact identity/digest and the full proof chain bind them.

After successful upload, the same trusted producer signs a custom predicate
using the official `actions/attest` action. The reviewed API candidate
`actions/attest` v3.0.0 resolves to
`daf44fb950173508f38bd2406030372c1d1162b1` and supports `subject-digest`,
`subject-name`, `predicate-type` and `predicate-path`. This is a capability
candidate, not an approved production pin; code/dependency review and hosted
qualification are still required. [Pinned attestation action interface](https://github.com/actions/attest/blob/daf44fb950173508f38bd2406030372c1d1162b1/action.yml)

Required publication permissions are `id-token: write`, `attestations: write`,
`contents: read` and the read permission needed for job/check observations.
They are scoped to the host producer and never passed into the guest. No
repository administration, signing secret, `actions: write`, package publication
or cross-repository write token is part of this design. Public attestation
verification does not require producer write permissions. Reusable caller and
called workflow permission propagation must be qualified, including insufficient
permission refusal. [Reusable-workflow attestation permissions](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/increase-security-rating)

A separate signer job is not an automatic substitute: it would introduce the
same missing upstream test-job/artifact association. Do not move signing to a
new job and trust whichever same-name artifact it downloads.

## 4. Signed product predicate and overwrite resistance

Proposed predicate type:
`https://github.com/taipei49314/delivery-review/attestation/pytest/v1`.
It is a versioned identifier for this design, not a published proof endpoint.
This is a custom product assertion, not a claim of SLSA certification.

There must be exactly one signed subject. Its name is the canonical actual
artifact URL:

```text
https://github.com/OWNER/REPO/actions/runs/RUN_ID/artifacts/ARTIFACT_ID
```

Its sole digest is `sha256:<actual upload ZIP digest>` as represented by the
in-toto subject's `digest.sha256` value. The action receives the known uploaded
ZIP digest directly; it does not accidentally hash `report.xml`, the receipt,
a locally rebuilt ZIP, or the artifact name. The verifier independently hashes
the downloaded original ZIP bytes and checks the same digest.

The strict custom predicate records:

```json
{
  "predicate_version": 1,
  "repository_id": 9001,
  "run_id": 1001,
  "run_attempt": 1,
  "job_id": 2001,
  "check_run_id": 3001,
  "artifact_id": 4001,
  "artifact_sha256": "<64 lowercase hex>",
  "receipt_sha256": "<64 lowercase hex>",
  "junit_sha256": "<64 lowercase hex>",
  "producer_profile": "pytest-isolated-container-v1"
}
```

The numbers and digest placeholders above are illustrative, not an accepted
fixture or JSON schema. Implementation must reject unknown/duplicate keys,
coercions, booleans as integers, nonpositive/out-of-range IDs and malformed
digests. Producer/source identities remain independently checked against the
certificate, referenced-workflow metadata and the signed archive's receipt.
Repeated fields must agree exactly; the parser cannot choose a convenient copy.
The approved producer's immutable source defines the only permitted way to form
this predicate. The caller cannot supply or modify it.

The signed subject URL binds the artifact ID in addition to its digest. This
also rejects an attacker who deletes an artifact and recreates byte-identical
contents under a new ID. With different bytes, digest verification fails; with
identical bytes, the signed ID comparison fails. An attacker signing the new
artifact in a caller-controlled job gets a different signer identity and cannot
meet the exact approved reusable-workflow SHA. Reusing an old attestation fails
the certificate's expected run/attempt or the subject's expected ID checks.

An attacker may race deletion before, during or after signing. The trusted
producer still signs only its captured original upload outputs; the verifier
requests that exact numeric ID, checks bytes and performs the required final
platform recheck. Deletion or absence becomes unavailable. There is no name
fallback. A saved successfully verified copy remains historical evidence,
not a claim that the remote artifact is still currently available.

This closes the delete/recreate substitution path **assuming the qualified
isolated producer contract**. Its job relation is an authenticated approved-
producer assertion cross-checked with independent job/check metadata. It is
neither an unsigned receipt's self-awarded binding nor a nonexistent native
certificate job field. The limited trust does not prove the tests themselves
could not fabricate JUnit.

## 5. Verifier boundary and replay envelope

The bounded runner must invoke an operator-pinned absolute `gh` executable
using argv, never a shell or receipt-provided command. For a captured artifact
and bundle, the effective command is:

```text
gh attestation verify <saved-archive.zip>
  --bundle <saved-bundle.jsonl>
  --custom-trusted-root <operator-approved-trusted-root.jsonl>
  --repo OWNER/REPO --hostname github.com
  --cert-identity https://github.com/PRODUCER/REPO/.github/workflows/pytest.yml@FULL_SHA
  --signer-digest FULL_SHA
  --cert-oidc-issuer https://token.actions.githubusercontent.com
  --deny-self-hosted-runners
  --predicate-type https://github.com/taipei49314/delivery-review/attestation/pytest/v1
  --digest-alg sha256 --format json
```

Exact approved source digest/ref can additionally be enforced by CLI flags when
their event semantics match the frozen source model. Never equate the
certificate's event-source SHA with the tested checkout without checking the
approved head/merge source contract. The product must inspect the successfully
verified certificate's numeric source repository ID, full signer identity,
run/attempt URI and event/source claims, as well as the expected signed subject
and every predicate field. Match the selected exact-attempt job/check and its
Actions App identity through retained platform responses. Confirm the actual
referenced reusable workflow and its repository ID/path/SHA independently.

The runner accepts neither uploaded `verificationResult` JSON nor a user-supplied
`verified: true` field as proof of cryptographic verification. It must actually
run the official tool on these saved bytes, require exit zero, validate bounded
JSON output and apply product policy only to the returned verified records.
Ambiguous matching records, partial output, timeout, unsupported output version
or any identity disagreement refuse binding. Unverified bundle payloads may be
displayed as claims, never substituted for verified results.

Retain an execution envelope containing:

- Tool version, executable SHA-256, platform, fixed adapter/contract version and
  actual argv with no token or signed download URL.
- Exact archive, attestation bundle, trusted-root and expected-policy byte hashes;
  archive artifact ID; raw run/attempt/job/check/reference-response hashes.
- Start/end time, timeout/termination status, actual native exit, bounded stdout
  bytes and SHA-256, and safe diagnostic status. Keep raw verified JSON from the
  successful official invocation; do not replace it with a boolean summary.
- A separate policy result showing which certificate, subject, predicate and
  platform fields were matched and which required proof remains absent.

The trusted root is operator-controlled input obtained through the official
trust-bootstrap path, never a root shipped inside the subject's artifact.
`gh attestation trusted-root` can export roots for offline verification. Root
rotation, CLI distribution authenticity, expiration policy and platform support
require a pinned verification-tool qualification record before release.
[CLI trusted-root command](https://cli.github.com/manual/gh_attestation_trusted-root)

Offline replay re-runs the official verifier against retained artifact/bundle
bytes and the approved saved trust roots before reducing policy. It retains
original observation times and does not fetch missing artifacts or promote
freshness. Local hashes/envelopes do not protect against an owner rewriting both
evidence and manifests; they are integrity bookkeeping, not another signature.

### Implemented adapter version 1

`src/delivery_review/adapters/github_attestation.py` exposes two execution
interfaces. Both require inert `artifact_bytes`, `bundle_bytes`,
`trusted_root_bytes`, an absolute `gh_executable`, the approved `gh_sha256`, a
trusted `expected` object, a new `destination`, and optional `AttestationLimits`.
They do not accept saved verified JSON as a substitute for running the tool.

`verify_github_attestation` checks the fixed custom product predicate. Its
`expected` object has exactly these fields:

```text
repository_id, repository_full_name,
certificate_source_sha, certificate_event,
run_id, run_attempt,
producer_repository_full_name, producer_path, producer_sha,
job_id, check_run_id, artifact_id,
artifact_sha256, receipt_sha256, junit_sha256
```

IDs are positive integers at most 2^53−1, with no bool/float coercion. Digests
are complete lowercase hex. `certificate_event` is `pull_request` for this
product profile. The function derives the exact subject URL, fixed predicate
type and `pytest-isolated-container-v1` producer profile. The event-source
`certificate_source_sha` is explicit: it must not be guessed from a tested PR
head. The composer independently establishes the event-source SHA and compares
the receipt's tested checkout against the frozen source contract.

`verify_attestation_cryptography` is a narrower general verification interface
used for official upstream qualification vectors. Its expected object replaces
the six job/artifact/hash fields with exact `subject_name` and `predicate_type`;
it allows the fixed product type or SLSA v1 and the three events `pull_request`,
`workflow_dispatch`, and `push`. It checks certificate and subject only and
always leaves `predicate` unconfirmed. It cannot substitute for the product
interface or qualify arbitrary SLSA metadata as a delivery-review proof.

The returned `AttestationExecution` contains immutable `manifest_bytes`,
`raw_files`, and `observation_bytes`, with defensive parsed `manifest` and
`observation` properties. `verification.json` is written last. The observation
separates official cryptographic success, certificate/subject/predicate matches,
original tool output, process facts, and integrity errors. The fields
`platform_binding`, `producer_qualification`, and `tested_source_binding` remain
`unconfirmed` in both interfaces. The composer must independently use retained
platform records and the approved producer qualification before awarding
`workflow_bound`; an expected dictionary is not platform evidence.

Only the following exact GitHub CLI 2.98.0 AMD64 binaries are supported:

| Platform | Binary SHA-256 |
| --- | --- |
| Windows | `a1701e3c806a805981554ace7df04812772c6449bdcfd1a77675013d97786401` |
| Linux | `62885b97de6a0cd85e616cdd94bcda908bf5cf1018094385892b05cea3537163` |

Both were downloaded from the official immutable
[v2.98.0 release](https://github.com/cli/cli/releases/tag/v2.98.0), checked against
its published checksums and extracted as single inert regular binary members.
The Windows release binary matches the earlier installed-binary observation.
The Windows ZIP digest is
`c28c7b3b584967a05b74d9eaf7481bff24ddc34930bf2d6e442c148236561eb1`;
the Linux tarball digest is
`3b8ac6b30336802fc1a858d7c084e11cdf24ac1a761ca90b68022d7d729208de`.
This records official HTTPS/checksum provenance; independent publisher-signature
qualification remains a release requirement. Passing a caller-selected hash does
not add it to the approved binary set.

The currently supported saved official trusted-root export is the SHA-256
`65ca537f6ed8a47fd0e560c421baa1f6c1efb8b25fc200d8c5c02c0e92eb2b9c`
recorded below. A subject-provided or unknown root is refused before execution.
Rotating this pin requires a reviewed trust-bootstrap update; no dynamic root
download occurs while verifying an artifact.

Each execution copies the verified binary into a new private directory and
rechecks it and the saved inputs after the process exits. It uses the bounded
process-tree implementation from SPEC-003. HOME, config, cache and temporary
directories are new. Tokens, debug variables and unrelated host configuration
are absent; prompts/update notifications are disabled and proxy addresses are
unusable. The manifest explicitly records that network isolation is not
OS-enforced. Original byte inputs are saved before invocation. Post-execution
integrity failure preserves raw output while refusing proof.

Default timeout is 60 seconds, with a hard maximum of 120. The artifact cap is
25 MiB, bundle/stdout caps 2 MiB each, trusted roots/stderr 1 MiB each, and binary
128 MiB. Local values may decrease these caps only. Version 1 accepts one JSON
Sigstore v0.3 bundle with one DSSE signature, stores it as `.json` for the
official CLI, and requires one verified result and one signed subject. JSONL
collections and other bundle/verification versions fail closed in this slice.
Unknown output structure, malformed UTF-8, duplicate keys, nonfinite numbers,
depth over 32 and more than 100,000 JSON nodes are refused. Native finite floats
may be preserved outside the strict product predicate.

The original DSSE payload is also parsed with duplicate-key refusal and compared
to the official verified statement. This is a consistency filter only: the
unverified payload never supplies authenticated claims or replaces the tool's
verified output. It prevents duplicate signed keys from being accepted merely
because another JSON implementation normalized them. Every product-predicate
key and exact type must agree with the expected independent observation.

Observed gh 2.98.0 output retains a generic `verifiedIdentity.issuer` matcher
even when the exact issuer flag is supplied. The adapter therefore checks the
authenticated certificate's `issuer` itself, along with exact signer, source
repository ID/URI/SHA, trigger, hosted-runner state and run/attempt URI. A
displayed verifier-policy hint is not used in place of the certificate.

## 6. Actual read-only verifier experiment, 2026-11-06

The upstream files came from the public `cli/cli` tag v2.98.0, resolved to commit
`a255baf71d13fe5947a4eb7ad521ffd412d64cee`:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `reusable-workflow-artifact` | 2,962 | `49a3aa6075e0f49f82843e74b5baa614ad2a588e6675612bf108a0a008c5ac25` |
| `reusable-workflow-attestation.sigstore.json` | 11,346 | `88e35c3496fc9b9bd29629b69271bd32738e170f0d85a12d67da0c72c743f3bd` |
| Exported `trusted_root.jsonl` | 34,634 | `65ca537f6ed8a47fd0e560c421baa1f6c1efb8b25fc200d8c5c02c0e92eb2b9c` |

The first two are upstream public test vectors, not delivery-review acceptance
fixtures. The artifact was treated only as inert bytes; it was not installed,
imported or executed. [Upstream fixture directory](https://github.com/cli/cli/tree/a255baf71d13fe5947a4eb7ad521ffd412d64cee/pkg/cmd/attestation/test/data)

After exporting trusted roots, all verification controls used saved bundle/root
files, no token environment, an empty `GH_CONFIG_DIR`, disabled prompts and
unusable local HTTP(S) proxy addresses. This is an observed offline-mode smoke,
not an OS-enforced proof that no network call can occur.

| Actual official CLI control | Exit | Observation |
| --- | ---: | --- |
| Correct artifact, exact signer identity/SHA, correct source repo, hosted runner | 0 | Verified JSON with certificate and signed subject retained |
| Append bytes to artifact | 1 | No verified JSON |
| Wrong signer SHA | 1 | No verified JSON |
| Wrong source repository | 1 | No verified JSON |
| Alter the signed subject name in the bundle without resigning | 1 | No verified JSON |

The experiment used the upstream SLSA predicate, not the proposed custom product
predicate, so it demonstrates the official cryptographic and identity machinery
only. `work/artifact-proof-feasibility/attestation-probe.json` retains exact argv,
tool/input/output fingerprints and all five native exits. The successful result
is `valid_offline.stdout.json`; original inputs and separate negative-control
copies are retained in the same ignored directory. No output was manufactured
to simulate successful cryptographic verification.

The subsequent `probe_verifier_adapter.py` runs the official release binaries
through the implemented bounded adapter on Windows/Python 3.12 and Linux/Python
3.11. Its correct upstream SLSA control returns native exit 0 and matching
certificate/subject; changed artifact bytes and signer SHA return exit 1.
Wrong expected attempt, subject name and event-source SHA still return native
exit 0 from the official crypto verifier but are rejected by the adapter's exact
claim comparison. Every result leaves platform/producer/tested-source binding
unconfirmed. The checked-in custom-predicate unit controls are explicitly
synthetic parser/process-response controls, not newly signed product evidence.

## 7. Required qualification before awarding workflow_bound

The next implementation gate is the bounded official-verifier runner plus the
isolated producer. No pure JSON parser is added as a substitute for that runner.
Release qualification must demonstrate:

- Actual approved producer → isolated pytest → receipt → upload → custom signed
  predicate → bounded download → official verification → exact job/check/source
  binding, with raw bytes and native outcomes retained.
- A malicious caller job that creates the same name before upload, replaces it
  after upload, recreates identical bytes with a new ID, signs with another
  workflow, or replays an older run/attempt's authentic attestation. None may
  satisfy the selected test obligation.
- Guest attempts to read host tokens/sockets/files, mutate workflow command
  files, forge host output, replace receipt bytes after exit, leave background
  writers, exhaust resources or exploit symlink/archive output paths. These
  controls must demonstrate the actual isolation configuration, not inspect YAML.
- Correct OIDC check-run → job API relation, different-job substitutions,
  duplicate/missing relations and multiple reusable invocations. Do not assume
  `check_run_id == job_id`; use the platform's explicit job/check link.
- Wrong root, issuer, signer SHA/path/repository ID, source ID/SHA, run, attempt,
  artifact ID/digest, receipt/JUnit digest and unsupported verifier output, with
  signature-valid but policy-wrong controls where feasible.
- Saved-root offline replay and refreshed evidence, without network fallback,
  source overwrite or false freshness. Producer/signature success must not erase
  zero tests, skips, test failures, malformed JUnit or missing evidence.

The design addresses the known overwrite/recreate race without name-based
inference. The public probes and official test-vector verification do not by
themselves establish a qualified delivery-review producer or product readiness.

## 8. Implemented archive byte boundary

`delivery_review.adapters.artifact_zip.read_artifact_zip` reads the archive
entirely in memory, with required frozen-policy limit fields and an exact
approved member tuple (default `receipt.json`, `junit.xml`). It returns immutable
member-byte/digest mappings plus original archive bytes/digest. It does not
interpret a receipt or authenticate anything in it.

The initial dialect accepts classic, single-disk ZIP with stored/DEFLATE regular
files and optional 32-bit data descriptors. It validates central/local fields,
exact nonoverlapping physical regions, complete DEFLATE consumption, sizes and
CRC. ZIP64, SFX prefixes, archive/member comments, directory entries, encryption,
other compression and unknown extra fields are unsupported; only inert UT/NTFS
timestamp extras are admitted. Unsafe/non-ASCII paths, Windows aliases,
case-insensitive duplicates, symlinks and extra/missing members are rejected.

Local ceilings are 25 MiB archive, 100 MiB expanded, 100 members, 25 MiB per member
and 100:1 compression ratio; external policy can only reduce effective limits.
No extraction, file opening, subprocess or network operation occurs. Twenty-two
authored unit methods exercise these boundaries. A deterministic local header
mutation smoke made 5,299 attempts: 5,271 domain refusals and 28 accepted archives
whose member bytes were unchanged; there were no unhandled exceptions.

Two actual public artifacts from run `41913448234`, whose job steps referenced
upload action SHA `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a`, were downloaded
read-only and accepted by the reader:

| Artifact ID | Archive bytes | Archive SHA-256 |
| --- | ---: | --- |
| `13362804193` | 386,148 | `bfcb19ee6693e634ddf3119181b6a1276a7478a4292d08f3e26ed8f5b9b20b46` |
| `13363711890` | 721 | `e9a9a57a0cf37ed0ffc26d4c226d1507139c4c1300273257998f8570fdacb40f` |

Both hashes matched the platform artifact metadata. The four-member and
one-member archives used DEFLATE, flag 8 (data descriptors), Unix regular-file
attributes, version-needed 20 and no extra fields. The larger archive included
a 2,430,721-byte member; full decompression, CRC, exact physical regions and
member digests were checked without writing extracted contents to disk.
`work/artifact-proof-feasibility/artifact-zip-probe.json` retains argv, headers,
digests and results. The probe explicitly selected the observed public filenames
only to test format compatibility; discovering names from an archive is never
operator approval for a production inspection. These are not pytest producer
receipts and establish no source or artifact/job trust. The exact proposed
producer/template remains unqualified; unsupported metadata is not silently
ignored to make an artifact pass.
