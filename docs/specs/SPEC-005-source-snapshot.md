# SPEC-005: Exact local Git object snapshots

Version: 1. Status: implemented local materialization boundary with authored
controls. Remote source acquisition, repository-membership authentication,
scanner integration and cross-platform qualification remain separate gates.
This specification does not change the frozen M0 readiness contract.

## 1. Purpose and API

Static readers need an exact PR-head working tree and the Git objects needed to
compare `merge_base..head`. The observed target-branch tip and merge base are
different identities. Neither may silently replace the other.

```python
materialize_snapshot(
    destination,
    *,
    repository: str,
    head_sha: str,
    base_tip_sha: str,
    objects: Mapping[str, bytes],
    git_executable: str | Path,
    limits: SnapshotLimits = SnapshotLimits(),
) -> SourceSnapshot

verify_snapshot(snapshot: SourceSnapshot) -> None
```

The destination must not exist. Its existing parent and ancestors must be plain
directories, with no symlink or Windows reparse point. The operator must own and
keep the parent private against concurrent changes. The Git executable is an
explicit absolute path to an operator-approved installation. This boundary does
not discover or execute a program supplied by the reviewed repository.

`repository` is a GitHub `owner/repository` locator, not proof of numeric repository
identity or membership. `head_sha` and `base_tip_sha` must each be 40 lowercase
hexadecimal characters. Abbreviations, symbolic references and shell syntax are
not accepted. The output supports only `source_mode: pr_head`. It does not attest
a producer checkout or implement candidate merge-checkout qualification.

## 2. Object input and complete closure

The object map contains canonical uncompressed Git bytes:

```text
<type> <decimal payload byte length> NUL <payload bytes>
```

Each key must match the Git SHA-1 identity calculated over those exact bytes.
Only commit, tree and blob objects are supported. Tags are not dereferenced.
Lengths, header spelling, type, identity, per-object size and total size are
checked before creating the destination. A private mapping snapshot prevents
later caller mutations from replacing already validated bytes.

The accepted set is exactly the complete reachable closure of both input commits:
every parent commit, every commit's tree, every nested tree and every referenced
blob. Missing parent history is incomplete, including a shallow boundary.
Unexpected additional objects are rejected. A reference to an object of the
wrong type is rejected. Objects from a different repository do not acquire
membership merely because their hashes and graph are self-consistent.

SHA-1 serves only as Git's object identifier. The inventory separately records
SHA-256 of every original canonical object, compressed loose object and
materialized blob. Neither
the inventory nor its local digests are signatures or publisher authentication.

## 3. Filesystem restrictions

All reachable trees are checked, including historical BASE-policy trees.
Only directory mode `40000` and regular-blob modes `100644` / `100755` are
accepted. Symlinks (`120000`) and gitlinks/submodules (`160000`) are rejected.

Names must be strict UTF-8 in NFC form. The portable subset rejects empty/dot
segments, slash and backslash, control/format characters, Windows metacharacters
and alternate-data-stream colons, trailing dots/spaces, `.git` in any case, DOS
device names and explicit short-name aliases containing `~`. Case-folding sibling
collisions, duplicate names and noncanonical Git tree ordering are rejected.
Depth, relative UTF-8 path length and expanded tree size have independent bounds.
The same subtree appearing under several paths counts for each expanded path.

Head files are written directly from their validated blob payloads using
exclusive file creation. CRLF, binary data and `.gitattributes` remain unchanged.
No checkout, reset, read-tree, archive extraction, clean/smudge filter, textconv,
dependency installation, PR hook or PR script is invoked. Files are created with
owner read/write permission, with no executable bit on POSIX; original Git modes
remain in the object graph and inventory. Windows permissions follow the private
operator-owned parent ACL.

## 4. Isolated Git verification

The implementation creates its own minimal `.git`, loose object files, detached
`HEAD`, empty refs directory and disabled-hooks directory. It generates the only
local Git configuration; it never copies a supplied repository's `.git/config`,
alternates, replacement refs, shallow file, templates or hooks.

Git receives fixed argument arrays with `shell=False`, no stdin, a shared
wall-clock deadline, explicit worktree/Git-directory paths, and only these read
operations:

1. `rev-parse --verify <head>^{commit}` must return the exact head.
2. `rev-parse --verify <base-tip>^{commit}` must return the exact base tip.
3. `merge-base --all <base-tip> <head>` must return exactly one existing commit.

