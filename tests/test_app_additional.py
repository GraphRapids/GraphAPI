from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from graphapi.iconset_store import IconsetStoreError


graphapi_module = importlib.import_module("graphapi.app")


def test_env_bool_and_cors_config_guard(monkeypatch) -> None:
    monkeypatch.setenv("GRAPHAPI_CORS_ALLOW_CREDENTIALS", "yes")
    assert graphapi_module._env_bool("GRAPHAPI_CORS_ALLOW_CREDENTIALS", default=False) is True

    monkeypatch.setenv("GRAPHAPI_CORS_ORIGINS", "")
    origins, allow_credentials = graphapi_module._cors_config()
    assert origins
    assert allow_credentials is True

    monkeypatch.setenv("GRAPHAPI_CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="requires explicit non-wildcard origins"):
        graphapi_module._cors_config()


def test_default_resources_have_list_get_bundle_routes(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200

    iconsets = client.get("/v1/icon-sets")
    assert iconsets.status_code == 200
    assert any(item["iconSetId"] == "default" for item in iconsets.json()["iconSets"])

    assert client.get("/v1/icon-sets/default").status_code == 200
    assert client.get("/v1/icon-sets/default/bundle", params={"stage": "draft"}).status_code == 200
    assert client.get("/v1/icon-sets/default/bundle", params={"stage": "published"}).status_code == 200
    assert client.get("/v1/icon-sets/default/entries", params={"stage": "draft"}).status_code == 200

    layouts = client.get("/v1/layout-sets")
    assert layouts.status_code == 200
    assert any(item["layoutSetId"] == "default" for item in layouts.json()["layoutSets"])
    assert client.get("/v1/layout-sets/default").status_code == 200
    assert client.get("/v1/layout-sets/default/bundle", params={"stage": "draft"}).status_code == 200
    assert client.get("/v1/layout-sets/default/entries", params={"stage": "draft"}).status_code == 200

    links = client.get("/v1/link-sets")
    assert links.status_code == 200
    assert any(item["linkSetId"] == "default" for item in links.json()["linkSets"])
    assert client.get("/v1/link-sets/default").status_code == 200
    assert client.get("/v1/link-sets/default/bundle", params={"stage": "draft"}).status_code == 200
    assert client.get("/v1/link-sets/default/entries", params={"stage": "draft"}).status_code == 200

    graph_types = client.get("/v1/graph-types")
    assert graph_types.status_code == 200
    assert any(item["graphTypeId"] == "default" for item in graph_types.json()["graphTypes"])
    assert client.get("/v1/graph-types/default").status_code == 200
    assert client.get("/v1/graph-types/default/bundle", params={"stage": "draft"}).status_code == 200

    themes = client.get("/v1/themes")
    assert themes.status_code == 200
    assert any(item["themeId"] == "default" for item in themes.json()["themes"])
    assert client.get("/v1/themes/default").status_code == 200
    assert client.get("/v1/themes/default/bundle", params={"stage": "draft"}).status_code == 200
    assert client.get("/v1/themes/default/variables", params={"stage": "draft"}).status_code == 200

    validate = client.post("/validate", json={"nodes": ["A"]})
    assert validate.status_code == 200
    assert validate.json()["valid"] is True


def test_error_mapping_for_missing_resources_and_render_refs(client: TestClient) -> None:
    assert client.get("/v1/icon-sets/missing").status_code == 404
    assert client.get("/v1/layout-sets/missing").status_code == 404
    assert client.get("/v1/link-sets/missing").status_code == 404
    assert client.get("/v1/graph-types/missing").status_code == 404
    assert client.get("/v1/themes/missing").status_code == 404

    runtime = client.get("/v1/graph-types/missing/runtime")
    assert runtime.status_code == 404
    assert runtime.json()["detail"]["code"] == "GRAPH_TYPE_NOT_FOUND"

    render_missing_graph_type = client.post(
        "/render/svg",
        params={"graph_type_id": "missing"},
        json={"nodes": ["A"]},
    )
    assert render_missing_graph_type.status_code == 404
    assert render_missing_graph_type.json()["detail"]["code"] == "GRAPH_TYPE_NOT_FOUND"

    render_missing_theme = client.post(
        "/render/svg",
        params={"theme_id": "missing"},
        json={"nodes": ["A"]},
    )
    assert render_missing_theme.status_code == 404
    assert render_missing_theme.json()["detail"]["code"] == "THEME_NOT_FOUND"


def test_iconset_resolve_covers_first_wins_and_empty_resolution(client: TestClient, monkeypatch) -> None:
    first = client.post(
        "/v1/icon-sets",
        json={
            "iconSetId": "first",
            "name": "First",
            "entries": {"router": "mdi:router"},
        },
    )
    assert first.status_code == 201
    assert client.post("/v1/icon-sets/first/publish").status_code == 200

    second = client.post(
        "/v1/icon-sets",
        json={
            "iconSetId": "second",
            "name": "Second",
            "entries": {"router": "mdi:router-wireless", "gateway": "mdi:gate"},
        },
    )
    assert second.status_code == 201
    assert client.post("/v1/icon-sets/second/publish").status_code == 200

    first_wins = client.post(
        "/v1/icon-sets/resolve",
        json={
            "iconSetRefs": [
                {"iconSetId": "first", "stage": "published", "iconSetVersion": 1},
                {"iconSetId": "second", "stage": "published", "iconSetVersion": 1},
            ],
            "conflictPolicy": "first-wins",
        },
    )
    assert first_wins.status_code == 200
    assert first_wins.json()["resolvedEntries"]["router"] == "mdi:router"

    class _EmptyBundleStore:
        def get_bundle(self, _id, *, stage, icon_set_version):
            class _Bundle:
                iconSetId = "empty"
                iconSetVersion = 1
                checksum = "a" * 64
                entries = {}

            return _Bundle()

    monkeypatch.setattr(graphapi_module, "iconset_store", _EmptyBundleStore())
    empty_response = client.post(
        "/v1/icon-sets/resolve",
        json={
            "iconSetRefs": [{"iconSetId": "empty", "stage": "published", "iconSetVersion": 1}],
            "conflictPolicy": "reject",
        },
    )
    assert empty_response.status_code == 400
    assert empty_response.json()["detail"]["code"] == "GRAPH_TYPE_ICONSET_REF_INVALID"


def test_resolve_iconset_store_errors_are_mapped(client: TestClient, monkeypatch) -> None:
    class _FailingStore:
        def get_bundle(self, _id, *, stage, icon_set_version):
            raise IconsetStoreError(status_code=500, code="ICONSET_FAILURE", message="boom")

    monkeypatch.setattr(graphapi_module, "iconset_store", _FailingStore())

    response = client.post(
        "/v1/icon-sets/resolve",
        json={
            "iconSetRefs": [{"iconSetId": "default", "stage": "published", "iconSetVersion": 1}],
            "conflictPolicy": "reject",
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "ICONSET_FAILURE"
