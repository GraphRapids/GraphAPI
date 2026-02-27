import importlib

from fastapi.testclient import TestClient

graphapi_module = importlib.import_module("graphapi.app")

client = TestClient(graphapi_module.app)


def test_render_svg_from_json(monkeypatch):
    called = {"layout": False}

    def fake_layout(payload, *, mode, node_cmd):
        called["layout"] = True
        return payload

    monkeypatch.setattr(graphapi_module, "layout_with_elkjs", fake_layout)

    payload = {
        "nodes": ["A", "B"],
        "links": ["A:eth0 -> B:eth1"],
    }

    response = client.post("/render/svg", json=payload)

    assert called["layout"] is True
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in response.text


def test_render_svg_accepts_graph_type_id_query(monkeypatch):
    called = {"graph_type_bundle": None, "theme_bundle": None}

    def fake_render(graph, *, graph_type_bundle=None, theme_bundle=None):
        called["graph_type_bundle"] = graph_type_bundle
        called["theme_bundle"] = theme_bundle
        return "<svg/>"

    monkeypatch.setattr(graphapi_module, "render_svg_from_graph", fake_render)

    response = client.post("/render/svg?graph_type_id=default", json={"nodes": ["A"]})

    assert response.status_code == 200
    assert called["graph_type_bundle"] is not None
    assert called["theme_bundle"] is None


def test_render_svg_accepts_theme_id_query(monkeypatch):
    called = {"graph_type_bundle": None, "theme_bundle": None}

    def fake_render(graph, *, graph_type_bundle=None, theme_bundle=None):
        called["graph_type_bundle"] = graph_type_bundle
        called["theme_bundle"] = theme_bundle
        return "<svg/>"

    monkeypatch.setattr(graphapi_module, "render_svg_from_graph", fake_render)

    response = client.post("/render/svg?theme_id=default", json={"nodes": ["A"]})

    assert response.status_code == 200
    assert called["theme_bundle"] is not None


def test_graphloom_schema_endpoint():
    response = client.get("/schemas/minimal-input.schema.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["type"] == "object"


def test_validate_endpoint():
    payload = {
        "nodes": ["A", "B"],
        "links": ["A:eth0 -> B:eth1"],
    }

    response = client.post("/validate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["normalized"]["nodes"]


def test_request_size_limit():
    large_value = "x" * (1024 * 1024 + 200)
    payload = {"nodes": [large_value]}

    response = client.post("/render/svg", json=payload)

    assert response.status_code == 413


def test_request_size_limit_without_content_length(monkeypatch):
    monkeypatch.setattr(graphapi_module, "MAX_REQUEST_BYTES", 128)

    def chunked_body():
        yield b'{"oversized":"'
        yield b"x" * 512
        yield b'"}'

    response = client.post(
        "/render/svg",
        content=chunked_body(),
        headers={"content-type": "application/json"},
    )

    assert response.request.headers.get("content-length") is None
    assert response.request.headers.get("transfer-encoding") == "chunked"
    assert response.status_code == 413


def test_cors_preflight_blocks_unknown_origin_by_default():
    response = client.options(
        "/render/svg",
        headers={
            "origin": "https://evil.example",
            "access-control-request-method": "POST",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_allows_local_origin_without_credentials_by_default():
    response = client.options(
        "/render/svg",
        headers={
            "origin": "http://localhost:9000",
            "access-control-request-method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:9000"
    assert response.headers.get("access-control-allow-credentials") != "true"


def test_request_timeout(monkeypatch):
    def slow_render(_graph, *, graph_type_bundle=None, theme_bundle=None):
        import time

        time.sleep(0.05)
        return "<svg/>"

    monkeypatch.setattr(graphapi_module, "REQUEST_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(graphapi_module, "render_svg_from_graph", slow_render)

    response = client.post("/render/svg", json={"nodes": ["A"]})

    assert response.status_code == 504
