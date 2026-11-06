# Original public base-tip discrepancy

This is a retained failure investigation, not a passing E04 receipt.
All responses concern the public, first-party reference repository (ID
`1447925554`) and [PR35](https://github.com/taipei49314/delivery-review-reference/pull/35).
No response bytes were redacted or rewritten.

After a scoped fast-forward of its target branch, observations at
2026-11-06 09:22:30–31 UTC disagreed:

| Original response | Observation |
| --- | --- |
| `0.stdout`, branch reference | `3440cbf47713116978e0ccf9e405bf0e0209897f` |
| `1.stdout`, PR `base.sha` | `4eb7a97ac7f6ff7c0455232f1744997da36869ac` |
| `2.stdout`, PR merge reference | `8f4d92520756d39c9c4e840d14669ae8b13ffc12` |

`result.json` preserves the exact GET commands, timestamps, native exits and
response digests. Empty stderr files are retained. `inventory.json` independently
indexes the seven original files. Hashes establish byte consistency, not a
signature or independent endorsement of the publisher.

The actual base-race controller had already stopped after bounded polling failed
to observe the pushed tip in the PR field. It did not complete a final inspection
or claim PASS. Current mapper 2 relies on that PR field, so it cannot establish
current target-branch freshness in this scenario. A versioned independent branch
reference protocol remains a release blocker; see the [round handoff](../../docs/qualification/2026-11-06-round-closeout.md).
