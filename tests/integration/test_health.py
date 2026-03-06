from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.integration


def test_health_endpoint_returns_ok(http_client: httpx.Client) -> None:
    """Verify the liveness health check against the live service."""
    response = http_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok"}
    assert response.headers["content-type"].startswith("application/json")
