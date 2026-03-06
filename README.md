[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE) [![CI](https://github.com/GraphRapids/GraphAPI/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/GraphRapids/GraphAPI/actions/workflows/ci.yml)

# GraphAPI

Orchestrates the GraphRapids pipeline and exposes functionality via HTTP/WebSocket.

## Quick Start

```bash
pip install -e ".[dev]"
pytest
```

## Docker

GraphAPI ships with a multi-stage `Dockerfile` and `docker-compose.yml`.

```bash
# Build and start
docker compose up --build -d --wait

# Verify the service is healthy
curl http://localhost:8000/health
# => {"status": "ok"}
```

See [DOCKER.md](DOCKER.md) for full details on building, running, environment
variables, and integration tests.

## Health Endpoint

| Method | Path      | Response            |
|--------|-----------|---------------------|
| GET    | `/health` | `{"status": "ok"}` |

This is a **cross-repo contract** consumed by Graphras and other orchestration
tooling to determine service readiness. Do not change the path or response
shape without coordinating with downstream consumers.

## Integration Tests

Integration tests live in `tests/integration/` and run against a live GraphAPI
instance. They are **skipped automatically** when `GRAPHAPI_BASE_URL` is not set.

```bash
GRAPHAPI_BASE_URL=http://localhost:8000 pytest tests/integration/ -v
```

See [DOCKER.md](DOCKER.md) for more examples.

## License

Apache 2.0 — see [LICENSE](LICENSE).
