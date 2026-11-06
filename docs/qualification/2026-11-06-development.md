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
| `078c032654f98249a2799588dd3bb82032a37d2e` | Clean native Linux / official Python 3.11.16 | 549 tests passed in 22.778 seconds. Frozen validator, build, fresh install, packaged resources, collector identity and byte-identical installed observation replay passed; source stayed clean. |
| `078c032` runtime with pending documentation-only changes | Local Windows / Python 3.12 | All 549 tests passed in 257.183 seconds. This working-tree run is separate from exact-commit pool CI. |
| `078c032654f98249a2799588dd3bb82032a37d2e` | Existing EC Windows / Python 3.12.10 lane | 549 tests, zero failures/errors/skips; 138.265-second test step and 152.547-second workload. Frozen validator and tracked-source cleanliness passed. |
| `3d3e54599066c98f52384c66953b130a01eaaff8` | Clean native Linux / official Python 3.11.16 | 552 tests passed in 21.092 seconds; frozen validator, clean build/install, resources/identity and installed observation replay passed. |
| `b9c1d2e9ba459882d8b8c94bbe09f7f93f8e9d32` | Clean native Linux / official Python 3.11.16 | 605 tests passed in 36.421 seconds; frozen validator, clean build/install, resources/identity and byte-identical installed observation replay passed. |
| `b9c1d2e9ba459882d8b8c94bbe09f7f93f8e9d32` | Existing EC Windows / Python 3.12.10 lane | 605 tests, zero failures/errors/skips; 193.437-second test step, 202.890-second workload. Exact-source input digests, frozen validation and source cleanliness passed under unchanged limits. |
| `d694175a475d6cdc109140797935de47652bddef` | Clean native Linux / official Python 3.11.16 | 635 tests passed in 25.157 seconds; all 24 source/build/install/offline-resource operations passed with a clean checkout. |
| `d694175a475d6cdc109140797935de47652bddef` | Existing EC Windows / Python 3.12.10 lane | 635 tests, zero failures/errors/skips/xfails; 323.703-second workload under unchanged limits. Exact-source contracts and cleanliness passed. |

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

The later clean native Linux `b9c1d2e` wheel is 206,629 bytes with SHA-256
`0023e96a9d0072bc35423f699dc8a9747953fb550832a7d498bc306542b6b590`;
its sdist is 311,741 bytes with SHA-256
`b06d98f2e4f8e1a025db0aba202a35ca7785f79d17454ad90452657dc4e48e30`.
Original build/install receipts are in `work/full-qualification-linux-b9c1d2e`.
The exact-source Windows check is `127786230835`; its private EC receipt is
retained locally and is not part of the public evidence export.

## Actual inspect, replay and refresh

