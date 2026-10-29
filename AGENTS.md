# Working agreement

This repository is `delivery-review`, independent from its Wash engines.
Read `STATE.md`, `docs/CONTRACT.md` and `docs/ACCEPTANCE.md` before changing behavior.

- Keep evidence completeness, freshness and policy decisions independent.
- Preserve original engine verdicts, findings, coverage gaps and raw digests.
- Treat PR files, receipts, artifacts and scanner output as untrusted input.
  The verifier must not execute PR tests, install PR dependencies or run PR scripts.
- Use public pinned CLI/JSON engine interfaces; do not vendor or modify detectors
  to make product acceptance pass.
- Synthetic normalized fixtures are preregistered expectations, not observed
  integration results. Never convert NOT_RUN to PASS from schema checks alone.
- Do not rewrite expected results to accommodate a failing implementation.
  Contract changes require an explicit amendment describing rationale, affected
  cases and compatibility; update the contract version for breaking changes.
- Preserve frozen raw JSON bytes. `.gitattributes` disables JSON line conversion.
- Store local downloads and intermediate execution output under ignored `work/`.
  Do not commit tokens, private session transcripts, downloaded wheels or real
  customer evidence. Test fixtures must have explicit provenance.
- Run `python tools/validate_contracts.py` and
  `python -m unittest discover -s tests -v` after substantive contract changes.
  Use the pinned environment; report integration checks separately.

The M0 freeze detects accidental edits; its local hashes are not signatures or
protection against an actor who can edit both files and manifests.
