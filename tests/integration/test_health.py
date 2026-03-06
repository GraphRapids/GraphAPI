from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_health_returns_ok(http_client) -> None:
    """GET /health must return 200 with {"status": "ok"}."""
    response = http_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok"}
