# Reviewed producer evidence

The bundled registry admits exactly producer commit
`3d3e54599066c98f52384c66953b130a01eaaff8`, separately for its fixed
`pytest-producer.yml` head workflow and `pytest-producer-merge.yml` merge workflow.
Both use literal `tests`, Linux x86_64 CPython 3.11.16 and pytest 8.4.2 xunit2.
This qualification does not release delivery-review 1.0 or establish E01–E18
complete-product acceptance.

The admission follows 18 actual public controls, independent review of original
bytes, 11 fresh official offline verifications of signed functional/boundary
measurements, 17 earlier cryptographic identity controls and three lifecycle
checks. The failed PR13 source acquisition is retained alongside its separately
successful PR26 fixture retry. Same-name precreation actually produced duplicate
artifacts; byte-identical reupload actually changed artifact identity. Neither
attack may acquire test credit. See [the control record](../docs/specs/SPEC-006-producer-execution.md#actual-public-hosted-controls-at-3d3e545).

## Original evidence

[Download the reviewed archive](producer-3d3e545-evidence-95605b33d82da5c11da7d04ca2543ab18a2b3e8fdb2beff35601f1e1d44b6e90.zip)
(2,382,366 bytes). Its SHA-256 is
`95605b33d82da5c11da7d04ca2543ab18a2b3e8fdb2beff35601f1e1d44b6e90`.

The archive contains 965 original payload files and a separate byte inventory.
It preserves original public API responses, fixture/source identities, original
ZIP/member/bundle bytes, cryptographic inputs/results, review records and explicit
omissions. The inventory SHA-256 is
`a7cd6db26585261067233516d331645db79d4fdea4bc53de91a9c9c071ba6e3a`.
Its README explains compressed bundle media types and the provenance of each
control. Public job logs remain local and hash-indexed; private EC and private
Pinwash inspection data are excluded. No original bytes were redacted.

Admission resources are separately digest-bound to avoid a circular archive:

- [Head qualification](../formats/producer-qualification-c143b95f6bb9a08910458dc3e54739bdeb2ee916ca3a9054a5ba22c22684cc29.json).
- [Merge qualification](../formats/producer-qualification-882617bde92a3bd7224b7312cb161b3645be36b64aa07f03e2d7ec065ffab6fc.json).
- [Release-owned registry](../formats/producer-qualifications.json).

An archive digest establishes retained-byte consistency. Trust in its publisher
and the admission decision remains separate. A PR cannot supply a replacement
registry, and a signed receipt alone does not prove qualification. A fresh
inspection must independently bind platform identity, source, artifact ID/digest
and signature to one admitted revision. Old captures lacking the exact retained
qualification receipt stay unqualified on replay.

## Scope of the review

Both workflow paths have direct source-mode and runtime controls. Most hostile
guest tests exercise their shared implementation through the head workflow.
Guest GID and stop/cleanup are enforced by reviewed producer gates before
signing; no separate retained hosted post-cleanup Docker inspection transcript
is claimed. The runner kernel, Docker daemon/image, pinned actions and GitHub
remain trusted components. Authored controls do not establish test correctness,
arbitrary project-command support or independent accuracy.

Complete installed-product acceptance and independent external maintainer
reproduction remain separate [release gates](../docs/RELEASE-1.0.md).
