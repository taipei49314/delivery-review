# SPEC-005: Exact local Git object snapshots

Version: 1. Status: implemented local materialization and GitHub HTTPS acquisition
boundaries, including optional credentials, with authored controls and real public
and private repository acquisition probes. Qualification of an immutable release,
repository-membership authentication and full product qualification remain
separate gates.
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

## 6. GitHub HTTPS acquisition

```python
acquire_source(
    destination,
    *,
    repository: {"id": int, "full_name": str},
    head_sha: str,
    base_tip_sha: str,
    git_executable: str | Path,
    limits: SourceFetchLimits = SourceFetchLimits(),
    token=None,
) -> GitHubSourceAcquisition
```

The caller must independently validate the repository ID/full name and the exact
head/base API observations. The acquisition receipt deliberately labels these
inputs `api_identity: supplied_not_reverified`. A canonical GitHub URL does not
authenticate a numeric repository ID. The report composer must compare the input
subject with a verified platform capture and retain the corresponding API raw
references. The underlying snapshot keeps membership `unconfirmed`.

The destination is exclusive and contains `quarantine/`, an empty `home/`, bounded
process streams under `raw/`, the final `snapshot/`, and `acquisition.json` written
only on success. Existing destinations are never overwritten. Windows requires a
local fixed or RAM drive; Linux accepts native local filesystems and rejects
network/compatibility mounts such as NFS, CIFS and WSL drive passthrough. Private
directory ownership and the no-concurrent-writer condition still apply.

Git receives one fixed HTTPS URL, `https://github.com/{full_name}.git`, with two
full-SHA refspecs into private head/base refs. It does not resolve a moving branch,
fetch tags, recurse into submodules, write a checkout, run maintenance, execute PR
files or invoke installation/build scripts. The quarantine is a newly generated
bare repository, not a copied Git directory.

