# ---- Stage 1: Builder ----
FROM python:3.12-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir .

# ---- Stage 2: Runtime ----
FROM python:3.12-slim

# Node.js is required by GraphLoom for ELKJS layout execution.
# curl is used by the HEALTHCHECK instruction.
RUN apt-get update && \
    apt-get install -y --no-install-recommends nodejs curl && \
    rm -rf /var/lib/apt/lists/*

# Non-root user for production security
RUN useradd --create-home appuser

WORKDIR /app

# Copy installed Python packages and console scripts from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Default service configuration (override via environment variables)
ENV GRAPHAPI_HOST=0.0.0.0
ENV GRAPHAPI_PORT=8000

# Expose the default GraphAPI service port
EXPOSE 8000

USER appuser

# Liveness health check — polls GET /health
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the production ASGI server via the console entrypoint
CMD ["graphapi"]
