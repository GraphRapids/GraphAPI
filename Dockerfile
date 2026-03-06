# ------------------------------------------------------------------
# Stage 1 - Builder: install Python dependencies
# ------------------------------------------------------------------
FROM python:3.12-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

RUN pip install --no-cache-dir .

# ------------------------------------------------------------------
# Stage 2 - Production runtime (slim, no test deps)
# ------------------------------------------------------------------
FROM python:3.12-slim

# Node.js is required at runtime by GraphLoom for ELK layout
RUN apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# GraphAPI listens on port 8000 by default
EXPOSE 8000

ENV GRAPHAPI_HOST="0.0.0.0"
ENV GRAPHAPI_PORT="8000"

CMD ["python", "-m", "graphapi"]
