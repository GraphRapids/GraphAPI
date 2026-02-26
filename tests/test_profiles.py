from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from graphapi.profile_defaults import default_profile_create_request
from graphapi.profile_store import ProfileStore


graphapi_module = importlib.import_module("graphapi.app")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path / "profiles.v1.json")
    store.ensure_default_profile(default_profile_create_request())
    monkeypatch.setattr(graphapi_module, "profile_store", store)
    return TestClient(graphapi_module.app)


def _new_profile_payload(client: TestClient, profile_id: str) -> dict:
    default_bundle = client.get(
        "/v1/profiles/default/bundle",
        params={"stage": "draft"},
    ).json()
    return {
        "profileId": profile_id,
        "name": f"{profile_id} profile",
        "nodeTypes": ["router", "switch", "gateway"],
        "linkTypes": ["directed", "undirected"],
        "elkSettings": default_bundle["elkSettings"],
        "renderCss": ".node.router > rect { fill: #334455; }\n.edge.directed polyline { stroke: #334455; }\n",
    }


def test_profiles_crud_publish_flow(client: TestClient) -> None:
    create_payload = _new_profile_payload(client, "team-alpha")

    created = client.post("/v1/profiles", json=create_payload)
    assert created.status_code == 201
    body = created.json()
    assert body["profileId"] == "team-alpha"
    assert body["draft"]["profileVersion"] == 1
    assert body["publishedVersions"] == []

    list_response = client.get("/v1/profiles")
    assert list_response.status_code == 200
    profile_ids = {item["profileId"] for item in list_response.json()["profiles"]}
    assert {"default", "team-alpha"}.issubset(profile_ids)

    update_payload = {
        **{k: v for k, v in create_payload.items() if k != "profileId"},
        "name": "team alpha updated",
        "nodeTypes": ["router", "switch", "gateway", "firewall"],
    }
    updated = client.put("/v1/profiles/team-alpha", json=update_payload)
    assert updated.status_code == 200
    assert updated.json()["draft"]["profileVersion"] == 2

    published = client.post("/v1/profiles/team-alpha/publish")
    assert published.status_code == 200
    assert published.json()["profileVersion"] == 2

    published_bundle = client.get(
        "/v1/profiles/team-alpha/bundle",
        params={"stage": "published"},
    )
    assert published_bundle.status_code == 200
    assert published_bundle.json()["profileVersion"] == 2

    second_update = client.put(
        "/v1/profiles/team-alpha",
        json={
            **update_payload,
            "name": "team alpha draft v3",
            "nodeTypes": ["router", "switch"],
        },
    )
    assert second_update.status_code == 200
    assert second_update.json()["draft"]["profileVersion"] == 3

    latest_published_still_immutable = client.get(
        "/v1/profiles/team-alpha/bundle",
        params={"stage": "published"},
    )
    assert latest_published_still_immutable.status_code == 200
    assert latest_published_still_immutable.json()["profileVersion"] == 2


def test_profiles_error_mapping_and_validation(client: TestClient) -> None:
    payload = _new_profile_payload(client, "team-beta")
    assert client.post("/v1/profiles", json=payload).status_code == 201

    duplicate = client.post("/v1/profiles", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "PROFILE_ALREADY_EXISTS"

    invalid_elk = client.put(
        "/v1/profiles/team-beta",
        json={
            "name": "broken",
            "nodeTypes": ["router"],
            "linkTypes": ["directed"],
            "elkSettings": {"layout_options": "not-an-object"},
            "renderCss": "body{}",
        },
    )
    assert invalid_elk.status_code == 400
    assert invalid_elk.json()["detail"]["code"] == "INVALID_ELK_SETTINGS"

    unpublished_bundle = client.get(
        "/v1/profiles/team-beta/bundle",
        params={"stage": "published"},
    )
    assert unpublished_bundle.status_code == 404
    assert unpublished_bundle.json()["detail"]["code"] == "PROFILE_NOT_PUBLISHED"


def test_autocomplete_catalog_and_render_profile_flow(client: TestClient, monkeypatch) -> None:
    payload = _new_profile_payload(client, "runtime")
    assert client.post("/v1/profiles", json=payload).status_code == 201
    assert client.post("/v1/profiles/runtime/publish").status_code == 200

    bundle = client.get(
        "/v1/profiles/runtime/bundle",
        params={"stage": "published"},
    )
    assert bundle.status_code == 200
    bundle_body = bundle.json()

    catalog = client.get(
        "/v1/autocomplete/catalog",
        params={"profile_id": "runtime"},
    )
    assert catalog.status_code == 200
    catalog_body = catalog.json()
    assert catalog_body["profileVersion"] == bundle_body["profileVersion"]
    assert catalog_body["checksum"] == bundle_body["checksum"]
    assert catalog_body["nodeTypes"] == bundle_body["nodeTypes"]

    monkeypatch.setattr(graphapi_module, "layout_with_elkjs", lambda payload, *, mode, node_cmd: payload)

    render_response = client.post(
        "/render/svg",
        params={"profile_id": "runtime"},
        json={
            "nodes": [
                {"name": "A", "type": "router"},
                {"name": "B", "type": "switch"},
            ],
            "links": [{"from": "A", "to": "B", "type": "directed"}],
        },
    )
    assert render_response.status_code == 200
    assert render_response.headers["x-graphapi-profile-checksum"] == bundle_body["checksum"]
    assert 'class="node router"' in render_response.text
    assert 'class="edge directed"' in render_response.text
    assert "fill: #334455" in render_response.text


def test_openapi_includes_profile_contract_endpoints(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    openapi = response.json()
    paths = openapi["paths"]
    assert "/v1/profiles" in paths
    assert "/v1/profiles/{id}" in paths
    assert "/v1/profiles/{id}/bundle" in paths
    assert "/v1/profiles/{id}/publish" in paths
    assert "/v1/autocomplete/catalog" in paths

    schemas = openapi["components"]["schemas"]
    assert "ProfileBundleV1" in schemas
    assert "ProfileCreateRequestV1" in schemas
    assert "AutocompleteCatalogResponseV1" in schemas
