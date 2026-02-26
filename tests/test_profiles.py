from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from graphapi.profile_defaults import default_profile_create_request
from graphapi.profile_store import ProfileStore
from graphapi.iconset_defaults import default_iconset_create_request
from graphapi.iconset_store import IconsetStore
from graphapi.profile_v2_defaults import default_profile_create_request_v2
from graphapi.profile_v2_store import ProfileStoreV2
from graphapi.theme_defaults import default_theme_create_request
from graphapi.theme_store import ThemeStore


graphapi_module = importlib.import_module("graphapi.app")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    profile_store = ProfileStore(tmp_path / "profiles.v1.json")
    profile_store.ensure_default_profile(default_profile_create_request())
    iconset_store = IconsetStore(tmp_path / "iconsets.v1.json")
    iconset_store.ensure_default_iconset(default_iconset_create_request())
    profile_store_v2 = ProfileStoreV2(tmp_path / "profiles.v2.json", iconset_store)
    profile_store_v2.ensure_default_profile(default_profile_create_request_v2())
    theme_store = ThemeStore(tmp_path / "themes.v1.json")
    theme_store.ensure_default_theme(default_theme_create_request())

    monkeypatch.setattr(graphapi_module, "profile_store", profile_store)
    monkeypatch.setattr(graphapi_module, "iconset_store", iconset_store)
    monkeypatch.setattr(graphapi_module, "profile_store_v2", profile_store_v2)
    monkeypatch.setattr(graphapi_module, "theme_store", theme_store)
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
    }


