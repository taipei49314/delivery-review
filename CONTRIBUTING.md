# Contributing

Changes should make evidence claims easier to inspect and reproduce. Start with
`AGENTS.md`, `docs/CONTRACT.md`, `docs/ACCEPTANCE.md`, `STATE.md` and the relevant
specification. The development roadmap is `docs/RELEASE-1.0.md`.

Use a branch, explain the concrete behavior change, and include appropriate
negative controls. Run the locked contract validator and unittest commands in
the README. Preserve raw bytes, native outcomes and unknown/missing states.
Never execute a reviewed PR's tests or installation scripts in verifier code.

The M0 baseline is frozen. Do not edit expected outcomes or update freeze hashes
to hide a regression. A necessary semantic change needs a documented rationale,
affected scenarios, compatibility plan and versioned amendment. Historical
captures must continue to replay under their declared mapper version.

Keep credentials, private transcripts, downloaded wheels, customer evidence and
temporary probes under ignored `work/` paths. Only add redistributable fixtures
with recorded origin/rights and clear synthetic-versus-observed labeling. A
synthetic control or an agent's run is not independent maintainer adoption.

PR descriptions should identify what changed, why, relevant tests and remaining
qualification limits. Follow the existing EC route for repository CI; public
runtime use must not depend on EC or access to a private maintainer repository.
Contributions are under the project's Apache-2.0 license.
