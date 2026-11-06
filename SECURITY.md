# Security and trust boundaries

The project is in development; no stable supported release has been qualified.
See `docs/RELEASE-1.0.md` for the release gates and `STATE.md` for actual status.

Relevant reports include false readiness, lost native failures, source/attempt
confusion, artifact substitution, parser/resource-limit bypasses, unexpected
execution, credential disclosure and unsafe rendering of provider content.

Use GitHub private vulnerability reporting when available on this repository.
If that channel is unavailable, open a minimal issue requesting a private contact
without exploit details, credentials, private evidence or customer data. There
is no stated response-time guarantee during development.

The verifier treats provider bodies, archives, XML, reports and reviewed source
as untrusted data. Its execution boundary permits only approved static tools on
bounded snapshots. The producer runs project tests separately in its approved
environment. A producer receipt alone cannot authenticate its origin, and a
hash manifest alone is not a publisher signature.

Readiness is limited to explicit policy requirements and supported evidence. It
does not prove program correctness or guarantee that tests are meaningful. A
local administrator, compromised interpreter/OS or rewritten trust policy is
outside the consistency guarantees provided by a saved bundle.
