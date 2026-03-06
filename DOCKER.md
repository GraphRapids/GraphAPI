# Docker & Integration Tests

## Building the Docker image

```bash
docker build -t graphapi .
```

The image exposes **port 8000** by default.

## Running with Docker Compose

```bash
docker compose up --build -d
```

Docker Compose includes a **healthcheck** that polls `GET /health` every 5 seconds.
Wait for the service to become healthy:

```bash
docker compose up --build -d --wait
```

## Health endpoint

| Method | Path      | Response          |
|--------|-----------|-------------------|
| GET    | `/health` | `{"status":"ok"}` |

This endpoint is a **cross-repo contract** used by Graphras and other
orchestration tooling to determine service readiness.

## Running integration tests

Integration tests live in `tests/integration/` and require a running
GraphAPI instance. They are **skipped automatically** when
`GRAPHAPI_BASE_URL` is not set.

```bash
# Start the service
docker compose up --build -d --wait

# Run integration tests only
GRAPHAPI_BASE_URL=http://localhost:8000 pytest tests/integration/ -v

# Run all tests (unit + integration)
GRAPHAPI_BASE_URL=http://localhost:8000 pytest -v

# Run unit tests only (default behaviour)
pytest -v
```

You can also select or deselect by marker:

```bash
# Only integration-marked tests
pytest -m integration

# Everything except integration tests
pytest -m "not integration"
```

## Environment variables

| Variable              | Default                | Description                          |
|-----------------------|------------------------|--------------------------------------|
| `GRAPHAPI_HOST`       | `0.0.0.0`             | Bind address inside the container    |
| `GRAPHAPI_PORT`       | `8000`                 | Listen port inside the container     |
| `GRAPHAPI_BASE_URL`   | *(unset)*              | Base URL for integration tests       |
