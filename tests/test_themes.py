from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

graphapi_module = importlib.import_module("graphapi.app")
client = TestClient(graphapi_module.app)


class _Theme:
    def __init__(self, theme_id: str, version: str) -> None:
        self.id = theme_id
        self.version = version
        self.display_name = "Theme"
        self.description = "Desc"


def test_list_themes_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        graphapi_module,
        "_graphtheme_api",
        lambda: (
            lambda: [_Theme("default", "0.1.0")],
            lambda _theme_id: {},
            lambda _theme_id: "",
            lambda _theme_id: {},
        ),
    )

    response = client.get("/themes")

    assert response.status_code == 200
    assert response.json()["themes"][0]["id"] == "default"


def test_get_theme_endpoint_returns_404_for_unknown_theme(monkeypatch) -> None:
    def fake_meta(_theme_id: str):
        raise ValueError("Unknown theme_id")

    monkeypatch.setattr(
        graphapi_module,
        "_graphtheme_api",
        lambda: (
            lambda: [],
            fake_meta,
            lambda _theme_id: "",
            lambda _theme_id: {},
        ),
    )

    response = client.get("/themes/missing")
    assert response.status_code == 404


def test_get_theme_css_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        graphapi_module,
        "_graphtheme_api",
        lambda: (
            lambda: [],
            lambda _theme_id: {},
            lambda _theme_id: "svg { color: red; }",
            lambda _theme_id: {},
        ),
    )

    response = client.get("/themes/default/css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert "color: red" in response.text


def test_theme_endpoints_return_503_when_graphtheme_unavailable(monkeypatch) -> None:
    def fail():
        raise RuntimeError("GraphTheme package is required for theme API endpoints.")

    monkeypatch.setattr(graphapi_module, "_graphtheme_api", fail)

    assert client.get("/themes").status_code == 503
    assert client.get("/themes/default").status_code == 503
    assert client.get("/themes/default/css").status_code == 503
    assert client.get("/themes/default/metrics").status_code == 503


def test_get_theme_metrics_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        graphapi_module,
        "_graphtheme_api",
        lambda: (
            lambda: [],
            lambda _theme_id: {},
            lambda _theme_id: "",
            lambda _theme_id: {"font_size_px": 16},
        ),
    )

    response = client.get("/themes/default/metrics")

    assert response.status_code == 200
    assert response.json()["font_size_px"] == 16
