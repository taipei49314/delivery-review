# Engine candidate byte lock

`locks/engine-candidates.json` identifies the three M0 wheel candidates by their
approved SHA-256, exact filename, byte size and official PyPI download URL.
These are candidate artifacts, not accepted adapter integrations.

| Candidate | Public JSON contract reviewed | Integration |
| --- | --- | --- |
| Checkwash 0.10.0 | findings v2 | NOT_RUN |
| Pinwash 0.1.1 | findings v1 | NOT_RUN |
| Ledgerwash 0.5.0 | findings v2 | NOT_RUN |

Contract labels come from documentation/source review. Byte verification does
not execute a CLI, validate runtime JSON, install a package, run a scan, or prove
that the three candidates are mutually compatible. Version selection remains
subject to the later adapter qualification gate.

## Reproduce the byte receipt

From the repository root, explicitly allow network access and put wheels in a
sibling scratch directory:

```text
python tools/verify_engine_artifacts.py --download --wheel-dir ../engine-downloads --receipt locks/engine-candidates.json
```

Without `--download`, the utility reads existing wheel files and makes no network
requests:

```text
python tools/verify_engine_artifacts.py --wheel-dir ../engine-downloads
```

The network mode first fetches official version-specific PyPI JSON and requires
exact filename, expected digest, size and URL agreement for a non-yanked wheel.
It then downloads the bytes and independently hashes them. HTTPS is required;
metadata and wheel requests are restricted to their respective official hosts,
including redirects. Downloads, archive members and expanded bytes are bounded.
Each network request has a 30-second socket timeout and a 120-second transfer
deadline (an in-progress final read can extend this by the socket timeout).
Existing different scratch bytes are never overwritten. A receipt is written
only after all candidates pass.

The verifier uses only the Python standard library. It reads wheel members in
memory without extracting them, importing their modules, or executing package
code. It rejects unsafe paths, duplicate members, symbolic links, encryption,
unsupported member types and malformed archive metadata. METADATA Name/Version
must match the candidate. Every regular file must match its RECORD digest and
size, except RECORD itself and the optional wheel signature files allowed by the
wheel format. Signature files are inventoried but their cryptographic signature
is not validated.

## Receipt meaning

`candidate_byte_verified` means that the observed archive matched the approved
digest and the declared inventory was internally consistent. It does not prove
the archive's code is safe, correct, authentic in a broader trust model, or ready
for this product's adapters. `integration_status` remains exactly `NOT_RUN`.

The receipt contains no absolute local paths. `member_count` counts regular file
members; `record_verified_files` counts RECORD rows with checked digests/sizes.
`metadata_sha256` and `record_sha256` hash the raw uncompressed bytes.
`inventory_sha256` hashes UTF-8 canonical JSON of the sorted regular-file list:
each item has `path`, `size_bytes`, and its uncompressed `sha256`; keys are sorted,
ASCII escaping is enabled, and JSON separators are `,` and `:` with no whitespace.
This allows the inventory to be reproduced without committing downloaded wheels.

Synthetic tests exercise corrupt member bytes, identity mismatch, path attacks,
missing RECORD coverage, bounds, and the offline network prohibition:

```text
python -m unittest discover -s tests -p test_engine_lock.py -v
```
