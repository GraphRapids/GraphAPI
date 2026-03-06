from __future__ import annotations

import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def _unique_id(prefix: str) -> str:
    """Generate a unique resource ID for test isolation."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def test_validate_endpoint_accepts_minimal_input(http_client: httpx.Client) -> None:
    response = http_client.post("/validate", json={"nodes": ["A", "B"], "links": ["A -> B"]})
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True


def test_icon_set_crud_lifecycle(http_client: httpx.Client) -> None:
    icon_set_id = _unique_id("inttest")

    create_resp = http_client.post(
        "/v1/icon-sets",
        json={
            "iconSetId": icon_set_id,
            "name": "Integration Test Iconset",
            "entries": {"server": "mdi:server"},
        },
    )
    assert create_resp.status_code == 201

    try:
        get_resp = http_client.get(f"/v1/icon-sets/{icon_set_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["iconSetId"] == icon_set_id

        list_resp = http_client.get("/v1/icon-sets")
        assert list_resp.status_code == 200
        ids = [s["iconSetId"] for s in list_resp.json()["iconSets"]]
        assert icon_set_id in ids

        bundle_resp = http_client.get(
            f"/v1/icon-sets/{icon_set_id}/bundle",
            params={"stage": "draft"},
        )
        assert bundle_resp.status_code == 200
    finally:
        http_client.delete(f"/v1/icon-sets/{icon_set_id}")


def test_theme_crud_lifecycle(http_client: httpx.Client) -> None:
    theme_id = _unique_id("inttest")

    create_resp = http_client.post(
        "/v1/themes",
        json={
            "themeId": theme_id,
            "name": "Integration Test Theme",
            "cssBody": ".node { fill: blue; }",
            "variables": {},
        },
    )
    assert create_resp.status_code == 201

    try:
        get_resp = http_client.get(f"/v1/themes/{theme_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["themeId"] == theme_id
    finally:
        http_client.delete(f"/v1/themes/{theme_id}")


def test_graph_type_list_returns_default(http_client: httpx.Client) -> None:
    response = http_client.get("/v1/graph-types")
    assert response.status_code == 200
    body = response.json()
    assert "graphTypes" in body
    assert any(gt["graphTypeId"] == "default" for gt in body["graphTypes"])


def test_property_catalog_returns_elements(http_client: httpx.Client) -> None:
    response = http_client.get("/v1/property-catalog")
    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "v1"
    assert "elements" in body
    assert set(body["elements"].keys()) == {"canvas", "node", "subgraph", "edge", "port", "label"}


def test_schema_endpoint_returns_json_schema(http_client: httpx.Client) -> None:
    response = http_client.get("/schemas/minimal-input.schema.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["type"] == "object"
