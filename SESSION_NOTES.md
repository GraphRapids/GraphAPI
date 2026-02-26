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
- Summary: Split runtime selectors into layout profiles and render themes.
- Changes:
  - Removed `renderCss` from profile contract/store payloads.
  - Added theme contract/store/defaults and `/v1/themes*` CRUD + publish endpoints.
  - Updated `POST /render/svg` to accept `theme_id`, `theme_stage`, and `theme_version`.
  - Added theme selector headers and runtime checksum header to render responses.
  - Updated contract and smoke tests for profile+theme runtime behavior.
- Files touched:
  - `src/graphapi/app.py`
  - `src/graphapi/profile_contract.py`
  - `src/graphapi/profile_defaults.py`
  - `src/graphapi/profile_store.py`
  - `src/graphapi/theme_defaults.py`
  - `src/graphapi/theme_store.py`
  - `tests/test_profiles.py`
  - `tests/test_smoke.py`
  - `README.md`
  - `PROJECT_CONTEXT.md`
  - `SESSION_NOTES.md`
- Tests run:
  - `./.venv/bin/python -m pytest -q` (12 passed)
- Known issues: none.
- Next steps:
  - Keep GraphEditor and SDK clients aligned with new `theme_id` selector semantics.

### 2026-02-26
- Summary: Removed external theme compatibility surfaces from API runtime and contracts.
- Changes:
  - Removed external theme package integration from API runtime path.
  - Removed compatibility theme endpoints.
  - Simplified `POST /render/svg` to accept profile selectors only (`profile_id`, `profile_stage`, `profile_version`).
  - Removed optional theme package dependency from `pyproject.toml`.
  - Updated tests for profile-only render behavior.
- Files touched:
  - `src/graphapi/app.py`
  - `tests/test_profiles.py`
  - `tests/test_smoke.py`
  - `pyproject.toml`
  - `README.md`
  - `PROJECT_CONTEXT.md`
  - `SESSION_NOTES.md`
- Tests run:
  - Pending.
- Known issues: none.
- Next steps:
  - Keep OpenAPI and consumer docs aligned with profile-only runtime selectors.

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
