# SPEC-007 — artifact acquisition and transient storage credentials

Status: bounded transport, create-once raw capture and offline mapping implemented
with authored boundary controls. Public artifact and attestation-bundle downloads
have separate development probes. This is transport feasibility, not producer/job
authentication or E01 qualification.

The fixed GitHub.com GET routes are artifact metadata, a run's artifact listing
with explicit page/per_page, and an artifact's ZIP download route. IDs and
repository locators are validated before process/network creation. There is no
write/delete endpoint or arbitrary URL input. API version is 2026-03-10.

Each operation runs in a first-party isolated Python worker with a parent-owned
deadline including DNS/connect/read. Original response bytes have hard caps:
2 MiB metadata and 25 MiB archive. Callers must lower these to the approved policy
and remaining total budget. The worker receives the token through stdin; the
credential is absent from argv, saved records and inherited child environment.
Proxy discovery and automatic redirects are disabled.

The archive route may return one HTTP 302 to the qualified HTTPS Azure Blob
storage domain. Only one account label followed by `.blob.core.windows.net` is
accepted. Userinfo, non-443 ports, fragments, control characters and other domains
are refused. The storage GET carries no GitHub Authorization header or API
credential. Further redirects are refused. This closed storage set reflects the
tested Actions v4 path; new storage hosts need deliberate qualification.

The REST API intentionally provides an ephemeral signed storage locator. That
locator and the redirect body may contain a bearer credential and are not saved.
The transport records original safe API headers/status plus the explicit marker
`body_retention: omitted_ephemeral_storage_credentials`. The final response and
ZIP bytes are preserved exactly with their own status/headers. It does not
pretend to have retained a redacted body as original bytes. Known GitHub tokens,
signed-locator echoes and potentially truncated secret prefixes cause refusal.
This boundary is specific to credential-bearing discovery/redirect metadata;
receipt/JUnit/archive/native evidence bytes are never silently rewritten.

The original archive SHA-256 must match independently acquired platform metadata
before parsing. That equality establishes consistency only. SPEC-004's signed
actual artifact ID/digest and independent job/check/producer chain are still
required for workflow binding. A name, upload timestamp or successful download
is not that proof. Pagination completeness and duplicate/artifact selection rules
belong to the collector above this transport, not a single GET.

