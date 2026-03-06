# Docker & Integration Tests

## Building the Docker Image

```sh
docker build -t graphapi .
```

The image uses a multi-stage build:
- **Builder stage**: installs Python dependencies (including git-based packages)
- **Runtime stage**: minimal image with Python 3.12, Node.js (for ELKJS layout), and the installed service

## Running with Docker Compose

```sh
docker compose up --build -d
```

The service starts on port **8000** by default. Override with:

```sh
GRAPHAPI_PORT=9000 docker compose up --build -d
```

## Health Check

The service exposes a liveness endpoint:

```
GET /health
```

Returns HTTP 200 with:

```json
{"status": "ok"}
```

Both the Dockerfile `HEALTHCHECK` instruction and the `docker-compose.yml` healthcheck poll this endpoint.

## Running Unit Tests

Unit tests run without any external service:

```sh
pytest
```

Integration tests are automatically excluded from the default run.

## Running Integration Tests

Integration tests run against a live service instance:

```sh
# 1. Start the service
docker compose up --build -d

# 2. Wait for healthy status
docker compose exec graphapi curl -f http://localhost:8000/health

# 3. Run integration tests
SERVICE_URL=http://localhost:8000 pytest tests/integration/ -v

# 4. Tear down
docker compose down
```

You can also run integration tests with the marker:

```sh
SERVICE_URL=http://localhost:8000 pytest -m integration -v
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GRAPHAPI_HOST` | `0.0.0.0` | Bind address |
| `GRAPHAPI_PORT` | `8000` | Service port |
| `GRAPHAPI_CORS_ORIGINS` | localhost origins | Allowed CORS origins (comma-separated) |
| `SERVICE_URL` | `http://localhost:8000` | Base URL for integration tests |
