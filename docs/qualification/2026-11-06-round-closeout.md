# 2026-11-06 round closeout

This round closes at the user's request. Development remains `1.0.0.dev0`;
no stable tag or complete 1.0 qualification is claimed. Exact-source qualification
results are also recorded in the development PR and its checks.

## Completed and retained

- Fixed head/merge producer `3d3e545` has two reviewed, digest-bound admission
  resources and a public original-evidence archive.
- Clean installed `d694175` passed 635 tests on Linux/Python 3.11.16 and
  Windows/Python 3.12.10. Actual public head/merge flows reached
  complete/current/clear, with source, native engines, artifacts and official
  signature verification retained.
- Two genuinely offline qualified replays matched all report bytes; tampering
  was refused; online refresh produced a new report and preserved the original.
  Three historical envelopes also replayed byte-identically without upgrading
  missing original qualification.
- Actual functional, exemption, policy, ledger and known-escape controls were
  retained, along with controller expectation mistakes and acquisition failures.
  An independent audit reverified and replayed 28 original live envelopes.
- Original-byte E10/E14/E16 controls matched 23 variants. Six E11/E12 composition
  controls preserve real native failures and independent blocking fingerprints;
  authored API/parser/coverage inputs are explicitly distinguished from hosted
  execution.
- Actual head and attempt races yielded complete/stale/clear. Matching new-head
  evidence reached ready. A newer pending run remained incomplete, an actual
  later failed attempt remained complete/block, and a failed-only rerun did not
  combine test artifacts from different attempts.
- Corrections in this closeout cover the resolved-listing wrong-App refusal,
  scoped execution-history diagnostics, closed CLI integrity errors, and Git
  object publication via rename while keeping hardlink checks strict.

The earlier partial-skip controller expected an unwarranted policy block; its
failure and explicit expectation amendment remain retained, with no frozen
expectation or product change. The first optional-partial-ledger inspection
retained a source acquisition refusal; a separate fresh attempt passed. These
are not silently relabelled successful original runs.

## First task next round: independent base-tip proof

The actual E04 base race exposed a release-blocking gap: PR35's target branch
advanced while the PR API continued returning its old `base.sha`, including
after roughly ten minutes. Current mapper 2 rechecks that PR field and therefore
cannot establish target-branch freshness in this scenario. The base-race
inspection was stopped and is not PASS. [Original public responses and hashes](../../qualification/base-tip-drift-d694175/README.md)
are committed for reproduction.

The next implementation should introduce an explicit mapper 3 protocol:

1. Capture the exact target branch reference from the initial and final
   independently observed `PR.base.ref`, using the fixed repository GET route.
2. Validate scope, route, reference name, commit type, full SHA, response
   completeness and ordering. Derive the current base tip only from that proof;
   do not fall back to the stale PR SHA when it is missing or invalid.
3. Use the independently verified final tip in report recheck. Preserve known
   head drift even when other final proof is missing. Keep mapper 1/2 saved-byte
   replay unchanged.
4. Review the fixed producer's corresponding base-source assumptions, qualify
   any changed producer revision separately, and exercise a new dedicated actual
   base race. Preserve PR35 and every earlier receipt.

An unfinished root-only route/capture/collector patch was saved locally under
ignored `work/mapper3-base-reference-unfinished.patch` and removed from the
working implementation before closeout. It is not reviewed, tested or shipped.
The mapper design has not been implemented. This avoids publishing a partially
connected protocol as a completed fix.

## Remaining release work

Complete the outstanding E01–E18 variants and aggregate exact-source receipts.
The pending-head timing window was missed; custom/queue inputs are rejected
out of scope, while the actual recorded unsupported-relationship control proves
octopus refusal only. The distinct newer failed-run control must retain its
exact history: an older successful attempt is archived, but the current older
run listing had already advanced to a failed attempt. Do not relabel these as
unobserved variants.

The already-issued distinct new failed run `42195155149` stopped in the fixed
producer with `event_source_ref_mismatch` before guest measurement. It produced
no artifact; the actual report was incomplete/current/block, exit 2. This is a
retained identity refusal and latest-run selection observation, not the missing
complete signed failing-test variant. No retry or boundary relaxation was made.

Finish a reviewed public product-evidence export and installation/reproduction
guide, then obtain genuinely independent external maintainer reproduction.
Local agents do not satisfy that gate. Private EC/Pinwash receipts remain local.
Only after the required gates pass should exact release artifacts and a stable
1.0 tag be published. See [the release gates](../RELEASE-1.0.md).