The clean installed `d694175` candidate completed the actual qualified head
chain on [reference PR4](https://github.com/taipei49314/delivery-review-reference/pull/4):
report `inspection-374f103f6d94f5d330c1b97347311bf93797f458`, observed
`2026-11-06T08:58:22Z`, was `complete/current/clear`, ready, exit 0. It retained
11 original API records, exact source and native engine observations, and fresh
official cryptography bound to the two reviewed producer resources. A separate
actual merge-mode inspection also passed. These are measured variants, not a
blanket claim that E01–E18 or stable 1.0 is complete.

Its development wheel is 212,144 bytes with SHA-256
`4333c7e0acbed630b936961df94ed5cf5f02318f421c339087813c94d2d846c3`;
sdist is 322,884 bytes with SHA-256
`2312798265e84f3a65b6d6372c2bc31649c3497f439964ca8dc1b89b79e8fd35`.
The exact-head Windows check is `127806756084`. Private EC originals remain
local, outside public qualification evidence.

Two actual disconnected replays of that qualified envelope each ran fresh
pinned official cryptography, preserved source/qualification/observation time,
and produced byte-identical JSON/Markdown/HTML with `complete/unknown/clear`,
exit 2. One-byte saved-file tampering caused verify/replay to refuse before a
new report existed. A genuine online refresh created
`inspection-7569c43c9a69f7b4f1d32113f072d1dbea9acf96` at `09:07:32Z`, with
11 new successful observation requests and `complete/current/clear`, exit 0.
The original tree stayed unchanged. Receipt
`work/e17-qualified-envelope-linux-d694175-02/result.json` has SHA-256
`882bf20a405f393c0ccc3aad0901e9aa67597cb14064d5b5ac17699cc44dbf90`.
The first E17 controller expected the wrong internal storage diagnostic; its
failed receipt remains retained separately, before the explicit correction.

Installed-reader compatibility at this same commit also compared three actual
historical envelopes against their original installed readers: legacy `8bb4337`,
signed `3d3e545`, and profile-v2/plan2 ambiguous-artifact `b9c1d2e`. All three
report formats remained byte-identical under blocked networking, with unchanged
original trees/plans/times. Fresh official signature verification did not upgrade
the old signed capture's missing retained qualification. Receipt
`work/plan3-legacy-compatibility-03/result.json` has SHA-256
`ab6bd51895813b416d787410e136762b61fbb3a6d630de2900e6c79537c4bdb2`.

Controlled original-byte E10/E14/E16 composition matched all 23 preregistered
variants, with real baseline crypto, unchanged installed package and original
capture, no network and no qualification override. A separate 19-case original
API control set matched 18 and exposed the resolved-listing wrong-App defect
described in ADR-005. The failing source and original report remain retained;
the implementation correction requires a new exact-source qualification.

Other actual hosted functional, exemption, policy, ledger and known-escape
variants are recorded under `work/actual-product-controls-d694175-01` and
`work/actual-policy-ledger-d694175-01`. The extra disallowed-partial-skip
controller originally expected a block where the contract permits a missing
obligation with clear policy: its incorrect authored expectation is preserved
and explicitly amended, without changing frozen cases or product behavior.
One optional-partial-ledger observation failed to acquire merge-source proof
with `source_quarantine_link`; its incomplete result is retained. A separate
fresh attempt with the same profile passed. Twelve diagnostic native fetches did
not reproduce that refusal, so its exact transient cause is not claimed proven.

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

The third reference [PR 3](https://github.com/taipei49314/delivery-review-reference/pull/3)
pins `078c032` on another distinct base/head pair. Run `42181483713` passed
platform identity, trusted dependency/image setup and exact source verification,
then refused at the isolated-tests stage before producing a receipt or signature.
Its original failure is retained; a host/runtime preflight investigation does
not count as successful hosted producer qualification.

The clean installed `078c032` candidate then inspected PR 3 using the real
platform, exact Git objects and pinned engines. It saved 11 platform observation
responses plus the separate merge-reference proof, verified its sealed envelope,
and returned `incomplete/current/block`, exit 2. The failed selected job remained
a block and missing test evidence remained incomplete. The full inspect took
13.883 seconds on native Linux. This is an actual negative diagnostic, not E01.

The Docker setup failure was independently reproduced with the pinned CLI and
Buildx: a successful trusted image build populated its configuration directory,
and the unchanged execution preflight refused that nonempty directory. Commit
`3d3e545` preserves build state and creates a separate new empty execution config.
Both platforms passed 53 focused producer controls and independent review.

The fourth reference [PR 4](https://github.com/taipei49314/delivery-review-reference/pull/4),
run `42183133333`, completed successfully with producer `3d3e545`. It created
artifact `13486795225` (1,926 bytes), whose original ZIP SHA-256 is
`abe09663a76a9419b91e4bb2f334ec0b84bc34fda5c53c27fdbe04e517db0672`,
and signed the measured subject. The clean installed verifier independently
acquired source, original archive/JUnit and custom attestation, then ran official
cryptographic verification. Its report was `incomplete/current/clear`, exit 2:
the only unmet required obligation was `producer_not_qualified`. The registry
was not overridden. The native full inspect took 17.634 seconds.

That actual signed-evidence capture was replayed twice with the installed
`3d3e545` reader inside a real disconnected network namespace. Both fresh
official cryptographic verifications exited 0; the original tree/timestamps
stayed unchanged. Both reports were `incomplete/unknown/clear`, retaining the
original qualification gap, with byte-identical JSON, Markdown and HTML. The
JSON SHA-256 was `7622e25c5f30ff32a53791464da1919187ebc34d3d3ff97afbe2bc9ddfc62474`.
Raw captures and separate fresh crypto audits remain under
`work/public-reference-inspection-linux-3d3e545-01/` and
`work/signed-reference-offline-replay-3d3e545-01/`.

Successful signing alone does not complete producer qualification. Zero-test,
all-skipped, native-failure, merge-source, boundary and replacement controls
remain separately recorded requirements before a registry entry or E01 claim.

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

The profile-v2 changes received a separate actual compatibility check against
both the installed `8bb4337` PR 1 reader and installed `3d3e545` signed PR 4
reader. Both original v1 captures produced byte-identical JSON/Markdown/HTML
with the new reader under a genuinely disconnected network namespace. Signed
PR 4 used a fresh pinned official verifier on each side; both exited 0. Original
trees, observation times, profile versions and execution plans stayed unchanged,
and no qualification was added. The 78-file receipt is retained under
`work/profile-v2-legacy-compatibility-01/`.

The installed `b9c1d2e` then inspected the actual new artifact attacks, keeping
the producer pinned to `3d3e545`. [PR 19](https://github.com/taipei49314/delivery-review-reference/pull/19)
preserved its original signed ZIP before deleting artifact `13486744886` and
reuploading the identical ZIP as `13487367915`. Official cryptography exited 0,
but the new artifact identity did not match the signed claims: the test
obligation was untrusted with `attestation_claims_unconfirmed`.
[PR 20](https://github.com/taipei49314/delivery-review-reference/pull/20)
created artifact `13488020622` before the producer uploaded `13487253094` with
the same exact name. Both existed; upload success did not establish uniqueness.
The collector refused selection with `artifact_ambiguous`. The initial `b9c1d2e`
outer report reduced that to `artifact_missing`; the subsequent v2 reporting
correction preserves the original scoped ambiguity instead. Both actual initial
reports were `incomplete/current/clear`, exit 2, independently of the empty
qualification registry. Those original reports remain retained under
`work/public-reference-inspection-{replacement,precreation}-linux-b9c1d2e-01/`.

Native engine regression and its explicit two-case expectation correction are
recorded in [ENGINE-REGRESSION.md](../ENGINE-REGRESSION.md). They preserve the
documented Checkwash #419 escape and do not establish whole-product acceptance.
Hosted boundary/signature qualification, complete E01–E18 receipts, independent
maintainer reproduction and the stable release remain open gates.

Original local receipts are kept under ignored `work/full-qualification-*`,
`work/full-installed-probe-windows-732729d`, `work/full-refresh-linux-732729d-02`,
`work/ec-qualification-*` and `work/hosted-reference`. Private captures and EC
receipts must not be copied into public release material.
