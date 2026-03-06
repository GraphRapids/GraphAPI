from __future__ import annotations


def test_health_endpoint_returns_ok(client) -> None:
    """Unit test: GET /health returns 200 with {"status": "ok"}."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
