from __future__ import annotations

import importlib


def test_main_uses_default_host_and_port(monkeypatch) -> None:
    main_module = importlib.import_module("graphapi.__main__")

    called: dict[str, object] = {}

    def fake_run(app_path, *, host, port):
        called["app_path"] = app_path
        called["host"] = host
        called["port"] = port

    monkeypatch.delenv("GRAPHAPI_HOST", raising=False)
    monkeypatch.delenv("GRAPHAPI_PORT", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)

    rc = main_module.main()

    assert rc == 0
    assert called == {"app_path": "graphapi.app:app", "host": "0.0.0.0", "port": 8000}


def test_main_prefers_explicit_graphapi_port(monkeypatch) -> None:
    main_module = importlib.import_module("graphapi.__main__")

    called: dict[str, object] = {}

    def fake_run(app_path, *, host, port):
        called["app_path"] = app_path
        called["host"] = host
        called["port"] = port

    monkeypatch.setenv("GRAPHAPI_HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("GRAPHAPI_PORT", "5050")
    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)

    rc = main_module.main()

    assert rc == 0
    assert called == {"app_path": "graphapi.app:app", "host": "127.0.0.1", "port": 5050}
