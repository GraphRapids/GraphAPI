# AGENTS.md

## Scope
- Work only in this repository (`GraphAPI`).
- Keep changes focused on the user request.

## Environment
- Use the project virtualenv for all Python commands: `.venv`.
- Run commands from repository root unless a task requires a subdirectory.
- Verify context before changes:
  - `pwd`
  - `which python`

## Tech Stack
- API framework: FastAPI.
- Graph pipeline:
  1. Accept GraphLoom minimal JSON input.
  2. Enrich with GraphLoom defaults.
  3. Always run ELKJS layout.
  4. Render SVG with GraphRender.

## API Contract Rules
- `POST /render/svg` accepts GraphLoom minimal JSON directly (not YAML).
- `GET /schemas/minimal-input.schema.json` exposes GraphLoom input schema.
- Do not add request-level switches for layout behavior unless explicitly requested.

## Coding Rules
- Prefer small, reviewable changes.
- Avoid adding dependencies unless necessary.
- Preserve existing project structure and conventions.
- Update tests and README when behavior or API contract changes.

## Validation
- Run tests after code changes:
  - `python -m pytest -q`
- For API behavior changes, verify at least:
  - `GET /healthz`
  - `POST /render/svg` with minimal valid JSON input.

## Git Workflow
- Never use destructive git commands.
- Do not commit or push unless explicitly requested.
- When asked to commit, stage only intended files and use a clear message.

## Security
- Never hardcode secrets or tokens.
- Prefer environment variables for runtime configuration.
