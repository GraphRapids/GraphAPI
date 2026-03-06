# Changelog

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `GET /health` endpoint returning `{"status": "ok"}` — cross-repo contract used by Graphras for readiness checks.
- Multi-stage `Dockerfile` for production runtime (no test dependencies in final image).
- `docker-compose.yml` with healthcheck polling `/health`.
- `.dockerignore` to keep the Docker build context clean.
- Integration test scaffolding under `tests/integration/` (skipped automatically when `GRAPHAPI_BASE_URL` is unset).
- `integration` pytest marker registered in `pyproject.toml`.
- `DOCKER.md` with build, compose, and integration test instructions.
- Unit test for the health endpoint (`tests/test_health.py`).


All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial template baseline.
- Reworked v1 theme contract to support typed light/dark variables, compiled top-of-theme CSS variable blocks, and theme variable CRUD endpoints.
