# Fixed inspection tool bootstrap

`delivery-review tools install --out NEW` provisions the public bytes recorded
in `formats/toolchain-lock.json`. That resource supplements the frozen candidate
lock with acquisition metadata; it does not alter engine qualification or
the verifier's accepted binary/root pins. A source checkout may provision tools
while dirty; a live inspection still requires a verified clean collector identity.

## Sources and pins

The three URLs, filenames, exact lengths and SHA-256 values are identical to
`locks/engine-candidates.json`. The official version-specific PyPI JSON metadata
was checked again on 2026-11-06 and matched all four fields for every wheel:

| Wheel | SHA-256 | Metadata source |
| --- | --- | --- |
| checkwash 0.10.0 | `3522d641d589ff9b75ad5e2f510d0884ca2520c32265d8397f0fcef226bc1171` | [PyPI version JSON](https://pypi.org/pypi/checkwash/0.10.0/json) |
| pinwash 0.1.1 | `d002160a73f5affbd4cf3446f676aa2809ef006a12feff77cab5edb4a8c62e03` | [PyPI version JSON](https://pypi.org/pypi/pinwash/0.1.1/json) |
| ledgerwash 0.5.0 | `5a38efe79c38f8d4e2ea25dae231d174e597ffbc5ee2e3df4d312fdb7bc128e9` | [PyPI version JSON](https://pypi.org/pypi/ledgerwash/0.5.0/json) |

The installer uses the fixed `files.pythonhosted.org` wheel URLs directly. It
does not ask a package resolver for a latest version, fetch metadata at runtime,
import a wheel, or install any dependency.

GitHub CLI 2.98.0 archives come from the official
[cli/cli release](https://github.com/cli/cli/releases/tag/v2.98.0).
Their hashes match the previously retained
[official checksum file](https://github.com/cli/cli/releases/download/v2.98.0/gh_2.98.0_checksums.txt),
whose SHA-256 is
`275b90ae8a642fb8bdf4f21d7673e34643a445f7993f1821ac917ff8a2cc4db9`.
The extracted executables match the existing verifier adapter's accepted pins:

| Platform | Original archive bytes / SHA-256 | Selected member / executable SHA-256 |
| --- | --- | --- |
| Linux amd64 | 14,863,663 / `3b8ac6b30336802fc1a858d7c084e11cdf24ac1a761ca90b68022d7d729208de` | `gh_2.98.0_linux_amd64/bin/gh` / `62885b97de6a0cd85e616cdd94bcda908bf5cf1018094385892b05cea3537163` |
| Windows amd64 | 15,035,669 / `c28c7b3b584967a05b74d9eaf7481bff24ddc34930bf2d6e442c148236561eb1` | `bin/gh.exe` / `a1701e3c806a805981554ace7df04812772c6449bdcfd1a77675013d97786401` |

These record official HTTPS/checksum provenance. Independent publisher-signature
qualification remains pending; the install receipt does not promote that claim.
The original archive is retained with its upstream license material. The wheels
and GitHub CLI remain separate third-party distributions with their own licenses.

## Bundled public trust material

`formats/github-trusted-root.json` stores a base64 encoding of the original
34,634-byte, two-line `gh attestation trusted-root` export recorded in
[SPEC-004's actual verifier experiment](specs/SPEC-004-artifact-provenance.md#6-actual-read-only-verifier-experiment-2026-11-06).
Decoding it reproduces the exact original bytes with SHA-256
`65ca537f6ed8a47fd0e560c421baa1f6c1efb8b25fc200d8c5c02c0e92eb2b9c`.
It contains public certificate and transparency-log trust material; no private
key or credential is included. The package build inventories the JSON resource.
The [official trusted-root command](https://cli.github.com/manual/gh_attestation_trusted-root)
documents exporting roots for later use.

The installer writes those decoded bytes as `trusted-root.jsonl` and verifies
the existing adapter pin before doing so. It never fetches a replacement root.
A root update needs a reviewed bootstrap change with a new approved digest and
actual verifier evidence. This package's local hashes establish consistency
with its reviewed pins, not authenticity of the package publisher or the host.

## Acquisition boundary

Each download runs in a first-party standard-library worker launched using the
operator's current Python with `-I -B`, a private temporary directory and a
small runtime environment. No GitHub token, proxy, HOME, PATH, Python injection
variable or caller-supplied URL reaches that worker. Only the four fixed public
download inputs are used. The parent enforces the whole-request deadline,
including DNS/connect/slow streams, and kills a timed-out worker. The worker
does not spawn other processes. Exceptions and signed redirect URLs are not
printed or saved. The installer has no configurable mirrors or cache override.

TLS validation remains Python's default. Proxy discovery is disabled. Wheel
redirects are refused. A GitHub release may redirect once to an HTTPS URL on
the exact `release-assets.githubusercontent.com` host with the expected asset
path, without userinfo, explicit port or fragment. Any further redirect fails.
Response bodies are bounded to their pinned sizes, and both worker and parent
check size and SHA-256. Partial files cannot become a completed configuration.

Archives are hash-verified before parsing. Only one exact regular executable
member is read into memory; duplicate selected names, links and wrong binary
bytes fail. No archive path is written to disk. The expanded Linux tar stream
has a 128 MiB cap; both archive formats have a 4,096-member limit. The installer
writes only its fixed filenames in a new operator-owned output directory. This
is not a sandbox against another local process modifying that directory.

The download receipt is local acquisition metadata. `toolchain.json` is the
last file written and contains the paths consumed by the full inspection CLI.
Neither file changes the approved policy, saved evidence, producer registry,
or any readiness axis.

## Validation scope

Sixteen authored offline tests cover origin/redirect restrictions, HTTP bodies,
size/hash changes, credential isolation, process timeout cleanup, archive member
selection, partial installation, no overwrite, supported runtimes and CLI errors.
They passed on Windows/Python 3.12 and Ubuntu 24.04 WSL2/Python 3.11 on 2026-11-06.
Actual public preparation also completed on Windows/Python 3.12.10 and Ubuntu
24.04 WSL2/Python 3.11.16 that day. Both runs fetched all four pinned files,
loaded the resulting configuration through the inspection CLI's loader, and
reported `gh version 2.98.0` from the selected native executable. The raw public
PyPI metadata and local download receipts are retained under ignored `work/`;
`work/toolchain-bootstrap-observed.json` records the tested working-file digests
and the separate acquisition receipt digests. These are development bootstrap
observations, not E01–E18 product acceptance or independent maintainer
reproduction. They do not qualify a later commit or published package.
