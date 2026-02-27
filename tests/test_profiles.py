from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from graphapi.graphtype_defaults import default_graph_type_create_request
from graphapi.graphtype_store import GraphTypeStore
from graphapi.iconset_defaults import default_iconset_create_request
from graphapi.iconset_store import IconsetStore
from graphapi.layoutset_defaults import default_layout_set_create_request
from graphapi.layoutset_store import LayoutSetStore
from graphapi.linkset_defaults import default_link_set_create_request
from graphapi.linkset_store import LinkSetStore
from graphapi.theme_defaults import default_theme_create_request
from graphapi.theme_store import ThemeStore


graphapi_module = importlib.import_module("graphapi.app")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    runtime_db_path = tmp_path / "runtime.v1.sqlite3"

    iconset_store = IconsetStore(runtime_db_path)
    iconset_store.ensure_default_iconset(default_iconset_create_request())

    layout_set_store = LayoutSetStore(runtime_db_path)
    layout_set_store.ensure_default_layout_set(default_layout_set_create_request())

    link_set_store = LinkSetStore(runtime_db_path)
    link_set_store.ensure_default_link_set(default_link_set_create_request())

    graph_type_store = GraphTypeStore(runtime_db_path, iconset_store, layout_set_store, link_set_store)
    graph_type_store.ensure_default_graph_type(default_graph_type_create_request())

    theme_store = ThemeStore(runtime_db_path)
    theme_store.ensure_default_theme(default_theme_create_request())

    monkeypatch.setattr(graphapi_module, "iconset_store", iconset_store)
    monkeypatch.setattr(graphapi_module, "layout_set_store", layout_set_store)
    monkeypatch.setattr(graphapi_module, "link_set_store", link_set_store)
    monkeypatch.setattr(graphapi_module, "graph_type_store", graph_type_store)
    monkeypatch.setattr(graphapi_module, "theme_store", theme_store)

    return TestClient(graphapi_module.app)


def _new_theme_payload(theme_id: str) -> dict:
    return {
        "themeId": theme_id,
        "name": f"{theme_id} theme",
        "cssBody": ".node.router > rect { fill: var(--node-fill); }\n.edge.directed polyline { stroke: var(--edge-color); }\n",
        "variables": {
            "node-fill": {
                "valueType": "color",
                "lightValue": "#334455",
                "darkValue": "#778899",
            },
            "edge-color": {
                "valueType": "color",
                "lightValue": "#334455",
                "darkValue": "#aabbcc",
            },
        },
    }


