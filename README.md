# GraphAPI

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![CI](https://github.com/GraphRapids/GraphAPI/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/GraphRapids/GraphAPI/actions/workflows/ci.yml)
[![Tests](https://github.com/GraphRapids/GraphAPI/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/GraphRapids/GraphAPI/actions/workflows/test.yml)
[![Gitleaks](https://github.com/GraphRapids/GraphAPI/actions/workflows/gitleaks.yml/badge.svg?branch=main)](https://github.com/GraphRapids/GraphAPI/actions/workflows/gitleaks.yml)

FastAPI service that converts GraphLoom minimal JSON input into SVG output using GraphRender.

## Features

- FastAPI HTTP service with OpenAPI docs (`/docs`)
- Canonical graph type service (`/v1/graph-types*`) with deterministic runtime resolution
- Canonical render theme service (`/v1/themes*`) with draft/publish lifecycle
- Canonical node-type icon set service (`/v1/icon-sets*`) with draft/publish lifecycle
- `POST /render/svg` endpoint for JSON-to-SVG conversion
- Optional `graph_type_id` and `theme_id` query parameters on `POST /render/svg` for runtime + render selection
- `POST /validate` endpoint for lightweight JSON validation
- GraphLoom integration for validation and default enrichment
- ELKJS layout is always executed before rendering
- GraphRender integration for SVG generation
- `GET /schemas/minimal-input.schema.json` to expose GraphLoom's official input schema
- Configurable CORS, request timeout, and request size limits

## Runtime Graph Type + Modular Runtime API (v1)

GraphAPI is the canonical runtime service for GraphRapids consumers.

### Endpoints

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
- `PUT /v1/themes/{id}/variables/{key}`
- `DELETE /v1/themes/{id}/variables/{key}`
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

### Graph Type Schema (v1)

Each bundle carries:

- `schemaVersion`
- `graphTypeId`
- `graphTypeVersion`
- `name`
- `layoutSetRef`
- `iconSetRefs[]`
- `linkSetRef`
- `iconConflictPolicy`
- `nodeTypes[]`
- `linkTypes[]`
- `typeIconMap`
- `edgeTypeOverrides`
- `elkSettings`
- `iconSetResolutionChecksum`
- `runtimeChecksum`
- `updatedAt`
- `checksum`

### Render Theme Schema (v1)

Each theme bundle carries:

- `schemaVersion`
- `themeId`
- `themeVersion`
- `name`
- `cssBody`
- `variables`
- `renderCss`
- `updatedAt`
- `checksum`

Theme variable shape:

- `valueType`: `color | float | length | percent | string | custom`
- `lightValue`: required
- `darkValue`: required
- Variable keys are normalized kebab-case without leading `--` in API payloads.
- Generated CSS declares:
  - `--light-<key>`
  - `--dark-<key>`
  - `--<key>: light-dark(var(--light-<key>), var(--dark-<key>));`

Example variable upsert request:

```bash
curl -sS -X PUT http://127.0.0.1:8000/v1/themes/default/variables/background-color \
  -H 'content-type: application/json' \
  -d '{
    "valueType": "color",
    "lightValue": "white",
    "darkValue": "black"
  }'
```

Example variable delete request:

```bash
curl -sS -X DELETE http://127.0.0.1:8000/v1/themes/default/variables/background-color
```

### Lifecycle

- Create graph type/layout set/link set/icon-set/theme: creates draft `version = 1`.
- Update: replaces draft and increments version.
- Theme variable upsert/delete also increments draft version.
- Publish: copies current draft into immutable published versions.
- Resolve bundle:
  - `stage=published` (default): latest published (or a specific `graph_type_version`)
  - `stage=draft`: current mutable draft

Consumers should use graph type/runtime/theme checksums for deterministic cache invalidation.

## Requirements

- Python `>=3.10`
- Node.js available on `PATH` (required for ELKJS layout)

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Quick Start

1. Activate the virtual environment: `source .venv/bin/activate`.
2. Start the API: `python -m graphapi`.
3. Confirm health: `curl http://127.0.0.1:8000/healthz`.
4. Fetch GraphLoom schema: `curl http://127.0.0.1:8000/schemas/minimal-input.schema.json`.
5. Validate JSON:
   ```bash
   curl -sS -X POST http://127.0.0.1:8000/validate \
     -H 'content-type: application/json' \
     -d '{
       "nodes": ["A", "B"],
       "links": ["A:eth0 -> B:eth1"]
     }'
   ```
6. Render SVG:
   ```bash
   curl -sS -X POST http://127.0.0.1:8000/render/svg \
     -H 'content-type: application/json' \
     -d '{
       "nodes": ["A", "B"],
       "links": ["A:eth0 -> B:eth1"]
     }'
   ```
## CLI Reference

```bash
python -m graphapi
```

Environment variables:

- `GRAPHAPI_HOST` (default: `0.0.0.0`)
- `GRAPHAPI_PORT` (default: `8000`)
- `PORT` (fallback if `GRAPHAPI_PORT` is unset)
- `GRAPHAPI_CORS_ORIGINS` (default: `http://127.0.0.1:9000,http://localhost:9000`, comma-separated list)
- `GRAPHAPI_CORS_ALLOW_CREDENTIALS` (default: `false`; requires explicit non-`*` origins when enabled)
- `GRAPHAPI_REQUEST_TIMEOUT_SECONDS` (default: `15`)
- `GRAPHAPI_MAX_REQUEST_BYTES` (default: `1048576`)
- `GRAPHAPI_RUNTIME_DB_PATH` (default: `~/.cache/graphapi/runtime.v1.sqlite3`)
- `GRAPHAPI_GRAPH_TYPE_STORE_PATH` (optional alias for runtime DB path)
- `GRAPHAPI_LAYOUT_SET_STORE_PATH` (optional alias for runtime DB path)
- `GRAPHAPI_LINK_SET_STORE_PATH` (optional alias for runtime DB path)
- `GRAPHAPI_THEME_STORE_PATH` (optional sqlite path alias for runtime DB path; if a `.json` path is provided, it is treated as a legacy import source)
- `GRAPHAPI_ICONSET_STORE_PATH` (optional alias for runtime DB path)
- `GRAPHAPI_DEFAULT_RENDER_CSS_PATH` (optional override for default theme CSS source)

## Python API

```python
from graphloom import MinimalGraphIn
from graphapi import render_svg_from_graph

graph = MinimalGraphIn.model_validate({
    "nodes": ["A", "B"],
    "links": ["A:eth0 -> B:eth1"],
})

svg = render_svg_from_graph(graph)
print(svg[:120])
```

## Input Expectations

`POST /render/svg` expects GraphLoom minimal graph JSON directly:

- `nodes` (array, optional): node names or node objects
- `links` (array, optional): shorthand links or edge objects

Minimal JSON example:

```json
{
  "nodes": ["A", "B"],
  "links": ["A:eth0 -> B:eth1"]
}
```

Schema for client-side validation:

- `GET /schemas/minimal-input.schema.json`
- `POST /validate`

Response:

- `200 OK` with `image/svg+xml` body on success
- `422` for invalid request payload shape
- `413` when request body exceeds size limit
- `504` when request processing exceeds timeout
- `500` for layout/render runtime failures

## Live Preview Pattern

Example browser-side pattern for editor-on-left / SVG-on-right:

```html
<textarea id="editor" spellcheck="false">
{
  "nodes": ["A", "B"],
  "links": ["A:eth0 -> B:eth1"]
}
</textarea>
<pre id="errors"></pre>
<div id="preview"></div>

<script type="module">
  import Ajv from "https://cdn.jsdelivr.net/npm/ajv@8/dist/ajv.min.js";

  const editor = document.getElementById("editor");
  const errors = document.getElementById("errors");
  const preview = document.getElementById("preview");

  const schema = await fetch("http://127.0.0.1:8000/schemas/minimal-input.schema.json").then(r => r.json());
  const ajv = new Ajv({ allErrors: true, strict: false });
  const validate = ajv.compile(schema);

  let timer = null;
  let inFlight = null;

  function scheduleRender() {
    clearTimeout(timer);
    timer = setTimeout(renderLatest, 300); // debounce
  }

  async function renderLatest() {
    let payload;
    try {
      payload = JSON.parse(editor.value);
    } catch (err) {
      errors.textContent = "Invalid JSON: " + err.message;
      return;
    }

    if (!validate(payload)) {
      errors.textContent = JSON.stringify(validate.errors, null, 2);
      return;
    }

    errors.textContent = "";

    if (inFlight) inFlight.abort(); // cancel stale request
    inFlight = new AbortController();

    try {
      const res = await fetch("http://127.0.0.1:8000/render/svg", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
        signal: inFlight.signal,
      });

      if (!res.ok) {
        errors.textContent = `Render failed: ${res.status} ${await res.text()}`;
        return;
      }

      preview.innerHTML = await res.text();
    } catch (err) {
      if (err.name !== "AbortError") {
        errors.textContent = "Request error: " + err.message;
      }
    }
  }

  editor.addEventListener("input", scheduleRender);
  scheduleRender();
</script>
```

## Settings

Runtime settings are controlled by environment variables:

- Process-level: `GRAPHAPI_HOST`, `GRAPHAPI_PORT`, `PORT`
- CORS: `GRAPHAPI_CORS_ORIGINS`
- Request controls: `GRAPHAPI_REQUEST_TIMEOUT_SECONDS`, `GRAPHAPI_MAX_REQUEST_BYTES`

Graph defaults currently use `graphloom.sample_settings()` in code. The API then always runs ELKJS layout (`mode=node`, `node_cmd=node`) before rendering.

## Troubleshooting

### Tests fail in CI but pass locally

Recreate the CI environment with a fresh virtualenv and run `python -m pytest -q`.

### Could not connect to server

Make sure the server is running in one terminal:

```bash
source .venv/bin/activate
python -m graphapi
```

Then test in another terminal:

```bash
curl -sS http://127.0.0.1:8000/healthz
```

### Layout fails

Install Node.js and ensure `node` is on `PATH`.

## Development

```bash
python -m pytest -q
python -m py_compile main.py src/graphapi/__init__.py src/graphapi/__main__.py src/graphapi/app.py
```

## Project Layout

```text
main.py
src/graphapi/
tests/
.github/workflows/
```

## Governance and Community

- Security policy: `SECURITY.md`
- Contribution guide: `CONTRIBUTING.md`
- Code of conduct: `CODE_OF_CONDUCT.md`
- Changelog: `CHANGELOG.md`
- Release process: `RELEASE.md`

## Automation

- CI build and sanity checks: `.github/workflows/ci.yml`
- Test matrix + coverage gate: `.github/workflows/test.yml`
- Secret scanning (gitleaks): `.github/workflows/gitleaks.yml`
- Tagged releases: `.github/workflows/release.yml`
- Dependency updates: `.github/dependabot.yml`

## Acknowledgements

- Python Packaging ecosystem (PyPA)
- Pytest
- GitHub Actions
- FastAPI
- GraphLoom
- GraphRender

## Third-Party Notices

See `THIRD_PARTY_NOTICES.md`.

## License

Licensed under Apache License 2.0. See `LICENSE`.
