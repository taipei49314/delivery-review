# ADR-001: Separate observation profile from delivery readiness

Date: 2026-11-06. Status: accepted direction for implementation; qualification
pending. Related specification:
[SPEC-001](../specs/SPEC-001-github-observations.md).

## Context

delivery-review is intended to become an open-source tool whose claims and
evidence can be inspected and reproduced by an independent maintainer. The
maintainer's repositories are an initial dogfood environment, not a proprietary
runtime dependency or a substitute for external reproduction.

The frozen M0 contract defines readiness from explicit obligations, independent
evidence/freshness/policy axes and qualified binding requirements. It does not yet
define a complete executable GitHub mapper. Its selector contains workflow ID,
App ID, job/matrix and reusable producer identity, but not an initiating workflow
path, event or a deterministic run-selection algorithm. Reusing `producer_path`
for the initiating workflow would change the field's meaning.

The implemented first M1 store accepts a normalized report and preserves its
supporting bytes. Its replay verifies those bytes and recomputes normalized
obligations. That is useful and deliberately limited: it does not independently
recreate observations from GitHub responses. Requiring a complete report before
recording a response would also make initial collection failures awkward to save.

Implementing all producers, parsers, scanners and release qualification before
the first real API observation would defer important platform feedback. Conversely,
calling metadata collection a ready delivery would weaken the existing contract.

## Decision

Introduce a separate versioned **observation-only profile**, raw capture and derived
observations format as specified in SPEC-001. The first executable boundary is
same-repository PR-head association on GitHub.com Actions `pull_request` runs.
An operator-approved local profile explicitly names repository, workflows, paths,
event, App identity, expected job labels, selection algorithm and resource bounds.

Run eligibility and latest selection precede path/PR/App/job association checks.
Native status never selects a preferred result. A missing PR association remains
unconfirmed and cannot trigger fallback to an older success. Raw replay derives
selection and observations from preserved response records and the saved profile,
not a saved normalized report. The first profile makes no runner checkout,
producer, pytest or scanner claim.

The observation command has operational success/error semantics. Its output does
not contain readiness, contract-v1 decisions or binding levels. A complete
observation can reveal a failed run; a partial observation can still preserve a
known failure. Neither is silently converted to a product report.

Capture is permitted before a complete subject exists. Original bodies are saved
before mapping, with bounded metadata and a final capture manifest. A stopped
capture can preserve an API failure without fake SHAs or a fake successful report.
This is a minimal collector-specific addition; the existing saved-report store
and reader are not generalized or replaced as part of this decision.

## Compatibility and frozen baseline

This is an additive development boundary, not a weakening of contract v1:

- The six M0 schemas, frozen normative documents, 40 case expectations and freeze
  inventory remain unchanged. No historical expected result is rewritten.
- Observation `profile_version`, `capture_version` and `observations_version` are
  independent namespaces. A numeric value of 1 does not make them contract-v1 or
  evidence-bundle-v1 documents. Consumers dispatch on kind and version and reject
  incompatible formats.
- Existing evidence bundle-v1 and saved-normalized replay retain their behavior,
  including offline report exit 2. New raw replay neither treats their normalized
  report as provider truth nor upgrades saved binding claims.
- Workflow paths/events/selectors in the new profile are not silently accepted as
  unknown fields in a frozen v1 policy. A future readiness profile must formally
  bind every decision-relevant selector to its approved policy identity.
- Breaking changes to existing field meanings or readiness semantics still require
  a contract version change and migration notes. Expansion of observation semantics
  requires a reviewed profile/mapper version; unknown versions fail closed.

The amendment records why a separate profile is introduced and how it interacts
with the baseline. It does **not** amend the frozen readiness or release promises.

## Effect on acceptance and release gates

SPEC-001 controls supply narrower, separately recorded evidence relevant to E02,
E04, E06, E07, E08, E09 and the raw-replay portion of E17. They do not close those
full product obligations automatically. They do not establish artifact/producer
binding under E10, actual test execution under E14, installed engine behavior or
the complete E01 happy path.

The producer, JUnit and scanner work is deferred in implementation order, not
removed from the stable release scope. A later full readiness report must retain
its declared required obligations as missing/unsupported until implementation and
evidence satisfy them; requirements cannot be deleted to manufacture readiness.

Linux/Python 3.11 remains the initial product qualification target in the frozen
contract. Windows/Python 3.12 local development and tests are accurately labeled
development evidence. This ADR does not claim Linux qualification or public
Windows support. Any change to the declared release platform needs its own
documented decision and actual installation/flow qualification.

Independent adoption requires a public, licensed fixture/reference path and clean
installation evidence without access to private repositories or EC. The existing
EC route may continue validating this project's own code; cross-repository EC
execution is an optional future adapter, not the canonical public product path.

## Alternatives considered

1. **Extend frozen v1 selectors immediately.** Rejected for this slice: it would
   mix metadata observation with a readiness contract before producer/source
   requirements can be fulfilled. A later deliberate readiness revision remains
   possible and must preserve old readers and historical evidence as appropriate.
2. **Use a reduced v1 policy to obtain a green result.** Rejected: omitting required
   tests/scanners or adding an unused engine pin would obscure the narrower claim.
3. **Specialize around the maintainer's EC workflow.** Rejected as the core path:
   it would make private operational details an adoption requirement and would not
   exercise an ordinary same-repository GitHub Actions chain.
4. **Treat a saved normalized report as raw replay.** Rejected: it would reproduce
   previous claims without testing the source-to-observation mapper.

## Evidence required before calling the profile qualified

The implementation must pass SPEC-001's deterministic mapping and failure controls,
preserve the old baseline, and record a controlled live same-repository PR example
at immutable product and fixture sources. Capture/API feasibility receipts and
the selected-run/job/check relationships must be reviewable. A maintainer outside
the implementation team must be able to reproduce the documented behavior from
publicly distributable inputs and a clean environment before public qualification
is claimed. None of those outcomes is asserted by accepting this ADR.
