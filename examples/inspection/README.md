# Synthetic inspection profile

`profile.json` contains fictional repositories, IDs and producer SHA. It is an
authored format example, not an approved live policy or observed evidence. The
derived policy ID follows ADR-003 and binds every selector; it does not establish
approval. Replace all selectors with independently reviewed identities, reseal
using `seal_inspection_profile`, and review the complete resulting profile before
using it. The full inspection orchestration is still under implementation.
