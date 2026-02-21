from fastapi.testclient import TestClient

from graphapi.app import app

client = TestClient(app)


def test_render_svg_from_yaml():
    payload = {
        "yaml": "nodes:\n  - A\n  - B\nlinks:\n  - A:eth0 -> B:eth1\n",
        "layout": False,
    }

    response = client.post("/render/svg", json=payload)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in response.text
