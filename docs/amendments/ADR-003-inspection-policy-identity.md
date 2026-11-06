# ADR-003 — bind the complete inspection profile to contract-v1 policy identity

Date: 2026-11-06. Status: implementation decision; product qualification pending.

## Problem

Contract v1 identifies an approved policy using its canonical digest. Its frozen
selector does not contain the initiating workflow path/event, artifact protocol,
pytest dialect or attestation predicate. ADR-001 prohibits silently accepting
those fields as unknown policy keys or reusing producer_path for another purpose.
A separate unbound profile would let acquisition choices change while a report
continued to display the same approved policy digest.

## Decision

Introduce `delivery-review-inspection-profile`, profile_version 1. It contains a
frozen v1 policy, SPEC-001 GitHub observation profile, producer protocol selectors
and native engine options. The entire profile must be approved outside the
reviewed PR. It is deliberately distinct from the observation-only profile.

Let B be the whole profile with only `policy.policy_id` removed. Set:

```text
policy.policy_id = "profile-" + sha256(project_canonical_json(B))
```

The existing report then includes the unchanged contract-v1 canonical digest of
that policy. This binds every external selector through the policy ID without a
circular digest. The complete original profile must be retained as raw evidence
in a real inspection bundle. Readers validate the derived ID before collection
or replay. Altering a selector and resealing creates a different policy identity;
resealing does not grant approval, authenticate a signer or weaken a requirement.

`seal_inspection_profile` is a construction helper for an operator to review.
`load_inspection_profile` rejects missing/unknown fields and identity mismatches.
No fields are inferred from currently green jobs or the reviewed PR.

## First supported profile

- External-file policy, exact repository ID, all three candidate engine pins.
- Checkwash and Pinwash required; at least one required pytest report.
- Static engines use fixed high threshold; Ledger completeness behavior is explicit.
- GitHub workflow ID/App/name selectors must agree with the acquisition profile.
- pytest evidence requires workflow binding to a full pinned producer revision.
- Fixed receipt/JUnit members, pytest 8.4.2 xunit2, fixed attestation predicate and
  artifact naming algorithm. The name locates evidence; SPEC-004 independently
  binds the actual artifact ID/digest/run/attempt/job.
- A direct CI-only requirement is platform-bound. Unsupported matrix or selector
  combinations are rejected before acquisition rather than guessed.

Validation of this format is not implementation of every producer or source
mode. A supported policy format can still have unimplemented or missing evidence
and cannot receive ready status on that basis.

## Compatibility

No frozen schema, state algebra, baseline file, expected outcome or historical
report changes. Old contract-v1 readers continue to see a valid policy ID and
digest; they still do not authenticate saved claims. Observation captures remain
observation-only. A full readiness composer must require a verified complete
inspection context and retain all proof bytes before deriving workflow binding.

Receipt/producer API proof fields remain their existing meanings. Attestation
proof is additional independently checked evidence, not a relabeling of a
receipt or an invented GitHub API response. If later composition cannot express
a truthful required claim in contract v1, it needs another explicit format
decision; this identity scheme does not authorize coercing native facts.
