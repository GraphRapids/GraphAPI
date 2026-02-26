# GraphAPI - Project Context

## Purpose
GraphAPI is the FastAPI service layer that validates minimal graph input, applies GraphLoom enrichment/layout, returns rendered SVG output via GraphRender, and serves as the canonical runtime graph-type + layout-set + link-set + iconset + render-theme service.

## Primary Goals
- Expose a stable HTTP API for validation and rendering.
- Expose contract-first graph type + layout set + link set + iconset + render theme APIs with explicit schema versioning.
- Expose deterministic graph type runtime resolution (layout + icons + link semantics).
- Keep request/response behavior predictable with clear error mapping.
- Enforce runtime safety limits (request size, timeout, CORS).
- Keep schema and runtime selector endpoints aligned with graph type and render theme bundles.

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
- `GET /v1/graph-types`
- `GET /v1/graph-types/{id}`
- `GET /v1/graph-types/{id}/bundle`
- `POST /v1/graph-types`
- `PUT /v1/graph-types/{id}`
- `POST /v1/graph-types/{id}/publish`
- `GET /v1/graph-types/{id}/runtime`
- `GET /v1/layout-sets`
- `GET /v1/layout-sets/{id}`
- `GET /v1/layout-sets/{id}/bundle`
- `POST /v1/layout-sets`
- `PUT /v1/layout-sets/{id}`
- `PUT /v1/layout-sets/{id}/entries/{key}`
- `DELETE /v1/layout-sets/{id}/entries/{key}`
- `POST /v1/layout-sets/{id}/publish`
- `GET /v1/link-sets`
- `GET /v1/link-sets/{id}`
- `GET /v1/link-sets/{id}/bundle`
- `POST /v1/link-sets`
- `PUT /v1/link-sets/{id}`
- `PUT /v1/link-sets/{id}/entries/{key}`
- `DELETE /v1/link-sets/{id}/entries/{key}`
- `POST /v1/link-sets/{id}/publish`
- `GET /v1/themes`
- `GET /v1/themes/{id}`
- `GET /v1/themes/{id}/bundle`
- `POST /v1/themes`
- `PUT /v1/themes/{id}`
- `POST /v1/themes/{id}/publish`
- `GET /v1/autocomplete/catalog?graph_type_id=...`
- `GET /v1/icon-sets`
- `GET /v1/icon-sets/{id}`
- `GET /v1/icon-sets/{id}/bundle`
- `POST /v1/icon-sets`
- `PUT /v1/icon-sets/{id}`
- `PUT /v1/icon-sets/{id}/entries/{key}`
- `DELETE /v1/icon-sets/{id}/entries/{key}`
- `POST /v1/icon-sets/{id}/publish`
- `POST /v1/icon-sets/resolve`

Behavior expectations:
- Always run ELKJS layout before SVG rendering.
- Graph type/layout set/link set/theme bundles are schema-versioned (`v1`) and checksumed.
- Graph type bundles include deterministic iconset + runtime resolution checksums.
- Published graph type/theme versions are immutable.
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
- `GRAPHAPI_GRAPH_TYPE_STORE_PATH`
- `GRAPHAPI_RUNTIME_DB_PATH`
- `GRAPHAPI_LAYOUT_SET_STORE_PATH`
- `GRAPHAPI_LINK_SET_STORE_PATH`
- `GRAPHAPI_THEME_STORE_PATH`
- `GRAPHAPI_ICONSET_STORE_PATH`
- `GRAPHAPI_DEFAULT_RENDER_CSS_PATH`

## Dependencies and Integration
- GraphLoom: input validation, enrichment, and layout integration.
- GraphRender: SVG generation from laid-out graph data.
- Graph type store: canonical source for runtime ELK + node/link type catalog settings.
- Theme store: canonical source for runtime render CSS settings.

## Testing Expectations
- `python -m pytest -q`
- `python -m py_compile main.py src/graphapi/__init__.py src/graphapi/__main__.py src/graphapi/app.py`

## Open Decisions / TODO
- [ ] Add endpoint-level authn/authz policy for graph type/layout set/link set/iconset/theme mutations.
- [ ] Add load/latency baseline checks for render endpoint under burst traffic.
- [ ] Evaluate endpoint-level telemetry for render and graph-type resolution timing/failure categories.

## How To Maintain This File
- Update after endpoint, environment variable, dependency, or runtime behavior changes.
- Keep contracts and paths implementation-accurate.
