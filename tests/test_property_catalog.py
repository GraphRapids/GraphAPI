from __future__ import annotations

from fastapi.testclient import TestClient


def _as_property_map(payload: dict, *, element: str) -> dict[str, dict]:
    return {item["key"]: item for item in payload["elements"][element]}


def test_property_catalog_endpoint_lists_supported_elements(client: TestClient) -> None:
    response = client.get("/v1/property-catalog")
    assert response.status_code == 200

    body = response.json()
    assert body["schemaVersion"] == "v1"
    assert "checksum" in body
    assert set(body["elements"].keys()) == {"canvas", "node", "subgraph", "edge", "port", "label"}
    assert body["elements"]["edge"]


def test_property_catalog_filter_by_element(client: TestClient) -> None:
    edge_only = client.get("/v1/property-catalog", params={"element": "edge"})
    assert edge_only.status_code == 200
    edge_payload = edge_only.json()

    repeated = client.get("/v1/property-catalog", params={"element": "edge"})
    assert repeated.status_code == 200
    assert repeated.json()["checksum"] == edge_payload["checksum"]
    assert set(edge_payload["elements"].keys()) == {"edge"}


def test_property_catalog_edge_graphrapids_properties_include_enums_and_defaults(client: TestClient) -> None:
    response = client.get("/v1/property-catalog", params={"element": "edge"})
    assert response.status_code == 200
    body = response.json()
    props = _as_property_map(body, element="edge")

    marker_start = props["graphrapids.edge.marker_start"]
    marker_end = props["graphrapids.edge.marker_end"]
    style = props["graphrapids.edge.style"]

    assert marker_start["valueType"] == "enum"
    assert marker_start["defaultValue"] == "NONE"
    assert marker_end["valueType"] == "enum"
    assert marker_end["defaultValue"] == "NONE"
    assert style["valueType"] == "enum"
    assert style["defaultValue"] == "SOLID"

    assert "SOLID_ARROW" in marker_start["enumValues"]
    assert "HOLLOW_DIAMOND" in marker_end["enumValues"]
    assert "LONG_DASH_DOT" in style["enumValues"]

    assert "layoutSet.elkSettings" in style["writableIn"]
    assert "linkSet.entries[*].elkProperties" in style["writableIn"]
