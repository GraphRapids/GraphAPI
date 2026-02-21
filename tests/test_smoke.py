import importlib

from fastapi.testclient import TestClient

graphapi_module = importlib.import_module("graphapi.app")

client = TestClient(graphapi_module.app)


def test_render_svg_from_yaml(monkeypatch):
    called = {"layout": False}

    def fake_layout(payload, *, mode, node_cmd):
        called["layout"] = True
        return payload

    monkeypatch.setattr(graphapi_module, "layout_with_elkjs", fake_layout)

    payload = {
        "yaml": "nodes:\n  - A\n  - B\nlinks:\n  - A:eth0 -> B:eth1\n",
    }

    response = client.post("/render/svg", json=payload)

    assert called["layout"] is True
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in response.text
