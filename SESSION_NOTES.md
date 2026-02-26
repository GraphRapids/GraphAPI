# GraphAPI - Session Notes

Use this file as a running log between work sessions.

## Entry Template

### YYYY-MM-DD
- Summary:
- Changes:
- Files touched:
- Tests run:
- Known issues:
- Next steps:

## Current

### 2026-02-26
- Summary: Implemented Option B profile runtime architecture with v1 profile CRUD/publish API and profile-driven render/catalog behavior.
- Changes:
  - Added versioned profile contract models (`schemaVersion=v1`) including `checksum` and `profileVersion`.
  - Added file-backed profile storage with draft/published version handling and immutable publish semantics.
  - Added endpoints: `/v1/profiles`, `/v1/profiles/{id}`, `/v1/profiles/{id}/bundle`, `/v1/profiles/{id}/publish`, `/v1/autocomplete/catalog`.
  - Added profile-aware render support (`POST /render/svg?profile_id=...`) and profile checksum/version headers.
  - Added contract + endpoint + end-to-end profile flow tests.
- Files touched:
  - `src/graphapi/app.py`
  - `src/graphapi/profile_contract.py`
  - `src/graphapi/profile_defaults.py`
  - `src/graphapi/profile_store.py`
  - `tests/test_profiles.py`
  - `tests/test_smoke.py`
  - `README.md`
  - `PROJECT_CONTEXT.md`
  - `SESSION_NOTES.md`
- Tests run:
  - `./.venv/bin/python -m pytest -q` (15 passed)
- Known issues: none.
- Next steps:
  - Add API authz controls around profile mutation/publish endpoints.

### 2026-02-25
- Summary: Added persistent project/session context documentation.
- Changes:
  - Introduced `PROJECT_CONTEXT.md`.
  - Introduced `SESSION_NOTES.md`.
- Files touched:
  - `PROJECT_CONTEXT.md`
  - `SESSION_NOTES.md`
- Tests run: not run (docs-only update).
- Known issues: none.
- Next steps:
  - Keep this log updated when API behavior or architecture changes.
