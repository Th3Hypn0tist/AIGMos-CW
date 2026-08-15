# AIGMos CW 2.0 migration

This branch migrates the locked AIGMos CW 1.4 baseline to the locked Canonical Contract Format 2.0 and CanonicalWireframe Dependency Rules 2.0.

Authoritative standard entry points:

- `https://aigm.fi/format/Canonical_Contract_Format.json`
- `https://aigm.fi/format/Canonical_Wireframe_Dependency_Rules.json`

Migration rules:

- `Entity + Property` is the CCF 2.0 semantic core.
- All migrated contracts remain `unlocked` until semantic closure and validation are complete.
- Directed structural connections become Link Properties.
- Event is a Property; directed Event semantics are split into Link Properties.
- Link endpoints may resolve to Entity or Property identities.
- No architecture, endpoint, causal or domain semantics are guessed.
- Legacy source material is retained only as non-authoritative migration evidence.
- AIGMos-specific legacy Property categories use an explicit unlocked AIGMos DependencyRules layer until reviewed.

`main` remains the locked 1.4 baseline until the 2.0 migration has been validated and explicitly accepted.