def test_layout_set_crud_publish_flow(client: TestClient) -> None:
    default_bundle = client.get("/v1/layout-sets/default/bundle", params={"stage": "draft"})
    assert default_bundle.status_code == 200

    payload = {
        "layoutSetId": "team-layout",
        "name": "Team Layout",
        "elkSettings": default_bundle.json()["elkSettings"],
    }
    created = client.post("/v1/layout-sets", json=payload)
    assert created.status_code == 201
    assert created.json()["draft"]["layoutSetVersion"] == 1

    updated = client.put(
        "/v1/layout-sets/team-layout",
        json={
            "name": "Team Layout Updated",
            "elkSettings": payload["elkSettings"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["draft"]["layoutSetVersion"] == 2

    upsert = client.put(
        "/v1/layout-sets/team-layout/entries/elk.spacing.nodeNode",
        json={"value": 72},
    )
    assert upsert.status_code == 200
    assert upsert.json()["draft"]["layoutSetVersion"] == 3
    assert upsert.json()["draft"]["elkSettings"]["elk.spacing.nodeNode"] == 72

    delete = client.delete("/v1/layout-sets/team-layout/entries/elk.spacing.nodeNode")
    assert delete.status_code == 200
    assert delete.json()["draft"]["layoutSetVersion"] == 4
    assert "elk.spacing.nodeNode" not in delete.json()["draft"]["elkSettings"]

    published = client.post("/v1/layout-sets/team-layout/publish")
    assert published.status_code == 200
    assert published.json()["layoutSetVersion"] == 4


def test_link_set_crud_entry_and_publish_flow(client: TestClient) -> None:
    created = client.post(
        "/v1/link-sets",
        json={
            "linkSetId": "team-links",
            "name": "Team Links",
            "entries": {
                "directed": {
                    "label": "Directed",
                    "elkEdgeType": "DIRECTED",
                    "elkProperties": {},
                }
            },
        },
    )
    assert created.status_code == 201
    assert created.json()["draft"]["linkSetVersion"] == 1

    upsert = client.put(
        "/v1/link-sets/team-links/entries/dependency",
        json={
            "label": "Dependency",
            "elkEdgeType": "DIRECTED",
            "elkProperties": {"org.eclipse.elk.edge.thickness": 2},
        },
    )
    assert upsert.status_code == 200
    assert upsert.json()["draft"]["linkSetVersion"] == 2
    assert "dependency" in upsert.json()["draft"]["entries"]

    delete = client.delete("/v1/link-sets/team-links/entries/directed")
    assert delete.status_code == 200
    assert delete.json()["draft"]["linkSetVersion"] == 3

    published = client.post("/v1/link-sets/team-links/publish")
    assert published.status_code == 200
    assert published.json()["linkSetVersion"] == 3


def test_iconset_crud_and_resolve_flow(client: TestClient) -> None:
    create_a = client.post(
        "/v1/icon-sets",
        json={
            "iconSetId": "team-a",
            "name": "Team A",
            "entries": {
                "router": "mdi:router",
                "switch": "mdi:switch",
            },
        },
    )
    assert create_a.status_code == 201

    create_b = client.post(
        "/v1/icon-sets",
        json={
            "iconSetId": "team-b",
            "name": "Team B",
            "entries": {
                "router": "material-symbols:router-outline",
                "gateway": "mdi:gate",
            },
        },
    )
    assert create_b.status_code == 201

    assert client.post("/v1/icon-sets/team-a/publish").status_code == 200
    assert client.post("/v1/icon-sets/team-b/publish").status_code == 200

    conflict = client.post(
        "/v1/icon-sets/resolve",
        json={
            "iconSetRefs": [
                {"iconSetId": "team-a", "stage": "published", "iconSetVersion": 1},
                {"iconSetId": "team-b", "stage": "published", "iconSetVersion": 1},
            ],
            "conflictPolicy": "reject",
        },
    )
    assert conflict.status_code == 409

    last_wins = client.post(
        "/v1/icon-sets/resolve",
        json={
            "iconSetRefs": [
                {"iconSetId": "team-a", "stage": "published", "iconSetVersion": 1},
                {"iconSetId": "team-b", "stage": "published", "iconSetVersion": 1},
            ],
            "conflictPolicy": "last-wins",
        },
    )
    assert last_wins.status_code == 200
    body = last_wins.json()
    assert body["resolvedEntries"]["router"] == "material-symbols:router-outline"
    assert body["resolvedEntries"]["switch"] == "mdi:switch"
    assert body["resolvedEntries"]["gateway"] == "mdi:gate"


def test_graph_type_crud_catalog_runtime_and_render_headers(client: TestClient, monkeypatch) -> None:
    created_iconset = client.post(
        "/v1/icon-sets",
        json={
            "iconSetId": "telecom",
            "name": "Telecom",
            "entries": {
                "router": "mdi:router",
                "gateway": "mdi:gate",
                "firewall": "mdi:shield",
            },
        },
    )
    assert created_iconset.status_code == 201
    assert client.post("/v1/icon-sets/telecom/publish").status_code == 200

    layout_bundle = client.get("/v1/layout-sets/default/bundle", params={"stage": "published"}).json()

    created_link_set = client.post(
        "/v1/link-sets",
        json={
            "linkSetId": "telecom-links",
            "name": "Telecom Links",
            "entries": {
                "directed": {
                    "label": "Directed",
                    "elkEdgeType": "DIRECTED",
                    "elkProperties": {},
                },
                "dependency": {
                    "label": "Dependency",
                    "elkEdgeType": "DIRECTED",
                    "elkProperties": {"org.eclipse.elk.edge.thickness": 2},
                },
            },
        },
    )
    assert created_link_set.status_code == 201
    assert client.post("/v1/link-sets/telecom-links/publish").status_code == 200

    create_graph_type = client.post(
        "/v1/graph-types",
        json={
            "graphTypeId": "telecom-type",
            "name": "Telecom Type",
            "layoutSetRef": {
                "layoutSetId": "default",
                "layoutSetVersion": layout_bundle["layoutSetVersion"],
            },
            "iconSetRefs": [{"iconSetId": "telecom", "iconSetVersion": 1}],
            "linkSetRef": {"linkSetId": "telecom-links", "linkSetVersion": 1},
            "iconConflictPolicy": "reject",
        },
    )
    assert create_graph_type.status_code == 201

    publish_graph_type = client.post("/v1/graph-types/telecom-type/publish")
    assert publish_graph_type.status_code == 200
    published_body = publish_graph_type.json()
    assert published_body["nodeTypes"] == ["firewall", "gateway", "router"]
    assert published_body["linkTypes"] == ["dependency", "directed"]
    assert published_body["iconSetResolutionChecksum"]

    catalog = client.get(
        "/v1/autocomplete/catalog",
        params={"graph_type_id": "telecom-type", "stage": "published"},
    )
    assert catalog.status_code == 200
    catalog_body = catalog.json()
    assert catalog_body["graphTypeVersion"] == published_body["graphTypeVersion"]
    assert catalog_body["nodeTypes"] == ["firewall", "gateway", "router"]
    assert catalog_body["linkTypes"] == ["dependency", "directed"]

    runtime = client.get(
        "/v1/graph-types/telecom-type/runtime",
        params={"stage": "published"},
    )
    assert runtime.status_code == 200
    runtime_body = runtime.json()
    assert runtime_body["resolvedEntries"]["router"] == "mdi:router"
    assert runtime_body["checksum"] == published_body["runtimeChecksum"]

    theme_payload = _new_theme_payload("night")
    assert client.post("/v1/themes", json=theme_payload).status_code == 201
    assert client.post("/v1/themes/night/publish").status_code == 200

    monkeypatch.setattr(graphapi_module, "layout_with_elkjs", lambda payload, *, mode, node_cmd: payload)

    render_response = client.post(
        "/render/svg",
        params={"graph_type_id": "telecom-type", "theme_id": "night"},
        json={
            "nodes": [
                {"name": "A", "type": "router"},
                {"name": "B", "type": "gateway"},
            ],
            "links": [{"from": "A", "to": "B", "type": "directed"}],
        },
    )
    assert render_response.status_code == 200
    assert render_response.headers["x-graphapi-graph-type-id"] == "telecom-type"
    assert render_response.headers["x-graphapi-graph-type-runtime-checksum"] == published_body["runtimeChecksum"]
    assert render_response.headers["x-graphapi-icon-set-sources"] == "telecom@1"
    assert render_response.headers["x-graphapi-runtime-checksum"]


def test_theme_crud_publish_flow(client: TestClient) -> None:
    create_payload = _new_theme_payload("midnight")

    created = client.post("/v1/themes", json=create_payload)
    assert created.status_code == 201
    body = created.json()
    assert body["themeId"] == "midnight"
    assert body["draft"]["themeVersion"] == 1
    assert "--node-fill: light-dark(var(--light-node-fill), var(--dark-node-fill));" in body["draft"]["renderCss"]

    updated = client.put(
        "/v1/themes/midnight",
        json={
            "name": "midnight updated",
            "cssBody": ".node.router > rect { fill: var(--node-fill); }",
            "variables": {
                "node-fill": {
                    "valueType": "color",
                    "lightValue": "#112233",
                    "darkValue": "#445566",
                }
            },
        },
    )
    assert updated.status_code == 200
    assert updated.json()["draft"]["themeVersion"] == 2

    upsert = client.put(
        "/v1/themes/midnight/variables/background-color",
        json={"valueType": "color", "lightValue": "white", "darkValue": "black"},
    )
    assert upsert.status_code == 200
    assert upsert.json()["draft"]["themeVersion"] == 3
    assert upsert.json()["draft"]["variables"]["background-color"]["valueType"] == "color"

    deleted = client.delete("/v1/themes/midnight/variables/node-fill")
    assert deleted.status_code == 200
    assert deleted.json()["draft"]["themeVersion"] == 4
    assert "node-fill" not in deleted.json()["draft"]["variables"]

    published = client.post("/v1/themes/midnight/publish")
    assert published.status_code == 200
    assert published.json()["themeVersion"] == 4


def test_error_mapping_for_invalid_graph_type_references(client: TestClient) -> None:
    payload = {
        "graphTypeId": "invalid-ref",
        "name": "Broken",
        "layoutSetRef": {"layoutSetId": "missing", "layoutSetVersion": 1},
        "iconSetRefs": [{"iconSetId": "default", "iconSetVersion": 1}],
        "linkSetRef": {"linkSetId": "default", "linkSetVersion": 1},
        "iconConflictPolicy": "reject",
    }
    response = client.post("/v1/graph-types", json=payload)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "GRAPH_TYPE_LAYOUT_SET_REF_INVALID"


def test_openapi_includes_modular_v1_contract_endpoints(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200

    openapi = response.json()
    paths = openapi["paths"]

    assert "/v1/icon-sets" in paths
    assert "/v1/layout-sets" in paths
    assert "/v1/layout-sets/{id}/entries/{key}" in paths
    assert "/v1/link-sets" in paths
    assert "/v1/graph-types" in paths
    assert "/v1/graph-types/{id}/runtime" in paths
    assert "/v1/themes/{id}/variables/{key}" in paths
    assert "/v1/autocomplete/catalog" in paths
    assert "/render/svg" in paths

    assert "/v1/profiles" not in paths
    assert "/v1/icon_sets" not in paths
    assert all(not key.startswith("/v2/") for key in paths)

    schemas = openapi["components"]["schemas"]
    assert "GraphTypeBundleV1" in schemas
    assert "LayoutSetBundleV1" in schemas
    assert "LinkSetBundleV1" in schemas
    assert "AutocompleteCatalogResponseV1" in schemas
