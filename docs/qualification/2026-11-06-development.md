# Recorded development qualification — 2026-11-06

These are observed development results, not stable 1.0 or E01–E18 acceptance.
Every result belongs to the named source and environment. Later commits do not
inherit an earlier commit's test receipt. Frozen expectations remain unchanged.

## Clean source and installed packages

| Source | Environment and operation | Actual result |
| --- | --- | --- |
| `732729d87943b173334b8bffead665eaba596cfb` | Windows / Python 3.12.10, complete unittest suite | 501 tests passed; 188.502 seconds. |
| `732729d87943b173334b8bffead665eaba596cfb` | Linux / Python 3.11.16, complete suite | One error in 501 tests: `/proc` disappeared while the cleanup assertion read it. Original failure retained. |
| `9c5ddb9d805966d14b90ef016693b0c5f4272380` | Windows / Python 3.12.10, complete suite | 527 tests passed; 343.400 seconds. Clean build/install and installed tool provisioning also passed. |
| `9c5ddb9d805966d14b90ef016693b0c5f4272380` | Linux with interpreter/stdlib on a Windows-mounted filesystem | Two errors in 527 tests: cold worker startup exceeded its test deadline; kernel process disappearance also surfaced as `ProcessLookupError`. Original failure retained. |
| `8bb4337d5c7f643e10acd56b5fe1fc28a542fdd8` | Clean native Linux / official Python 3.11.16, complete suite | 527 tests passed; 23.263 seconds. Frozen validator, build, fresh installation, packaged resources, collector identity and installed observation replay passed; source remained clean. |
| `8bb4337d5c7f643e10acd56b5fe1fc28a542fdd8` | Existing EC Windows / Python 3.12.10 lane | Old 90-second test-step limit expired. No completed unittest result; retained as a failed run. |
| `1fa5c2892cac5ab4937b0755bbe69450037ee013` | Existing EC Windows / Python 3.12.10 lane | 527 tests, zero failures/errors/skips; 159.297-second test step, 166.422-second workload. Frozen validator and tracked-source cleanliness passed. |
| `73559527ab1bfc75b85da93e6ce634e6ec1c2e88` | Clean native Linux / official Python 3.11.16 | 535 tests passed. Frozen validator, clean build, fresh installation, packaged resources, collector identity and byte-identical installed observation replay passed. |

The successful native Linux environment was freshly installed with the already
verified uv 0.12.12, using the official Python build-standalone 3.11.16 release
dated 2026-09-01. A provisional copy from the Windows-mounted filesystem had
incorrect ancillary terminfo symlink resolution; it was explicitly excluded from
qualification. No interpreter fallback or increased HTTP test deadline was used.

