# delivery-review 1.0 release gates

Status: implementation in progress. `1.0.0.dev0` is a development version, not
stable qualification. This checklist preserves the original product contract,
M0 acceptance expectations and ADR-001 release boundaries.

## Supported outcome

A maintainer can install a public, licensed release, inspect a fixed GitHub.com
PR using an independently approved policy, inspect original evidence and the
three independent decision axes, replay without network access, and refresh into
a new directory. The verifier never installs or runs reviewed project code.
The standard producer runs pytest in GitHub Actions separately from the verifier.
The product helps human review; it does not certify correctness or auto-merge.

The initial qualification target remains Linux/Python 3.11. Other tested
environments are recorded explicitly; development tests do not imply support.
Only declared source modes, producer revisions, pytest dialects and engine pins
may satisfy requirements. Unsupported inputs retain missing/error observations.

## Gates and required receipts

| Gate | Deliverable and acceptance evidence | Current status |
| --- | --- | --- |
| G01 Raw capture and replay | Create-once raw bytes, exact inventories, bounded acquisition, deterministic version-dispatched replay, independent freshness | Implemented; Windows/Linux development controls and public raw API probes; full candidate qualification pending |
| G02 Source identity | Exact repository/head/base/merge-base; independently established checkout; two-parent merge proof where declared; safe static object access | Static acquisition/restore implemented; real public/private fetches on Windows/Linux; full product source-mode qualification pending |
| G03 Producer and test evidence | Pinned reusable producer, independently bound artifact ID/digest/run/attempt/job, bounded ZIP/XML parsing, genuine zero/all-skip/failure controls | Fixed head/merge `3d3e545` admitted with original public evidence after 18 actual controls, independent offline cryptography and artifact attack review; full-product acceptance and exact-release validation remain separate |
| G04 Engine integration | Hash-pinned isolated public CLIs, immutable subject, preserved raw output/native verdict/findings/coverage, time/output limits, negative probes | Implemented; real three-engine static probes on Windows/Linux; complete-product acceptance pending |
| G05 Policy and reports | Approved selector identity, required obligations, unchanged frozen state algebra, JSON/Markdown/self-contained HTML, actionable evidence references | Composer and renderers implemented; authored cross-document/race/error controls pass; real qualified readiness chain pending |
| G06 Installed CLI | Source and wheel distributions, bundled offline schemas, release source identity, clean Linux/Python 3.11 install and complete inspect/replay/refresh | Installed development inspect/replay/refresh and clean native Linux builds passed; qualified positive chain and exact-release artifact qualification pending |
| G07 Acceptance | E01–E18 executed against declared product/version/environment; no synthetic fixture relabeled as integration | NOT_RUN |
| G08 Open-source adoption | Apache-2.0 source, dependency notices, contributor/security docs, public reference fixture/repo, independent maintainer reproduction without private access/EC | Product and reference repositories public under Apache-2.0; notices/contributor/security docs implemented; independent external reproduction pending |
| G09 Release | Exact source SHA, tested artifact hashes, qualification receipts, changelog, install/replay instructions; stable tag only after required gates pass | Not released |

## Implementation order

1. Finish raw mapper compatibility, installable resources and source provenance.
2. Integrate the pytest dialect and pinned scanners with bounded static execution.
3. Establish producer/artifact provenance using independent platform or verified
   attestation evidence. Names, timestamps, receipt claims and upload success are
   insufficient to identify the actual artifact created by a job.
4. Compose genuine evidence into the policy/report contract and render all outputs.
5. Run the controlled reference scenarios, install/replay qualification and
   independent reproduction; publish only the demonstrated support scope.

## Evidence accounting

Every qualification receipt records source revision, artifact hashes, command,
environment, timestamp and actual outcome. Product failures and unresolved
coverage stay visible. Tests of authored JSON/XML are parser controls, public API
metadata captures are observation evidence, and subprocess probes are native CLI
compatibility evidence; none alone is an E01 complete-product PASS.

The frozen 40 cases and 20 baseline files remain unchanged. Changes to mapper
semantics are versioned with an ADR and old-byte replay tests. A new readiness
profile must bind every decision-relevant selector to its approved identity.
Local hash inventories detect inconsistency, not publisher authenticity.

The planned multi-repository pilot remains a usability learning target rather
than an accuracy claim. Independent reproduction is a distinct stable-release
gate: another local agent or an authored fixture does not count as an external
maintainer. Until that evidence exists, its receipt stays pending.
