# GraphAPI

FastAPI service that converts minimal GraphLoom YAML input into SVG output using GraphRender.

## Features

- FastAPI HTTP service with OpenAPI docs (`/docs`)
- `POST /render/svg` endpoint for YAML-to-SVG conversion
- GraphLoom integration for validation and default enrichment
- ELKJS layout is always executed before rendering
- GraphRender integration for SVG generation

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
4. Open API docs: `http://127.0.0.1:8000/docs`.
5. Render SVG:
   ```bash
   curl -sS -X POST http://127.0.0.1:8000/render/svg \
     -H 'content-type: application/json' \
     -d '{
       "yaml":"nodes:\n  - A\n  - B\nlinks:\n  - A:eth0 -> B:eth1\n"
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

## Python API

```python
from graphapi import render_svg_from_yaml

yaml_text = """
nodes:
  - A
  - B
links:
  - A:eth0 -> B:eth1
"""

svg = render_svg_from_yaml(yaml_text)
print(svg[:120])
```

## Input Expectations

`POST /render/svg` expects JSON with exactly one field:

- `yaml` (string, required): GraphLoom minimal graph YAML

Minimal YAML example:

```yaml
nodes:
  - A
  - B
links:
  - A:eth0 -> B:eth1
```

Response:

- `200 OK` with `image/svg+xml` body on success
- `400` for invalid YAML or GraphLoom validation errors
- `422` for unexpected request fields
- `500` for layout/render runtime failures

## Settings

Runtime settings are controlled by request fields and environment variables:

- Process-level: `GRAPHAPI_HOST`, `GRAPHAPI_PORT`, `PORT`

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
