# Synthetic observation profile

`profile.json` is an authored SPEC-001 configuration example. Repository ID
9001, workflow ID 101, names and job selection are fictional. App ID 15368 is
the example GitHub Actions App selector; its presence does not authenticate any
execution. No API request, run or accepted evidence is represented by this file.

An operator supplies a reviewed local profile with independently checked IDs.
The profile is separate from the frozen readiness policy v1: it cannot produce
`ready_for_review`, lower a policy requirement or claim confirmed test checkout.
It is retained byte-for-byte and hash-bound in the planned observation capture.

See `docs/specs/SPEC-001-github-observations.md` for the protocol and
`formats/github-observation-profile-v1.schema.json` for its data shape. The
sample and future raw-provider fixtures stay outside the M0 freeze's existing
top-level examples and synthetic GitHub files.