The EC retry changes only the unittest step's maximum to 480 seconds within the
existing shared 540-second deadline and admitted ten-minute outer limit. Its
actual [native run](https://github.com/taipei49314/estate-consolidation/actions/runs/42177131657)
and private receipt commit `483dba27fcbadb8148369a5305d8c62f3111fbc1`
remain separate from public product acceptance. The earlier timeout run
`42176384573` and its receipt remain retained. Public runtime use does not require
EC access, and a Windows pool pass does not qualify Linux.

The native Linux `8bb4337` package artifacts are:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `delivery_review-1.0.0.dev0-py3-none-any.whl` | 195865 | `93f557704153361eda62ff787431e6f7ff6cc65121279579d85a2d40d9743d68` |
| `delivery_review-1.0.0.dev0.tar.gz` | 282079 | `ce5441d4d067b465f91b015ea00137c31ca6eb16a00b1114375ae546c2738d06` |

These hashes identify observed development artifacts. They are not a stable
release, publisher signature, or a claim that artifacts built on different
operating systems are byte-identical.

## Actual inspect, replay and refresh

An installed `732729d` candidate inspected the owner's real Pinwash PR 81 using
an external diagnostic policy requiring the new producer, which that PR had not
adopted. Its original API response identifies Pinwash as **private**; all related
source, captures and archives remain local and are not published with this repo.
The operation used authorized credentials and retained 1,724 Git objects,
332 source files, original API bodies and actual pinned-engine output.

The live report was `incomplete/current/clear`, exit 2. Checkwash and Pinwash
exited 0; Ledgerwash's strict invocation exited 1 for absent evidence, retaining
its native result while the policy treated optional absence separately. Missing
producer/artifact evidence was not promoted by the existing green CI jobs.

Windows replay twice produced identical canonical report bytes. Installed Linux
replay also ran twice inside a real `unshare --net` namespace: only loopback was
present, there was no route, and a direct TCP control returned `ENETUNREACH`.
Both Linux replays returned `incomplete/unknown/clear`, exit 2, with identical
JSON, Markdown and HTML bytes. Original source/envelope bytes stayed unchanged.
This case had no producer artifact, so it does not establish the signature path;
the separate actual signature experiments remain documented in SPEC-004.

Installed Linux refresh acquired 16 new API responses, exact Git source and
three new native engine executions. Its observation time changed from
`2026-11-06T06:24:23Z` to `2026-11-06T06:42:03Z`, and its new report was
`incomplete/current/clear`, exit 2. The old tree, manifest and timestamp were
unchanged. A separate raw-body tamper copy was refused with exit 2. An anonymous
attempt correctly received 404 for the private repository; that failure remains.

A separately installed `8bb4337` candidate inspected the public authored
reference PR 1 anonymously. It retained 11 original platform observations,
22 Git objects and output from all three pinned engines. Its result was
`incomplete/current/block`, exit 2: the selected job failed and its required
pytest artifact was absent. This observed negative result is a full CLI
diagnostic, not E01 or positive producer qualification.

## Public reference and remaining gates

Both [delivery-review](https://github.com/taipei49314/delivery-review) and its
[authored reference repository](https://github.com/taipei49314/delivery-review-reference)
are public Apache-2.0 repositories. Product PR 1 merged the tested development
foundation; its merge is not stable release qualification.

The first public reference [PR 1](https://github.com/taipei49314/delivery-review-reference/pull/1)
pinned producer `8bb4337`. Hosted run `42177088855` completed OIDC bootstrap,
the exact producer checkout, Python setup and pinned host-dependency installation,
then refused the measurement stage. It produced no receipt/JUnit artifact or
attestation. Original platform records and the complete log remain retained.

The workflow lacked the Checks API's required `checks: read` scope. The fixed
candidate adds only that read permission plus allowlisted stage diagnostics;
the initial generic log does not prove which route failed. A separate
[PR 2](https://github.com/taipei49314/delivery-review-reference/pull/2) preserves
the first base/head pair and pins the reviewed `7355952` follow-up. Hosted run
`42178476087` passed setup but refused at platform identity. It also produced no
qualified artifact or signature. GitHub REST API `2026-03-10` had removed
`merge_commit_sha` from the original PR response. A fresh API and independent
canonical Git probe confirmed the exact merge ref and ordered base/head parents.

[ADR-004](../amendments/ADR-004-github-merge-reference.md) records the resulting
versioned merge-reference proof. It requires both original reference bytes and
the canonical Git object; missing proof cannot fall back to the PR head.
Historical three-key execution plans retain their original semantics. The
new protocol's unit controls and real API probe do not establish hosted
producer qualification; the two failed runs remain retained separately.

Actual legacy compatibility was checked using the saved public PR 1 inspection.
The installed `8bb4337` reader and the new working-tree reader ran inside a real
network namespace with only loopback, no route, and an `ENETUNREACH` TCP control.
Both returned `incomplete/unknown/block`, exit 2, and byte-identical JSON,
Markdown and HTML. The original tree, observation time and three-key legacy
plan stayed unchanged; no reference was fetched or qualification added. The
canonical JSON SHA-256 was
`f3d3432ae3851c7714077a81a8331fed95817761a7fff7cca9d26883a63da529`.
Original source inventories and all replay logs are retained in
`work/legacy-plan-compatibility-v2-01/`.

Native engine regression and its explicit two-case expectation correction are
recorded in [ENGINE-REGRESSION.md](../ENGINE-REGRESSION.md). They preserve the
documented Checkwash #419 escape and do not establish whole-product acceptance.
Hosted boundary/signature qualification, complete E01–E18 receipts, independent
maintainer reproduction and the stable release remain open gates.

Original local receipts are kept under ignored `work/full-qualification-*`,
`work/full-installed-probe-windows-732729d`, `work/full-refresh-linux-732729d-02`,
`work/ec-qualification-*` and `work/hosted-reference`. Private captures and EC
receipts must not be copied into public release material.
