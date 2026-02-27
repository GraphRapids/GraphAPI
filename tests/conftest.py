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
