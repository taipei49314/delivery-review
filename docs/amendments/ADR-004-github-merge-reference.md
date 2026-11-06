# ADR-004 — versioned GitHub merge-reference observations

Date: 2026-11-06. Status: accepted for implementation; hosted qualification and
complete-product acceptance remain separate.

## Problem and evidence

GitHub REST API version `2026-03-10` removes `merge_commit_sha` from pull-request
payloads. A missing field under that API cannot establish that there is no merge
event source. Requiring it prevents the existing source proof from completing.
The removal is documented in GitHub's
[versioned breaking changes](https://docs.github.com/en/rest/about-the-rest-api/breaking-changes?apiVersion=2026-03-10#version-2026-03-10).

A bounded probe of the authored public reference PR obtained
`GET /repos/taipei49314/delivery-review-reference/git/ref/pull/2/merge` and
independently obtained the referenced commit. The returned reference was
`refs/pull/2/merge` and its object SHA was
`db2d791921eac93911fb0f45393da63a8c41a0a3`; the observed parents matched the
selected base and head. Original bytes and route receipts are retained in
`work/hosted-reference/run-42178476087/ref-probe/`. The reference response's
SHA-256 is `37028d667518c20539688587dd586684103c3895599116c67453f57b1c053efc`.
This is an API/source feasibility observation, not a passing producer run.

The [Git reference API](https://docs.github.com/en/rest/git/refs?apiVersion=2026-03-10#get-a-reference)
provides a single-reference read. References can move; a name alone is not an
immutable checkout identity. The narrow implementation accepts only the exact
numeric PR merge-reference route and then verifies the original commit object.
It does not introduce general branch resolution, matching-reference search or
caller-selected refs.

## Decision

Introduce the inspection event-source basis `github_merge_reference_v1` in an
explicitly versioned execution plan. For this basis, obtain and preserve the
original fixed-route request/response separately from the original PR response.
Do not add a synthetic `merge_commit_sha` property to saved PR JSON or rewrite
any original platform bytes.

The source relationship requires all of the following:

1. The original request is exactly
   `/repos/{approved_repository}/git/ref/pull/{observed_pr_number}/merge`, with a
   complete HTTP 200 response, bounded body, valid observation metadata and safe
   response headers. Parse duplicate-free JSON; derive body digests from bytes.
2. The reference is exactly `refs/pull/{observed_pr_number}/merge`; its object is
   a commit with a full lowercase Git SHA and the matching fixed repository Git
   commit URL. A response for another PR, repository, namespace or object type
   does not satisfy the relationship.
3. Obtain the commit by the observed immutable SHA, rederive its canonical Git
   object hash, and parse the original ordered parent list. It must have exactly
   `[observed_base_tip, observed_pr_head]` as parents. A supplied parent list,
   matching tree or plausible branch name cannot substitute for these bytes.
4. Bind the independently verified attestation's source SHA to that exact commit.
   The authenticated event source and the measured test checkout remain distinct:
   the fixed head workflow tests the PR head; the fixed merge workflow tests this
   verified two-parent merge. Policy and exact producer qualification still
   determine which checkout mode is permitted.

`EventSourceFact` carries canonical `commit_bytes` and an optional original
`RawApiFact` reference. The new basis requires both; missing reference, partial
HTTP data, an unavailable object or a source conflict remains incomplete/error
evidence. It must not fall back to assuming the certificate source is the PR
head. Reference request/body bytes remain retained even when validation fails.

Under the new basis, the fixed reference and original commit establish source
identity. The deprecated PR property is retained but ignored, whether absent,
null or inconsistent; it is not an alternative proof. Reference/OIDC/canonical
object/parent conflicts still fail. The historical basis continues to use the
deprecated property under its original rules.

## Race handling

The reference is observed after the initial PR identity, and its commit is then
acquired by SHA. If the base/head changes between those reads, an inconsistent
ordered-parent relationship is refused. The producer obtains its own OIDC event
SHA and requires the observed reference to match it; immediately before signing,
it repeats the platform identity collection. A moved reference, changed subject
or changed selected execution cannot be substituted into the earlier measurement.

The full inspection still re-observes PR head/base and selected run/attempt after
source, scanners, artifacts and cryptographic verification. A demonstrated
head/base/attempt change remains stale under the frozen freshness contract. The
reference is a timestamped source observation, not a promise that a mutable ref
will never change. Unavailable or inconsistent proof cannot be promoted to ready
by selecting another source or an older green execution.

Offline replay performs no reference lookup. It checks the original saved
reference, original commit and signed evidence according to the saved basis.
Freshness remains unknown, even when all original relationships verify.

## Execution-plan and replay compatibility

The outer envelope remains `inspection_version: 1`. That is a storage inventory
format; the execution plan separately selects how its original source inputs
are interpreted. New live inspections save these exact plan keys:

```json
{
  "plan_version": 2,
  "event_source_basis": "github_merge_reference_v1",
  "engine_names": [],
  "stage_errors": [],
  "pr_url": "https://github.com/OWNER/REPO/pull/NUMBER"
}
```

The empty lists are an illustrative shape, not a successful inspection. Strict
readers reject unsupported plan versions, bases and extra fields. Older full
inspection readers accept only their historical three-key plan and therefore
reject the new shape rather than silently applying old source rules.

The exact unversioned shape `{engine_names, stage_errors, pr_url}` dispatches to
`legacy_pr_field_v1`. It retains the historical `PR.merge_commit_sha` relationship
and historical head-source behavior. Stray reference files cannot opt that plan
into the new proof; replay ignores them for source interpretation. Conversely,
the new basis cannot silently switch to legacy behavior when reference evidence
is absent. A direct binder call supplying a reference together with the legacy
basis is refused as a conflicting input, rather than implicitly changing its
meaning. No replay operation adds a missing proof by fetching current data.

Original reference metadata and bytes are stored at
`raw/event-source/reference/request.json` and `body.bin`; canonical event-source
bytes remain at `raw/event-source/commit.bin`. All are covered by the inspection
inventory, and proof inputs used by the binder appear in the report's retained
evidence inventory. Existing captures, plans, reports and cryptographic audits
are never rewritten in place. Refresh creates a new inspection with fresh facts
and an explicit new plan while preserving the approved profile.

## Contract and qualification boundary

Contract v1 already permits an independently established two-parent PR merge
source; it does not require a particular GitHub response field as the universal
proof format. This decision changes the provider observation mechanism, not the
meaning of head/base/merge identity, permitted checkout modes, evidence binding,
policy identity, freshness or readiness. The frozen schemas, forty expected
cases and three decision axes remain unchanged. The existing producer execution
profile and attestation predicate do not become newly qualified through this
adaptation; each exact workflow revision still needs its own hosted evidence.

Validation must cover exact and conflicting reference identities, wrong object
types/URLs, missing/partial bodies, canonical object/hash/parent mismatches,
unchanged PR bytes, mandatory new-basis inputs, legacy replay isolation and
rejection of unsupported plan versions. Authored controls establish these
boundaries; actual hosted execution and full-product receipts establish their
separate release claims.
