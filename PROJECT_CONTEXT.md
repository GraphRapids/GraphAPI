# GraphAPI - Project Context

## Purpose
GraphAPI is the FastAPI service layer that validates minimal graph input, applies GraphLoom enrichment/layout, returns rendered SVG output via GraphRender, and serves as the canonical runtime layout-profile + render-theme service.

## Primary Goals
- Expose a stable HTTP API for validation and rendering.
- Expose contract-first layout profile + render theme APIs with explicit schema versioning.
- Keep request/response behavior predictable with clear error mapping.
- Enforce runtime safety limits (request size, timeout, CORS).
- Keep schema and runtime selector endpoints aligned with layout profile and render theme bundles.

## Package Snapshot
- Python package: `graphapi`
- Entry points:
  - `python -m graphapi`
  - `main.py`
- Core source:
  - `src/graphapi/app.py`
  - `src/graphapi/__init__.py`

## API Contract
Primary endpoints:
- `GET /healthz`
- `POST /validate`
- `POST /render/svg`
- `GET /schemas/minimal-input.schema.json`
- `GET /v1/profiles`
- `GET /v1/profiles/{id}`
- `GET /v1/profiles/{id}/bundle`
- `POST /v1/profiles`
- `PUT /v1/profiles/{id}`
- `POST /v1/profiles/{id}/publish`
- `GET /v1/themes`
- `GET /v1/themes/{id}`
- `GET /v1/themes/{id}/bundle`
- `POST /v1/themes`
- `PUT /v1/themes/{id}`
- `POST /v1/themes/{id}/publish`
- `GET /v1/autocomplete/catalog`

Behavior expectations:
- Always run ELKJS layout before SVG rendering.
- Profile/theme bundles are schema-versioned (`v1`) and checksumed.
- Published profile/theme versions are immutable.
- Return clear status codes for validation, timeout, size, and runtime failures.
- Keep OpenAPI docs accurate and available.

## Runtime Configuration
Environment variables:
- `GRAPHAPI_HOST`
- `GRAPHAPI_PORT`
- `PORT` (fallback)
- `GRAPHAPI_CORS_ORIGINS`
- `GRAPHAPI_REQUEST_TIMEOUT_SECONDS`
- `GRAPHAPI_MAX_REQUEST_BYTES`
- `GRAPHAPI_PROFILE_STORE_PATH`
- `GRAPHAPI_THEME_STORE_PATH`
- `GRAPHAPI_DEFAULT_RENDER_CSS_PATH`

## Dependencies and Integration
- GraphLoom: input validation, enrichment, and layout integration.
- GraphRender: SVG generation from laid-out graph data.
- Profile store: canonical source for runtime ELK + type catalog settings.
- Theme store: canonical source for runtime render CSS settings.

## Testing Expectations
- `python -m pytest -q`
- `python -m py_compile main.py src/graphapi/__init__.py src/graphapi/__main__.py src/graphapi/app.py`

## Open Decisions / TODO
- [ ] Add endpoint-level authn/authz policy for profile mutations.
- [ ] Add load/latency baseline checks for render endpoint under burst traffic.
- [ ] Evaluate endpoint-level telemetry for render/profile timing and failure categories.

## How To Maintain This File
- Update after endpoint, environment variable, dependency, or runtime behavior changes.
- Keep contracts and paths implementation-accurate.
