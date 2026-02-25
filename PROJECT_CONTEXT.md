# GraphAPI - Project Context

## Purpose
GraphAPI is the FastAPI service layer that validates minimal graph input, applies GraphLoom enrichment/layout, and returns rendered SVG output via GraphRender.

## Primary Goals
- Expose a stable HTTP API for validation and rendering.
- Keep request/response behavior predictable with clear error mapping.
- Enforce runtime safety limits (request size, timeout, CORS).
- Keep schema and theme endpoints aligned with GraphLoom and GraphTheme.

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
- `GET /themes`
- `GET /themes/{id}`
- `GET /themes/{id}/css`
- `GET /themes/{id}/metrics`

Behavior expectations:
- Always run ELKJS layout before SVG rendering.
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

## Dependencies and Integration
- GraphLoom: input validation, enrichment, and layout integration.
- GraphRender: SVG generation from laid-out graph data.
- GraphTheme: theme catalog and CSS/metrics exposure.

## Testing Expectations
- `python -m pytest -q`
- `python -m py_compile main.py src/graphapi/__init__.py src/graphapi/__main__.py src/graphapi/app.py`

## Open Decisions / TODO
- [ ] Add explicit request/response contract tests for all non-200 error paths.
- [ ] Add load/latency baseline checks for render endpoint under burst traffic.
- [ ] Evaluate endpoint-level telemetry for render timing and failure categories.

## How To Maintain This File
- Update after endpoint, environment variable, dependency, or runtime behavior changes.
- Keep contracts and paths implementation-accurate.