The inherited environment is discarded except essential operating-system values.
New empty HOME/USERPROFILE/XDG directories also prevent `.netrc` or user-specific
credential lookup. Configuration disables helpers, prompts, proxies, cookies,
redirects, alternate protocols, hooks, automatic maintenance and bundle URIs.
HTTPS certificate verification remains enabled. Unknown redirects fail rather
than expanding the host scope; this uses Git's
[documented redirect control](https://git-scm.com/docs/git-config#Documentation/git-config.txt-httpfollowRedirects).
`token=None` selects anonymous public access. The compatibility entry point
`acquire_public_source` still refuses every non-null token before disk/process IO.
The general `acquire_source` entry point accepts an explicit ASCII GitHub token;
it never reads a token implicitly from the inherited environment. A caller should
provide a narrowly scoped, short-lived credential with repository contents read
access. GitHub documents installation-token HTTP Git access with the
[`x-access-token` username](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation).

Only authenticated fetch launches an isolated first-party Python worker by
absolute filename with `-I -B`. A bounded 16-KiB private stdin message carries
the token and fixed repository/commit inputs. The worker revalidates the inputs
and generated quarantine configuration and closes the input channel. POSIX
replaces the worker with the fixed Git invocation using `os.execve`. Windows
starts the fixed argument array through `subprocess` and waits for the actual
Git exit; this preserves Windows quoting for installation paths containing
spaces. That child inherits the parent's Job Object and captured output handles.
Neither path creates another process group, breakaway process or capture
implementation. The existing parent owns the same tree, output limits,
quarantine monitor and deadline.

The worker supplies a single URL-scoped runtime setting:
`http.https://github.com/{full_name}.git.extraHeader`, whose value is an HTTP Basic
authorization header for `x-access-token:<token>`. It uses Git's documented
[`GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_0` and `GIT_CONFIG_VALUE_0`](https://git-scm.com/docs/git-config#Documentation/git-config.txt-GITCONFIGCOUNT)
environment interface, not argv or a configuration file. The fresh environment
contains no inherited tracing, proxy, askpass, loader or credential settings;
redirects remain forbidden and HTTPS certificate/hostname verification remains
enabled. The token exists in memory and in the fetch process environment, which
requires the same private operator ownership as the rest of this boundary. This
does not claim to hide credentials from the process owner or an administrator.

Version lookup, object enumeration/export, and snapshot materialization all use
the original token-free environment. The parent refuses raw-token or Basic-value
echoes in captured stdout/stderr before persisting either original stream or its
digest, including prefixes truncated by timeout/output refusal. Both streams are
replaced with an explicitly marked `sanitized_sensitive_output` diagnostic;
native process exit/termination remain recorded. Failure never triggers an
anonymous retry or emits a successful acquisition receipt. No token or header is
included in process argv, generated Git configuration, inventories or receipts.

Fetch, object enumeration and raw object export share a 120-second deadline and
the tested process-tree boundary. Windows children join a kill-on-close Job
Object before their first instruction; Linux children use a separate process
group. Timeout, bounded-output refusal or a resource monitor exception stops and
reaps the tree. The monitor runs during execution and before accepting completion.
It rejects links/reparse points, more than 1,024 quarantine entries, over 128 MiB
of logical quarantine file content, or PACK headers/loose objects exceeding
20,000 objects. Those ceilings can only be lowered.

The disk monitor is sampled, not an operating-system quota: a process can briefly
cross a threshold between polls, after which the acquisition is killed and
refused. It does not claim a hard instantaneous disk or memory sandbox. Successful
receipts are rechecked against all byte/count ceilings. This distinction must
remain visible if a deployment requires kernel-enforced storage quotas.

After fetch, `cat-file --batch-all-objects --batch-check` observes every object's
type and size. Count, 8-MiB per canonical object and 128-MiB aggregate canonical
bytes are checked before requesting blob content. `cat-file --batch` then reads
unfiltered payload bytes through bounded pipes. The provider reconstructs only
Git's canonical type/size header and verifies every exact Git SHA-1 identity;
original commit headers, messages, signatures and encodings remain unchanged.
Lossy REST reconstruction and a source ZIP cannot substitute for this graph.

Only the complete reachable head/base parent/tree/blob closure is passed to the
materializer. Additional objects received by Git remain in the diagnostic
quarantine and are counted as excluded; they cannot affect selected source or
merge base. Missing history, shallow files, invalid identities, unsafe reachable
paths and ambiguous merge bases fail closed. There is no fallback to an older
head, shallower comparison or different merge base.

The receipt retains the requested identities/URL, timestamps, Git version,
bounded process outcomes and stream digests, quarantine inventory/digests,
captured/reachable/excluded counts and the final snapshot inventory SHA-256. The
large raw object export stream is represented by its digest and the individually
verified objects, without retaining a redundant second plaintext copy. A failed
attempt may retain `acquisition-error.json` and partial diagnostic bytes, but it
does not return a successful source snapshot or create a success receipt.

Receipt mode is `anonymous_public` or `authenticated_https`; neither mode upgrades
`api_identity` or snapshot repository membership. Authenticated implementation
controls do not substitute for actual private-repository qualification.

## 7. Qualification status

Authored controls use real local Git objects and Git 2.55.0.windows.3. They exercise
identity/closure, native bytes, unsafe paths/modes, bounds, polluted environment,
no PR execution, unrelated/ambiguous history, deadlines, deterministic inventory,
post-construction tampering, added hooks/refs, hardlinks and compressed-object bombs.
They establish the tested local boundaries only. Authored source-acquisition
controls replace only the network fetch with synthetic canonical Git objects;
Git enumeration/export and materialization still execute locally. They test
fixed scope, environment isolation, anonymous-wrapper refusal, monitor limits,
unavailable history, unsafe modes, preserved errors and extra-object exclusion.
Authored credential controls additionally cover private stdin separation,
URL-scoped runtime configuration, immutable request scope, polluted environments,
raw/Basic/truncated echo refusal, missing authorization, deadlines/output refusal,
no anonymous fallback and the real isolated worker's sanitized invalid-input path.
They use fake tokens and do not contact a private repository.

An uncommitted development-tree public smoke at `2026-11-06T05:17:45Z` through
`05:17:46Z` acquired `octocat/Hello-World` (API repository ID `1296269`), head
`7fd1a60b01f91b314f59955a4e4d4e80d8edf11d` and base
`553c2077f0edc3d5dc5d17262f6aa498e69d6f8e`. All four Git processes exited zero;
seven objects and 2,746 quarantine bytes were retained. Materialization and
independent snapshot verification succeeded with the same base as merge base.
The snapshot inventory SHA-256 was
`dd4244f88990345f82f68d90ceb08229b37a8f0a4db07e6a8c6a1e927ef9b058`.
Original API responses, streams, quarantine and snapshot are under ignored
`work/source-acquisition-hello-world-01`. This is feasibility evidence, not
qualification of an immutable released tool, a PR workflow, a private-repository
credential flow, producer binding or E01–E18 integration.

A separate anonymous Linux smoke with Git 2.43.0 at `2026-11-06T05:19:40Z` through
`05:19:41Z` observed the same seven objects, 2,746 quarantine bytes, merge base and
snapshot inventory digest. Its receipt is under ignored
`work/github-source-linux-smoke/acquisition.json`. This independently exercises
the native Linux filesystem/process path and carries the same feasibility-only
qualification limits.

A private-repository development probe at `2026-11-06T05:42:00Z` through
`05:42:09Z` acquired `taipei49314/delivery-review` commit
`2f8ed29a72890241f59a8ec22371e8f515c688e4` as both head and base. The original
authenticated API response confirmed `private: true`. Windows/Python 3.12.10
with Git 2.55.0.windows.3 and Linux/Python 3.11.16 with Git 2.43.0 each retained
125 reachable objects and 133,419 quarantine bytes; independent snapshot
verification succeeded. Both snapshot inventories have SHA-256
`378893dcabd7b7c377df85b414d29cb38ce27952540e1a07a1afabdb824ee528`.
The anonymous negative control failed with Git exit 128 and no success receipt.
Credentials were supplied only in memory/private stdin and the scoped fetch
environment; none were retained in the source evidence.

The first Windows attempt exposed the CRT `os.execve` quoting failure for
`Program Files`; its failed receipt remains under ignored
`work/source-acquisition-private-windows-01`. The corrected implementation uses
the inherited Job Object with the subprocess argument array and native exit
described above. Successful receipts are under ignored
`work/source-acquisition-private-windows-02` and
`work/source-acquisition-private-linux-01`; the latter references the original
native Linux snapshot directory. The anonymous refusal is under
`work/source-acquisition-private-anonymous-negative-01`. Nineteen focused
source-acquisition controls passed on both platforms, including separate Windows
argv/exit and POSIX process-replacement controls. This proves the observed
credentialed acquisition path, not an approved immutable release, a PR
relationship, producer source binding or E01–E18 product acceptance.
