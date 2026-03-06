from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_validate_endpoint_accepts_minimal_input(http_client) -> None:
    """POST /validate with a simple graph should return valid=true."""
    payload = {"nodes": ["A", "B"], "links": ["A:eth0 -> B:eth1"]}
    response = http_client.post("/validate", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["normalized"]["nodes"]


def test_graphloom_schema_endpoint_is_available(http_client) -> None:
    """GET /schemas/minimal-input.schema.json should return a JSON Schema."""
    response = http_client.get("/schemas/minimal-input.schema.json")
    assert response.status_code == 200
    assert response.json()["type"] == "object"


def test_graph_types_list_includes_default(http_client) -> None:
    """GET /v1/graph-types should include the default graph type."""
    response = http_client.get("/v1/graph-types")
    assert response.status_code == 200
    ids = [gt["graphTypeId"] for gt in response.json()["graphTypes"]]
    assert "default" in ids


def test_themes_list_includes_default(http_client) -> None:
    """GET /v1/themes should include the default theme."""
    response = http_client.get("/v1/themes")
    assert response.status_code == 200
    ids = [t["themeId"] for t in response.json()["themes"]]
    assert "default" in ids


def test_icon_sets_list_includes_default(http_client) -> None:
    """GET /v1/icon-sets should include the default icon set."""
    response = http_client.get("/v1/icon-sets")
    assert response.status_code == 200
    ids = [i["iconSetId"] for i in response.json()["iconSets"]]
    assert "default" in ids


def test_icon_set_crud_lifecycle(http_client) -> None:
    """Create, read, and delete a test icon set to verify CRUD."""
    icon_set_id = "integration-test-icons"

    # Create
    create_resp = http_client.post(
        "/v1/icon-sets",
        json={
            "iconSetId": icon_set_id,
            "name": "Integration Test Icons",
            "entries": {"server": "mdi:server"},
        },
    )
    assert create_resp.status_code == 201

    try:
        # Read
        get_resp = http_client.get(f"/v1/icon-sets/{icon_set_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["iconSetId"] == icon_set_id
    finally:
        # Cleanup - always attempt deletion
        http_client.delete(f"/v1/icon-sets/{icon_set_id}")

    # Verify deletion
    gone_resp = http_client.get(f"/v1/icon-sets/{icon_set_id}")
    assert gone_resp.status_code == 404
