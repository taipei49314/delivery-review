# Architecture

Status: **M0 contract architecture**. Schemas, synthetic contract fixtures and a
local validator are the current milestone. The provider, installed-engine
adapters, producer, parser and report workflow described below are subsequent
implementation boundaries, not claims that those components already work.

The authoritative semantics are in [CONTRACT.md](CONTRACT.md). Product
integration evidence is tracked separately in [ACCEPTANCE.md](ACCEPTANCE.md).

## 1. One bounded inspection

```text
PR URL + trusted policy + engine lock
                 |
        fixed subject snapshot
                 |
      +----------+-------------+----------------+
      |                        |                |
GitHub observations     fixed diff scans    optional ledger scan
run/attempt/artifact    Checkwash/Pinwash       Ledgerwash
      |                        |                |
source binding + pytest receipt/JUnit observations
      |                        |                |
      +------ raw evidence store + digests ------+
                              |
                    pure policy evaluation
                              |
                  final live freshness check
                              |
              JSON + Markdown + standalone HTML
```

An inspection reads an existing PR and CI execution. It does not run the PR's
tests, install its dependencies or execute its scripts. The three Wash engines
remain separately versioned products, called through their public CLI/JSON
interfaces. The new product owns evidence linkage and policy evaluation.

## 2. Component responsibilities

| Boundary | Owns | Does not infer |
| --- | --- | --- |
| `snapshot` | Numeric repository identities, full head/base/merge-base SHAs and allowed merge-parent proof. | Branch labels or equal-looking content as source identity. |
| GitHub provider | Bounded authenticated reads, complete pagination, run/attempt/job/check/artifact observations and final refresh. | Trust from a check name or a self-reported receipt. |
| Evidence store | New report directories, selected original bytes, inventory and independently computed digests. | Authentic execution from hash consistency alone. |
| Binding layer | Policy-approved workflow identity, referenced workflow SHA, source mode, run/attempt/job/artifact relationships. | Hardware attestation or truth of arbitrary test reports. |
| Engine adapters | Locked executable identity, isolated subprocess, exact arguments/exits, original JSON and coverage. | Private engine API stability, agreement between engines or session-wide protection. |
| pytest adapter | Qualified JUnit dialect, counts, outcomes and producer linkage. | That upload success means tests passed or that zero tests suffice. |
| Policy evaluator | Obligation matching, evidence/freshness/policy axes, reasons and readiness. | Requirements from observed green jobs or arbitrary natural-language task intent. |
| Renderer | Traceable summaries, complete findings, limitations and escaped presentation. | A new decision or a stronger claim than the JSON report. |

The evaluator should be deterministic over verified normalized observations and
the saved policy. Provider and scanner code performs IO; the evaluator does not
fetch missing data, rerun a test, edit an exemption or change the source snapshot
to obtain a better outcome.

## 3. Reuse boundaries

- **Checkwash 0.10.0, Pinwash 0.1.1/SPEC 29, Ledgerwash 0.5.0/SPEC 5** are initial
  lock candidates. Published wheel hashes are inputs to independent acquisition
  checks, not completed installation or adapter qualification. Later upgrades
  require deliberate lock changes and compatibility checks.
- **Checkwash's receipt bridge** informs exact source/collector/artifact linkage,
  JUnit handling and live PR rechecks. Its repository, host and command-specific
  contract is not copied as a generic collector.
- **ec-reference** informs platform identity, run/attempt and artifact verification.
  Its bounded executor does not become a general runner or task-governance
  dependency of this product.
- **checkwash-corpus** supplies selected regression and narrow oracle examples.
  It is not a runtime database and its historical labels are not new truth labels.
- **GitHub CLI/API** provides the initial provider interface. The authenticated
  boundary must not print credentials or pass them to scanners/parsers.
- **actions/upload-artifact** is a future producer-template dependency pinned to a
  qualified full commit. The local collector reads artifacts through the API.
- **reviewdog and dorny/test-reporter** inform reporter/parser design. RDJSON is
  optional after the required JSON/Markdown/HTML flow; remote publishing is out
  of scope. **in-toto Statement v1** is an optional unsigned export shape and
  supplies no extra trust. None is a reason to delay the core milestone.

No detector, third-party source corpus or private transcript is vendored in M0.
Any later code reuse must preserve its applicable license and notice records.

## 4. Producer and verifier separation

The planned producer is an operator-approved fixed reusable workflow. It records
source/checkout identity, workflow/run/attempt/job, test command and exit,
JUnit digests and counts. It writes the receipt before upload; the receipt has no
enclosing artifact ID or archive digest. The collector independently reads the
GitHub artifact metadata, downloads and hashes the archive, and stores its
`artifact_id`/`artifact_sha256` in outer evidence. Extracted receipt/JUnit bytes
have separate digests. Setup, execution and upload remain separate observations.
The verifier independently reads platform metadata, confirms referenced workflow
path/SHA and applies the approved source contract before awarding workflow_bound.

The subject can influence test code and generated JUnit; the producer's approval
defines that trust boundary. A receipt is data, never an instruction to execute
its recorded command. A successful signature or byte digest, if later supported,
would authenticate the statement's association, not make all its content true.

Use a newly controlled checkout/object store for static analysis. Do not inherit
the subject's Git hooks, filters, config or Python import path. Keep scanner
installations fixed and outside the subject's writable checkout, run them in
bounded isolated subprocesses, and separate API credentials from their environment.

Artifact processing admits only bounded manifest-selected JSON/XML. It rejects
unsafe paths, symlinks, duplicate names, unsupported formats, DTD/external entities
and oversized input. HTML is self-contained and escapes untrusted content. Saved
source links are data; raw artifact content never becomes executable markup.

The initial planned limits are 25 MiB per downloaded artifact, 100 MiB expanded,
100 files and 120 seconds per engine. They are configurable planning defaults,
not measured performance guarantees; refusal must remain incomplete/error.

## 5. Persistence and command lifecycle

There is no service database in v0.1. Each inspection creates a new directory
with raw evidence, digests and reports. This gives reproducible local inputs;
it does not protect against an owner rewriting both files and the manifest.

The planned commands are `inspect`, `replay` and `refresh`. M0 does not claim
these product commands are implemented. Inspect captures a snapshot and finishes
with a live consistency check. Replay reads saved bytes/policy, preserves the
original `observed_at` and emits freshness `unknown`. Refresh obtains new
observations in a new directory and preserves the old report. A future history
index can reference these records without changing
their original observation times or source identities.

The product does not reconstruct a complete Agent session, operate a scheduler,
manage tasks, write GitHub content, auto-accept changes or train a model. Existing
Checkwash limitations, including #419, remain disclosed analysis boundaries.
M1 and M2 can proceed after the M0 contract is fixed; M3 joins their outputs into
the user flow, and M4 qualifies the declared installation and pilot experience.