The unique merge base is independently recomputed from the complete in-memory
parent graph and must agree with Git. An unrelated history, missing object,
unsupported Git invocation, timeout or
multiple best merge bases fails closed. No arbitrary best ancestor is selected.
Git can return several merge bases in criss-cross history, as its
[merge-base documentation](https://git-scm.com/docs/git-merge-base) specifies.

The process environment is an allowlist. Inherited Git configuration, object
directories, replacement/alternate sources, tracing, credential prompts, proxies,
Python paths and loader injection variables are not passed through. System and
global Git configuration are disabled; replacement objects, lazy fetching,
optional locks, credential helpers and all transport protocols (including file)
are disabled. These controls use Git's documented
[configuration and environment interfaces](https://git-scm.com/docs/git).

The resulting repository deliberately has no index. Explicit commit-range object
readers can access the preserved graph. A scanner depending on an index, a
checkout side effect or global Git configuration must be qualified separately;
it must not report an empty successful scan merely because those inputs are absent.

## 5. Resource limits and completion

Limits may be lowered, never raised above these hard ceilings. Integers are
required; booleans are not accepted as integers.

| Resource | Ceiling |
| --- | --- |
| Object count | 20,000 |
| One canonical object | 8 MiB |
| Total canonical object bytes | 128 MiB |
| Total compressed loose-object content | 128 MiB |
| Files / materialized blob bytes per tree | 10,000 / 128 MiB |
| Unique tree entries / expanded entries per tree | 50,000 / 50,000 |
| Tree depth / relative path UTF-8 bytes | 32 / 240 |
| Canonical inventory | 2 MiB, matching the strict JSON reader |
| Whole operation, including Git processes | 30 seconds |

The disk limit counts logical object-file content, not filesystem allocation
units or directory metadata. No network operation occurs in this boundary.
Fixed Git commands never print arbitrary blob/diff/message content; their output
is checked against the bounded commit count and stderr is not exposed as a public
diagnostic. The overall deadline is checked between bounded parsing/writing units
and enforced on subprocess completion.

On success the result retains `root`, `repository`, `head_sha`, `base_tip_sha`,
`merge_base_sha` and immutable `inventory_bytes`. Its `inventory` property returns
a defensive parsed document. The inventory records source mode, acquisition
`operator_supplied_objects`, repository membership `unconfirmed`, all object/file
identities and byte counts. It is written last under the protected `.git`
namespace. Same objects and identities yield the same inventory independent of
destination and mapping insertion order.

An I/O or Git failure can leave a partial newly created directory. It is not
automatically removed, reused or returned as a successful snapshot. The caller
must not scan that directory. A dataclass or disk inventory alone is not proof.

`verify_snapshot` performs bounded no-follow reads before and after a scan. It
requires the disk inventory to equal the saved immutable inventory bytes, checks
the fixed HEAD/config, rejects extra/missing files and directories (including refs,
hooks, an index or alternates), and rejects symlinks, reparse points and hardlinks.
It bounds compressed reads and decompression both per object and in aggregate,
rechecks the complete object closure, reconstructs every file identity, and derives
the unique merge base again without invoking Git. Modified raw bytes, forged
metadata and merely self-consistent rewritten merge-base claims cannot pass.

This does not turn a mutable filesystem into a sandbox against a concurrent owner
who can rewrite files between checks. The runner must retain exclusive private
ownership and successfully verify both before and after execution before recording
a source-bound scanner result. Repository membership remains independently
unconfirmed even when local re-verification succeeds.

## 6. Remote acquisition gate — not implemented here

A future GitHub acquisition layer must provide the exact original object bytes;
reconstructing a commit from lossy REST fields or extracting a source ZIP is not
equivalent. Each reconstructed object, if supported, must still match the complete
requested Git identity, including signatures, headers and original encodings.

An HTTPS Git transport must use a separately quarantined operator-owned directory,
the same configuration isolation, a canonical allowlisted GitHub URL, disabled
redirect/protocol expansion and no credential helper. A token may enter a worker
through stdin or its private environment only; it must not enter argv, repository
configuration, process diagnostics, captures or final files. No private-repository
network test is claimed by this local slice.

Remote work additionally requires an end-to-end deadline and an enforced bound on
downloaded/expanded object storage while acquisition is running. Checking the size
only after `git fetch` completes does not satisfy that gate. Worker descendants
must be stopped on deadline; partial bytes cannot be promoted into a successful
snapshot. The final head/base identities, complete history, unique merge base,
repository identity and original acquisition evidence must all be checked before
passing the object map to this materializer.

## 7. Qualification status

Authored controls use real local Git objects and Git 2.55.0.windows.3. They exercise
identity/closure, native bytes, unsafe paths/modes, bounds, polluted environment,
no PR execution, unrelated/ambiguous history, deadlines, deterministic inventory,
post-construction tampering, added hooks/refs, hardlinks and compressed-object bombs.
They establish this local boundary only. Linux execution, live GitHub acquisition,
Pinwash/Checkwash/Ledgerwash invocation, producer binding and E01–E18 integration
qualification are not established by these tests.
