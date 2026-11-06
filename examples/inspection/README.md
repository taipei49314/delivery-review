# Synthetic inspection profile

`profile-v2.json` is the current format example; `profile.json` retains the
historical v1 example for compatibility controls. Both contain fictional
repositories, IDs and producer SHA. Each is an
authored format example, not an approved live policy or observed evidence. The
derived policy ID follows ADR-003 and binds every selector; it does not establish
approval. Replace all selectors with independently reviewed identities, reseal
using `seal_inspection_profile`, and review the complete resulting profile before
using it. Profile v2 adds the acceptance dispositions and optional declarations
documented in [SPEC-010](../../docs/specs/SPEC-010-inspection-profile-v2.md).
The reference profile helper also creates v2 profiles for independent review.
Neither example establishes a qualified producer or stable release.