GitHub documents the ZIP redirect and its short lifetime in the
[artifact download API](https://docs.github.com/en/rest/actions/artifacts?apiVersion=2026-03-10#download-an-artifact).
Attestation discovery responses contain signed `bundle_url` values. The bounded
discovery worker consumes those locators privately, records the original
discovery body's SHA-256, size and completeness with an explicit omission marker,
and retains each downloaded signed bundle's original bytes. Storage requests
carry no API authorization; unknown storage hosts and further redirects are
refused. Every pagination relation must preserve repository, subject digest,
predicate type and page size. Cursor loops and incomplete pages cannot establish
complete discovery. Cryptographic verification does not rely on the locator
remaining valid. Discovery metadata and bundle bytes alone do not verify a
signature.
[Attestation discovery API](https://docs.github.com/en/rest/repos/attestations?apiVersion=2026-03-10#list-attestations)

## Versioned bundle storage decoding

Real storage responses use `Content-Type: application/x-snappy`, containing a
Snappy block rather than JSON. The official
[GitHub CLI 2.98.0 client](https://github.com/cli/cli/blob/v2.98.0/pkg/cmd/attestation/api/client.go)
also explicitly decodes this block before parsing the bundle. It is incorrect
to pass compressed storage bytes directly to the offline verifier's JSON input.

Artifact mapper version 2 uses `bundle_encoding` decoder version 1 for this
explicit media type. The separately retained transport bytes and headers remain
unchanged. Decoding produces the original uncompressed bytes, without JSON
reserialization, and only those bytes go to official cryptographic verification.
Absent content type and the qualified JSON media types retain their input bytes;
there is no compression guessing or gzip/framed-Snappy fallback. Reader support
for mapper version 1 keeps its original opaque-bundle mapping unchanged.

The decoder follows Google's
[Snappy block format](https://github.com/google/snappy/blob/main/format_description.txt):
a bounded length varint, literals and three copy forms with overlapping-copy
semantics. It refuses invalid offsets, truncated operands/literals, incorrect
output lengths, appended bytes and framed streams. Encoded and decoded bundles
are each capped at 2 MiB, with smaller caller limits honored. The declared output
bound is checked before allocation, and aggregate decoded candidates cannot
exceed the capture's total-byte cap. Seven authored encoding controls cover
these boundaries; the real probe below separately confirms interoperability.

## Create-once artifact capture and offline mapping

`delivery_review.evidence.artifacts.collect_artifacts` takes the independently
selected repository ID/name, run ID, attempt and check-run ID, a new destination,
the bounded artifact/discovery transports, collector version/source SHA, clock
and `ArtifactLimits`. Its fixed candidate name is
`delivery-review-{run_id}-{run_attempt}-{check_run_id}`. Names select candidates;
they do not prove that the intended job produced them.

The returned `ArtifactCapture` contains immutable `manifest_bytes` and a
read-only `raw_files` mapping. The disk completion marker is `capture.json`.
Each response has a sequential record, fixed request path, observed timestamp,
native HTTP status, safe response headers, explicit transport error and a raw
body inventory entry. Original listing, detail and archive bytes are stored
under `raw/rNNNNNN.body`; bundle bytes use `raw/rNNNNNN-bNNN.body`. Bytes are
saved before payload parsing or candidate selection. Malformed JSON, bounded
prefixes and native error bodies remain available in a completed partial capture.
The marker is written only after the capture structure and inventories validate.
An existing destination is never reused or overwritten.

The manifest records the exact input identities, collector source identity,
original observation/completion times and effective limits. Hard ceilings are
2 MiB for metadata, each bundle and the manifest; 25 MiB for an archive;
100 MiB aggregate retained/omitted-discovery body bytes; 200 network requests;
10 pages per listing; and a 300-second collection deadline with at most 30
seconds per transport call. Callers can only lower caps. Archive redirects and
bundle downloads count toward request budgets, with conservative capacity
reservation before a transport call. A refused credential echo still consumes
its transport byte budget. The writer reserves manifest space for a terminal
failure, so a response cannot erase the completion record for prior evidence.

Selection waits for all artifact-list pages. IDs must be unique, page scope must
stay fixed, `total_count` must remain stable and match the accumulated entries,
and there must be exactly one matching name. There is no fallback to another
attempt or an older artifact. The selected listing item and separately fetched
detail must agree on artifact ID, name, run/repository metadata, digest, byte size
and expiration. The downloaded ZIP's independently computed SHA-256 and byte
size must agree before its bytes can be passed to the archive parser. ZIP member
and JUnit interpretation remain separate qualified boundaries.

Attestation acquisition uses that computed archive digest and the fixed custom
pytest predicate type. Original bundles from every page are retained. A discovery
error, mismatched repository, cursor cycle or duplicate bundle cannot become
complete provenance. Multiple distinct bundles remain explicit candidates; the
inspection orchestrator must enforce unambiguous approved cryptographic identity.
An empty, complete attestation listing retains `attestation_missing`.

`verify_artifact_capture(directory)` reads the exact tree without following
links/reparse points or accepting hardlinks, checks sizes and SHA-256 inventories,
and refuses extra files/directories. `parse_artifact_capture(manifest_bytes,
raw_files)` applies the same bounded structural checks to saved bytes.
`map_artifact_capture(capture)` then replays the same deterministic selection
plan without any network operation. It does not trust a saved normalized
selection: unexpected request scope/order, missing/extra records, changed bytes
or unsupported capture/mapper versions are refused or remain incomplete.

The mapping returns `status`, `reason_codes`, `artifact_id`, original detail
metadata/path/bytes, accepted archive bytes, bundle candidate bytes and
`attestations_complete`. `selected` denotes the consistent artifact acquisition,
not signature verification or `workflow_bound`. Partial provenance errors retain
already obtained archive bytes and the full raw capture while preventing a
successful selection result. Offline mapping preserves the original timestamps;
it cannot renew freshness. The root inspection still needs its final PR/run
recheck after source, scanners and artifacts finish.

Local manifests and hashes detect inconsistency. They are not signatures against
an actor who can rewrite the data and manifest together. The operator must keep
the capture directory privately owned and free of concurrent writers.

Twenty-one authored acquisition/storage controls passed on Windows/Python 3.12 and
Linux/Python 3.11. They cover complete pagination, duplicate/changed/missing
metadata, fixed identity, digest/size disagreement, byte and request limits,
deadline failures, malformed raw retention, discovery ambiguity, deterministic
offline mapping, unexpected request scope, saved tampering, hardlinks and
create-once behavior. Their unsigned authored bundle bytes do not count as
hosted producer, cryptographic or E01–E18 product acceptance.

## Recorded development probe

The implementation downloaded the public `taipei49314/checkwash` artifact
13363711890 through the real metadata and ZIP endpoints. The 721-byte archive's
SHA-256 matched the platform digest; the 302 was followed without forwarding API
authorization or retaining its signed location. Original bytes and the module
hash are retained under ignored `work/artifact-transport-smoke-01`.

This probe used an uncommitted development tree. It does not qualify an immutable
release, the standard producer, a job relationship, or any product acceptance
family. The older two-archive format probes in SPEC-004 independently cover ZIP
reader compatibility; neither is a full delivery inspection.

A separate development probe downloaded the public `malancas/attest-demo`
attestation for subject digest
`49a3aa6075e0f49f82843e74b5baa614ad2a588e6675612bf108a0a008c5ac25`.
Windows/Python 3.12 and Linux/Python 3.11 each observed the same 9,975 encoded
bytes, SHA-256
`aadfd8e2e9225dac31259d8e9e4f9e87989f6f24544e7ddeb8d592bc4306285a`.
Bounded decoding produced 10,799 bytes, SHA-256
`a44e4f7fd069d7e7643b6a45300c519889511d29b306bc95a692a1199c85977b`.
These bytes differ from the older formatted fixture, while their parsed values
match; this was measured, not assumed.

Each platform then invoked its hash-pinned official gh 2.98.0 with the saved
trusted root and downloaded/decoded bundle. Native exit 0 verified the exact
certificate and artifact subject. An altered-artifact control returned native
exit 1 and unconfirmed cryptography. Raw transport, both encoding layers,
verification inputs/streams and receipts are under ignored
`work/attestation-integrated-windows-01` and `work/attestation-integrated-linux-01`.
The signed predicate is the upstream generic SLSA fixture, not delivery-review's
custom pytest producer. Job binding, producer qualification, tested-source
binding and product acceptance remain unconfirmed/NOT_RUN.
