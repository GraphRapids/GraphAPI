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


def test_render_svg_accepts_theme_id_query(monkeypatch):
    called = {"theme_id": None}

    def fake_render(graph, *, theme_id="default"):
        called["theme_id"] = theme_id
        return "<svg/>"

    monkeypatch.setattr(graphapi_module, "render_svg_from_graph", fake_render)

    response = client.post("/render/svg?theme_id=default", json={"nodes": ["A"]})

    assert response.status_code == 200
    assert called["theme_id"] == "default"


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


def test_request_timeout(monkeypatch):
    def slow_render(_graph, *, theme_id="default"):
        import time

        time.sleep(0.05)
        return "<svg/>"

    monkeypatch.setattr(graphapi_module, "REQUEST_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(graphapi_module, "render_svg_from_graph", slow_render)

    response = client.post("/render/svg", json={"nodes": ["A"]})

    assert response.status_code == 504