def _new_theme_payload(theme_id: str) -> dict:
    return {
        "themeId": theme_id,
        "name": f"{theme_id} theme",
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


def test_themes_crud_publish_flow(client: TestClient) -> None:
    create_payload = _new_theme_payload("midnight")

    created = client.post("/v1/themes", json=create_payload)
    assert created.status_code == 201
    body = created.json()
    assert body["themeId"] == "midnight"
    assert body["draft"]["themeVersion"] == 1
    assert body["publishedVersions"] == []

    list_response = client.get("/v1/themes")
    assert list_response.status_code == 200
    theme_ids = {item["themeId"] for item in list_response.json()["themes"]}
    assert {"default", "midnight"}.issubset(theme_ids)

    updated = client.put(
        "/v1/themes/midnight",
        json={
            "name": "midnight updated",
            "renderCss": ".node.router > rect { fill: #112233; }",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["draft"]["themeVersion"] == 2

    published = client.post("/v1/themes/midnight/publish")
    assert published.status_code == 200
    assert published.json()["themeVersion"] == 2

    published_bundle = client.get(
        "/v1/themes/midnight/bundle",
        params={"stage": "published"},
    )
    assert published_bundle.status_code == 200
    assert published_bundle.json()["themeVersion"] == 2


def test_profiles_and_themes_error_mapping_and_validation(client: TestClient) -> None:
    profile_payload = _new_profile_payload(client, "team-beta")
    assert client.post("/v1/profiles", json=profile_payload).status_code == 201

    duplicate_profile = client.post("/v1/profiles", json=profile_payload)
    assert duplicate_profile.status_code == 409
    assert duplicate_profile.json()["detail"]["code"] == "PROFILE_ALREADY_EXISTS"

    invalid_elk = client.put(
        "/v1/profiles/team-beta",
        json={
            "name": "broken",
            "nodeTypes": ["router"],
            "linkTypes": ["directed"],
            "elkSettings": {"layout_options": "not-an-object"},
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

    theme_payload = _new_theme_payload("ocean")
    assert client.post("/v1/themes", json=theme_payload).status_code == 201
    duplicate_theme = client.post("/v1/themes", json=theme_payload)
    assert duplicate_theme.status_code == 409
    assert duplicate_theme.json()["detail"]["code"] == "THEME_ALREADY_EXISTS"

    unpublished_theme = client.get(
        "/v1/themes/ocean/bundle",
        params={"stage": "published"},
    )
    assert unpublished_theme.status_code == 404
    assert unpublished_theme.json()["detail"]["code"] == "THEME_NOT_PUBLISHED"


def test_autocomplete_catalog_and_render_runtime_flow(client: TestClient, monkeypatch) -> None:
    profile_payload = _new_profile_payload(client, "runtime")
    assert client.post("/v1/profiles", json=profile_payload).status_code == 201
    assert client.post("/v1/profiles/runtime/publish").status_code == 200

    theme_payload = _new_theme_payload("night")
    assert client.post("/v1/themes", json=theme_payload).status_code == 201
    assert client.post("/v1/themes/night/publish").status_code == 200

    profile_bundle = client.get(
        "/v1/profiles/runtime/bundle",
        params={"stage": "published"},
    )
    assert profile_bundle.status_code == 200
    profile_bundle_body = profile_bundle.json()

    theme_bundle = client.get(
        "/v1/themes/night/bundle",
        params={"stage": "published"},
    )
    assert theme_bundle.status_code == 200
    theme_bundle_body = theme_bundle.json()

    catalog = client.get(
        "/v1/autocomplete/catalog",
        params={"profile_id": "runtime"},
    )
    assert catalog.status_code == 200
    catalog_body = catalog.json()
    assert catalog_body["profileVersion"] == profile_bundle_body["profileVersion"]
    assert catalog_body["checksum"] == profile_bundle_body["checksum"]
    assert catalog_body["nodeTypes"] == profile_bundle_body["nodeTypes"]

    monkeypatch.setattr(graphapi_module, "layout_with_elkjs", lambda payload, *, mode, node_cmd: payload)

    render_response = client.post(
        "/render/svg",
        params={"profile_id": "runtime", "theme_id": "night"},
        json={
            "nodes": [
                {"name": "A", "type": "router"},
                {"name": "B", "type": "switch"},
            ],
            "links": [{"from": "A", "to": "B", "type": "directed"}],
        },
    )
    assert render_response.status_code == 200
    assert render_response.headers["x-graphapi-profile-checksum"] == profile_bundle_body["checksum"]
    assert render_response.headers["x-graphapi-theme-checksum"] == theme_bundle_body["checksum"]
    assert render_response.headers["x-graphapi-runtime-checksum"]
    assert 'class="node router"' in render_response.text
    assert 'class="edge directed"' in render_response.text
    assert "fill: #334455" in render_response.text


def test_openapi_includes_profile_and_theme_contract_endpoints(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    openapi = response.json()
    paths = openapi["paths"]
    assert "/v1/profiles" in paths
    assert "/v1/profiles/{id}" in paths
    assert "/v1/profiles/{id}/bundle" in paths
    assert "/v1/profiles/{id}/publish" in paths
    assert "/v1/themes" in paths
    assert "/v1/themes/{id}" in paths
    assert "/v1/themes/{id}/bundle" in paths
    assert "/v1/themes/{id}/publish" in paths
    assert "/v1/autocomplete/catalog" in paths
    assert "/v2/iconsets" in paths
    assert "/v2/iconsets/{id}" in paths
    assert "/v2/iconsets/{id}/bundle" in paths
    assert "/v2/iconsets/{id}/publish" in paths
    assert "/v2/iconsets/resolve" in paths
    assert "/v2/profiles" in paths
    assert "/v2/profiles/{id}" in paths
    assert "/v2/profiles/{id}/bundle" in paths
    assert "/v2/profiles/{id}/publish" in paths
    assert "/v2/profiles/{id}/iconset-resolution" in paths
    assert "/v2/autocomplete/catalog" in paths

    schemas = openapi["components"]["schemas"]
    assert "ProfileBundleV1" in schemas
    assert "ThemeBundleV1" in schemas
    assert "ProfileCreateRequestV1" in schemas
    assert "ThemeCreateRequestV1" in schemas
    assert "AutocompleteCatalogResponseV1" in schemas
    assert "IconsetBundleV1" in schemas
    assert "ProfileBundleV2" in schemas
    assert "AutocompleteCatalogResponseV2" in schemas


def test_iconsets_crud_and_resolve_flow(client: TestClient) -> None:
    create_a = client.post(
        "/v2/iconsets",
        json={
            "iconsetId": "team-a",
            "name": "Team A",
            "entries": {
                "router": "mdi:router",
                "switch": "mdi:switch",
            },
        },
    )
    assert create_a.status_code == 201
    assert create_a.json()["draft"]["iconsetVersion"] == 1

    create_b = client.post(
        "/v2/iconsets",
        json={
            "iconsetId": "team-b",
            "name": "Team B",
            "entries": {
                "router": "material-symbols:router-outline",
                "gateway": "mdi:gate",
            },
        },
    )
    assert create_b.status_code == 201

    assert client.post("/v2/iconsets/team-a/publish").status_code == 200
    assert client.post("/v2/iconsets/team-b/publish").status_code == 200

    conflict = client.post(
        "/v2/iconsets/resolve",
        json={
            "iconsetRefs": [
                {"iconsetId": "team-a", "stage": "published", "iconsetVersion": 1},
                {"iconsetId": "team-b", "stage": "published", "iconsetVersion": 1},
            ],
            "conflictPolicy": "reject",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "ICONSET_KEY_CONFLICT"

    last_wins = client.post(
        "/v2/iconsets/resolve",
        json={
            "iconsetRefs": [
                {"iconsetId": "team-a", "stage": "published", "iconsetVersion": 1},
                {"iconsetId": "team-b", "stage": "published", "iconsetVersion": 1},
            ],
            "conflictPolicy": "last-wins",
        },
    )
    assert last_wins.status_code == 200
    body = last_wins.json()
    assert body["resolvedEntries"]["router"] == "material-symbols:router-outline"
    assert body["resolvedEntries"]["switch"] == "mdi:switch"
    assert body["resolvedEntries"]["gateway"] == "mdi:gate"
    assert body["checksum"]


def test_profile_v2_catalog_resolution_and_render_headers(client: TestClient, monkeypatch) -> None:
    # Create and publish iconset that will drive node type resolution.
    created_iconset = client.post(
        "/v2/iconsets",
        json={
            "iconsetId": "telecom",
            "name": "Telecom",
            "entries": {
                "router": "mdi:router",
                "gateway": "mdi:gate",
                "firewall": "mdi:shield",
            },
        },
    )
    assert created_iconset.status_code == 201
    assert client.post("/v2/iconsets/telecom/publish").status_code == 200

    default_bundle = client.get(
        "/v2/profiles/default/bundle",
        params={"stage": "draft"},
    )
    assert default_bundle.status_code == 200
    elk_settings = default_bundle.json()["elkSettings"]

    create_profile = client.post(
        "/v2/profiles",
        json={
            "profileId": "telecom-profile",
            "name": "Telecom Profile",
            "linkTypes": ["directed", "dependency"],
            "elkSettings": elk_settings,
            "iconsetRefs": [
                {"iconsetId": "telecom", "iconsetVersion": 1},
            ],
            "iconConflictPolicy": "reject",
        },
    )
    assert create_profile.status_code == 201
    assert create_profile.json()["draft"]["profileVersion"] == 1

    published = client.post("/v2/profiles/telecom-profile/publish")
    assert published.status_code == 200
    published_body = published.json()
    assert published_body["nodeTypes"] == ["firewall", "gateway", "router"]
    assert published_body["typeIconMap"]["gateway"] == "mdi:gate"
    assert published_body["iconsetResolutionChecksum"]

    catalog = client.get(
        "/v2/autocomplete/catalog",
        params={"profile_id": "telecom-profile", "stage": "published"},
    )
    assert catalog.status_code == 200
    catalog_body = catalog.json()
    assert catalog_body["profileVersion"] == 1
    assert catalog_body["nodeTypes"] == ["firewall", "gateway", "router"]
    assert catalog_body["linkTypes"] == ["directed", "dependency"]
    assert catalog_body["iconsetResolutionChecksum"] == published_body["iconsetResolutionChecksum"]

    resolution = client.get(
        "/v2/profiles/telecom-profile/iconset-resolution",
        params={"stage": "published"},
    )
    assert resolution.status_code == 200
    resolution_body = resolution.json()
    assert resolution_body["resolvedEntries"]["router"] == "mdi:router"
    assert resolution_body["checksum"] == published_body["iconsetResolutionChecksum"]
    assert resolution_body["sources"] == [
        {
            "iconsetId": "telecom",
            "iconsetVersion": 1,
            "checksum": resolution_body["sources"][0]["checksum"],
        }
    ]

    theme_payload = _new_theme_payload("night-v2")
    assert client.post("/v1/themes", json=theme_payload).status_code == 201
    assert client.post("/v1/themes/night-v2/publish").status_code == 200

    monkeypatch.setattr(graphapi_module, "layout_with_elkjs", lambda payload, *, mode, node_cmd: payload)

    render_response = client.post(
        "/render/svg",
        params={"profile_id": "telecom-profile", "theme_id": "night-v2"},
        json={
            "nodes": [
                {"name": "A", "type": "router"},
                {"name": "B", "type": "gateway"},
            ],
            "links": [{"from": "A", "to": "B", "type": "directed"}],
        },
    )
    assert render_response.status_code == 200
    assert render_response.headers["x-graphapi-profile-id"] == "telecom-profile"
    assert render_response.headers["x-graphapi-iconset-resolution-checksum"] == published_body["iconsetResolutionChecksum"]
    assert render_response.headers["x-graphapi-iconset-sources"] == "telecom@1"
    assert render_response.headers["x-graphapi-runtime-checksum"]
