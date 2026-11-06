# Authored public reference flow and qualification procedure

Status: the first-party Apache-2.0 fixture is published in the dedicated public
[delivery-review-reference repository](https://github.com/taipei49314/delivery-review-reference).
The first two hosted attempts are preserved as actual failures before artifact
upload or signing: [run 42177088855](https://github.com/taipei49314/delivery-review-reference/actions/runs/42177088855)
and [run 42178476087](https://github.com/taipei49314/delivery-review-reference/actions/runs/42178476087).
Their original base/PR pairs remain unchanged; later attempts use new pairs and
separate evidence records. Fixed revision `3d3e54599066c98f52384c66953b130a01eaaff8`
has since produced authentic signed head/merge evidence and actual failure,
skip, isolation and artifact-lifecycle controls; see the
[recorded hosted controls](../../docs/specs/SPEC-006-producer-execution.md#actual-public-hosted-controls-at-3d3e545).
The two fixed paths now have [reviewed qualification](../../qualification/README.md);
E01–E18 complete-product acceptance remains pending. Fixture files or
individual successful Actions runs do not establish product acceptance.
Do not use another project's PR as a destructive test target.

The initial producer supports same-repository GitHub.com `pull_request`, Linux
amd64 GitHub-hosted Ubuntu 24.04, Python 3.11.16, pytest 8.4.2, and literal
`tests` with no project dependency installation. It executes tests only inside
the restricted guest. Two separate, fixed reusable workflows select PR head
(`pytest-producer.yml`) or GitHub event merge (`pytest-producer-merge.yml`). The
certificate's event merge SHA is checked against the exact ordered base/head
parents in both modes. The merge workflow measures that actual merge checkout;
the head workflow measures the PR head. Neither workflow accepts caller inputs.
Forks, Dependabot `dynamic`, merge queues and arbitrary test selectors remain
unsupported. See [SPEC-006](../../docs/specs/SPEC-006-producer-execution.md).

## Freeze the candidate and author the reference

1. Finish review and commit the product/producer candidate. Record the complete
   source SHA and immutable wheel/sdist hashes. Make the reviewed licensed
   producer repository publicly readable before trying this consumer: bootstrap
   verifies its numeric repository ID before obtaining source. No moving tag or
   branch may replace the producer SHA.
2. Run `render.py` from the product checkout with that reviewed full SHA and a
   new destination outside the product checkout. The renderer copies an explicit
   small file list, writes the pinned consumer workflow and never initializes
   Git, contacts GitHub, executes tests or publishes anything.

   ```sh
   python examples/reference/render.py --producer-sha FULL_REVIEWED_SHA --variant base --out ../reference-base
   python examples/reference/render.py --producer-sha FULL_REVIEWED_SHA --variant e01 --out ../reference-e01
   ```

   For the E03 merge control, author another base/PR pair using
   `--source-mode github_merge` on both commands. This selects the other fixed
   workflow while authoring source; it does not add a runtime selector to the
   workflow. Keep separate approved policies and qualification records for each
   exact producer path, revision and permitted source mode.

3. Review both trees. Initialize the dedicated reference repository from the
   base tree and make an independently recorded base commit. Open a same-repo PR
   containing the `e01` production refactor and its updated fixture manifest.
   The four tests and consumer workflow are unchanged. Record reference numeric
   repository ID, base/head commits, PR number and its GitHub merge object.
4. Read the actual platform workflow ID and full job name from this execution.
   A reusable workflow's display name can include its caller prefix; do not
   guess that it is simply `pytest-linux`. Record run, attempt, job/check IDs
   separately, App ID, and original API bytes. Job ID need not equal check ID.

`fixture-manifest.json` records the authored variant and file bytes. Its
`product_integration: NOT_RUN` describes authoring, not the eventual run. Keep
it unchanged as source provenance; later actual run receipts are separate files.

## Qualify the producer before using it for readiness

Hosted execution requires a public, reachable full producer SHA in the original
numeric repository, a same-repository PR in the dedicated reference, and GitHub
Actions enabled with execution allowance. The consumer workflow must exist on
the selected base and grant `contents: read`, `actions: read`, `checks: read`, `id-token: write`
and `attestations: write`; repository/organization Actions policy must permit all
pinned actions and the reusable workflow. The runner must supply the OIDC
`check_run_id` and full reusable workflow identity that the controller can
independently match through the Jobs/Checks APIs. A missing capability refuses
the run; do not weaken the identity check to accommodate it.

The supported host is GitHub-hosted Ubuntu 24.04 Linux amd64 with Python 3.11.16,
compatible Docker, cgroup v2 limits, private tmpfs and the exact shipped seccomp
profile. Trusted dependency retrieval needs the fixed Docker/PyPI/base-image
endpoints before guest execution. Project code runs only with networking
disabled. Actual hosted behavior of these dependencies, OIDC/API mapping,
artifact upload/download and custom attestation remains a qualification gate.

Run the fixed producer through the real hosted path. Preserve the exact
workflow revision and action pins, runner image/kernel/Docker/Python versions,
original receipt/JUnit/archive bytes, upload ID, platform digest, downloaded
SHA-256, custom Sigstore bundle and original API records. Independently verify
the custom statement with the approved offline gh binary and saved trusted root.
Record every actual native exit and original verifier output.

The qualification must include actual kernel/cgroup/tmpfs isolation, absent host
tokens and Docker socket, denied writable host mounts/network access, no raw
guest stdout reaching Actions commands, stopped guest before JUnit copy, and
confirmed cleanup. Include symlink output, background writers, resource limits,
workflow-command spoofing and boundary-error receipt refusal. Local authored
Docker controls are useful regression evidence; they do not establish hosted
OIDC, job identity or signing behavior.

Exercise artifact spoof/replacement controls in separate caller jobs: same-name
precreation, delete/recreate after upload, identical bytes with a different ID,
another job's output, another signer workflow and an older run/attempt's valid
attestation. None may satisfy the selected obligation. Preserve both the
legitimate and attack bytes/IDs; never rewrite a failed attempt as the control.
The producer's explicit upload ID/digest and signed job/check assertion must
agree with independently observed platform records.

Only an actually passing producer qualification may be reviewed into the
release-bundled producer registry. Its receipt must identify source revision,
profile `pytest-isolated-container-v1`, literal `tests`, permitted source modes,
actual platform and immutable supporting evidence. Missing registry approval
leaves test evidence unqualified even when a signature verifies. Do not add an
entry merely to make E01 green. This producer qualification is separate from
the complete product E01 report, so the gate is not circular.

## Prepare an independently approved inspection profile

Build a candidate policy outside the reviewed PR using independently recorded
identities. This helper only constructs a digest identity and explicitly does
not approve it:

```sh
uv run --locked python examples/reference/prepare_profile.py \
  --repository OWNER/REFERENCE --repository-id NUMERIC_ID \
  --workflow-id NUMERIC_WORKFLOW_ID --job-name 'EXACT OBSERVED NAME' \
  --producer-sha FULL_QUALIFIED_SHA --out ../reference-policy-for-review.json
```

Add `--source-mode github_merge` only for the independently qualified fixed merge
workflow. Its policy permits that mode alone; its selector names the merge
workflow's exact path. Use the observed job name, including caller prefix, for
each workflow rather than copying the head workflow's display name.

Review the complete file, including repository, workflow/App/job, producer SHA,
engine pins, source mode, minimum tests, skip rule and resource limits. Approve
that exact file through an independent maintainer process. Copying it into a PR
or running `policy seal` is not approval. Required source, CI, pytest, Checkwash
and Pinwash obligations are present; Ledgerwash absence is explicitly optional.

## Run the installed full product

Use the installed candidate outside its source checkout on Linux/Python 3.11.
Follow [INSTALL.md](../../docs/INSTALL.md) to verify/install the exact wheel and
runtime dependencies. The example toolchain file lists local paths only; fill
in the actual qualified Python/Git, three exact hash-pinned engine wheels,
approved gh binary and saved trusted root. This is not a dependency downloader
or permission to substitute newer tools. Engine and verifier pins remain in
their respective locks/specifications.

```sh
delivery-review inspect https://github.com/OWNER/REFERENCE/pull/NUMBER \
  --policy /absolute/approved-reference-policy.json \
  --tools /absolute/reference-toolchain.json --out /absolute/e01-live
delivery-review verify /absolute/e01-live
delivery-review replay /absolute/e01-live \
  --tools /absolute/reference-toolchain.json --out /absolute/e01-replay
delivery-review refresh /absolute/e01-live \
  --tools /absolute/reference-toolchain.json --out /absolute/e01-refreshed
```

Read-only GitHub API authorization may be required to download the artifact.
Supply it only through the process environment; do not put a token in a command,
profile, receipt or published reproduction file. Replay must work with network
blocked and unusable token/proxy variables. It reparses saved evidence, restores
static Git objects and runs the approved offline signature verifier; it does not
rerun pytest, project installation or native static scanners. Each output
directory is new. Save raw JSON/Markdown/HTML and exact inventories from every
attempt, including failures.

An actual E01 pass must be `complete/current/clear`, ready, exit 0. Replay must
preserve evidence/policy and `observed_at`, produce freshness `unknown`, and
return exit 2. Same-input offline replays must agree; refresh must leave old
bytes untouched. A failure remains a qualification result to investigate, not
permission to alter the frozen expected decision or silently change the policy.

## E01–E18 control design

The normative variants and expected decisions remain in
[ACCEPTANCE.md](../../docs/ACCEPTANCE.md) and the frozen acceptance cases. This
table identifies how to obtain evidence; every family remains **NOT_RUN** until
its own recorded product execution meets those expectations.

| Family | Controlled input and execution boundary |
| --- | --- |
| E01 | Public reference `base` → `e01`, unchanged four tests, genuine hosted receipt/upload/custom signature, installed full product and original three native engine outputs. |
| E02 | Preserve a complete older head's run, then push a new head without matching evidence; compare a genuinely matching new-head run. Never relabel old run source. |
| E03 | Actual direct-head control and separately qualified fixed merge workflow for the positive two-parent case; preserve canonical Git objects and measured checkout bytes. Use reversed/wrong/missing/extra parents, mode/path substitutions and disallowed policy as refusal controls. Both candidates exist locally; their hosted qualifications remain NOT_RUN. |
| E04 | On the dedicated fixture, advance head, base, or rerun attempt between initial acquisition and final recheck. Keep before/after original API bytes and the exact timing trigger. |
| E05 | Change only HEAD threshold/job/allowlist/outer policy while the external approved profile stays byte-identical; use native BASE policy semantics and retain config/exemption observations. |
| E06 | Remove workflow/job/matrix cell on separate fixture branches; bounded recorded API page controls for incomplete pagination. Actual transport pagination must also be observed. |
| E07 | Real skipped/cancelled/timed-out jobs and explicit neutral metadata control; a separate qualified partial-skip execution under both skip policies. `all_skipped` is available from the renderer. |
| E08 | Wrong App/workflow, duplicate check/job matches and a genuine trusted match; preserve exact platform relation and avoid display-name inference. |
| E09 | New pending run versus old success, latest failed attempt versus old success, and complementary jobs across attempts. Use actual rerun/attempt records where possible. |
| E10 | Alter a copy of archive/receipt/JUnit/digests/IDs/source; use genuine signatures with wrong signer/job/run/attempt where feasible; include identical-byte replacement under a new artifact ID and fully bound control. |
| E11 | Bounded scanner subprocess controls for timeout/native exit 2/output cap plus malformed/unknown JSON; another real pinned scanner produces a known block which remains in the final report. |
| E12 | Native skipped/unread/config-error/unknown-assertion observations with required/optional coverage policies. Preserve coverage bytes; do not translate unavailable scope into clean findings. |
| E13 | Actual locked engines on authored weakening/refactor, valid BASE exemption and new HEAD exemption. Preserve native verdict/fingerprint/coverage; do not tune detectors or expected output. |
| E14 | Renderer variants `test_failure`, `zero_tests`, `all_skipped`; isolated producer controls for setup/collection errors, missing XML, duplicate case IDs and count conflicts. Tie valid cases through real upload and report; boundary-refused cases retain producer failure evidence. |
| E15 | Genuine absent, unsupported and supported-with-gaps ledgers under optional/required policies; actual Ledgerwash output and raw ledger bytes. No fabricated empty ledger. |
| E16 | Authored ZIP traversal/symlink/duplicate/size and XML DTD/entity controls through the actual parser; `hostile_html` through real pytest/artifact/report, checking JSON retention and escaped self-contained HTML. |
| E17 | Separate saved-byte tamper copies, blocked-network replay twice, and a fresh online capture into a new directory. Compare old inventory before/after and record actual network/subprocess boundary observations. |
| E18 | Reproduce the documented Checkwash #419 escape with its real native verdict and disclosure; canonical custom/queue/octopus source controls remain unconfirmed. Do not modify the detector to obtain a different label. |

Recorded provider variants are explicitly authored/replayed controls. They may
prove mapping decisions but cannot stand in for real transport, crypto, hosted
isolation or installed subprocess evidence. Hosted positive merge-source
qualification and other unsupported cases remain open work; this document does not
shrink the 1.0 acceptance gate around the current producer.

## Reproduction and publication record

Each receipt names case/variant, immutable product and fixture sources, approved
policy digest, all tool/artifact hashes, actual environment, commands/API routes,
timestamps, original evidence inventory, native exits, expected and actual three
axes/exit, and any deviation. Publish only evidence whose data permissions were
reviewed; this authored fixture is designed to avoid customer data. Keep failed
attempts and a precise public support statement beside the eventual release.

Independent maintainer reproduction is still a separate release gate. Another
agent, a second local machine or this authored template does not satisfy it. A
maintainer must be able to start from public source/release/reference material
without private repository access or EC infrastructure, install the named
candidate and reproduce the documented inspection/replay outcome. Record that
actual reproduction without inventing a person, endorsement or approval.

## Recorded local fixture feasibility

Before publication, the authored E01 head was executed through the actual local
Linux/Python 3.11 isolated producer: four tests passed, with guest stop and
cleanup confirmed. The three actual locked CLIs observed Checkwash pass/exit 0,
Pinwash pass/exit 0, and Ledgerwash not-provided (native strict invocation exit
1). Optional ledger absence stays distinct from a required ledger's outcome;
the native bytes and coverage limits were retained.

The local `test_failure`, `zero_tests`, `all_skipped` and `hostile_html` variants
returned native pytest exits 1, 5, 0 and 1 respectively. The parser retained
one failed test, zero tests, four skips, and one failure containing the hostile
text respectively. Every run confirmed guest stop and cleanup. Renderer syntax,
consumer Actionlint and generated-profile identity checks also completed.
Original bytes are in ignored
`work/reference-qualification/local-1793945516972116330` and
`work/reference-authoring-01` during development. The producer source commit and
reference input hashes are recorded; the accompanying source-context record
explicitly identifies the surrounding development tree. These are local fixture
feasibility observations, not hosted, immutable full-product or E01–E18 PASS.

A separate authored local merge probe runs the same literal `tests` against
different head and canonical two-parent merge trees. The head marker produces
native exit 1; the actual merge marker produces exit 0. Both setup exits are 0,
and both executions confirm stop/cleanup with no boundary errors. Original Git
objects, measured source inventories, tested commit bytes, stdout/stderr, JUnit
and visibly synthetic producer-declared receipts are preserved in
`work/producer-qualification/merge-1793946710126325151`. This demonstrates actual
checkout selection in the local isolated runtime. It establishes no GitHub
membership, signed workflow binding or hosted E03 PASS. The reusable helper is
`producer/merge_qualification.py`; it accepts only its built-in authored source.
