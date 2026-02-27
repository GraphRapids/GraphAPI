from __future__ import annotations

import json
import sqlite3

from graphapi.graphtype_defaults import default_graph_type_create_request
from graphapi.graphtype_store import GraphTypeStore
from graphapi.iconset_defaults import default_iconset_create_request
from graphapi.iconset_store import IconsetStore
from graphapi.layoutset_defaults import default_layout_set_create_request
from graphapi.layoutset_store import LayoutSetStore
from graphapi.linkset_defaults import default_link_set_create_request
from graphapi.linkset_store import LinkSetStore


def _column_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _bootstrap_runtime_store(db_path):
    iconset_store = IconsetStore(db_path)
    iconset_store.ensure_default_iconset(default_iconset_create_request())

    layout_set_store = LayoutSetStore(db_path)
    layout_set_store.ensure_default_layout_set(default_layout_set_create_request())

    link_set_store = LinkSetStore(db_path)
    link_set_store.ensure_default_link_set(default_link_set_create_request())

    graph_type_store = GraphTypeStore(db_path, iconset_store, layout_set_store, link_set_store)
    graph_type_store.ensure_default_graph_type(default_graph_type_create_request())

    return iconset_store, layout_set_store, link_set_store, graph_type_store


def test_iconset_schema_survives_extra_columns_without_dropping_data(tmp_path) -> None:
    runtime_db_path = tmp_path / "runtime.v1.sqlite3"
    iconset_store = IconsetStore(runtime_db_path)
    iconset_store.ensure_default_iconset(default_iconset_create_request())

    with sqlite3.connect(runtime_db_path) as conn:
        conn.execute("ALTER TABLE icon_sets ADD COLUMN legacy_note TEXT DEFAULT ''")
        conn.execute("UPDATE icon_sets SET legacy_note = 'keep' WHERE icon_set_id = 'default'")

    migrated_store = IconsetStore(runtime_db_path)
    record = migrated_store.get_iconset("default")
    assert record.draft.iconSetId == "default"

    with sqlite3.connect(runtime_db_path) as conn:
        assert "legacy_note" in _column_names(conn, "icon_sets")
        preserved = conn.execute(
            "SELECT legacy_note FROM icon_sets WHERE icon_set_id = 'default'"
        ).fetchone()
        assert preserved is not None
        assert str(preserved[0]) == "keep"


def test_graphtype_schema_migration_adds_missing_columns_without_data_loss(tmp_path) -> None:
    runtime_db_path = tmp_path / "runtime.v1.sqlite3"
    iconset_store, layout_set_store, link_set_store, graph_type_store = _bootstrap_runtime_store(runtime_db_path)
    published_bundle = graph_type_store.get_bundle("default", stage="published")

    with sqlite3.connect(runtime_db_path) as conn:
        conn.row_factory = sqlite3.Row
        graph_type_rows = conn.execute(
            """
            SELECT
                graph_type_id,
                name,
                draft_version,
                draft_updated_at,
                draft_checksum,
                draft_runtime_checksum,
                draft_payload
            FROM graph_types
            """
        ).fetchall()
        published_rows = conn.execute(
            """
            SELECT
                graph_type_id,
                graph_type_version,
                updated_at,
                checksum,
                runtime_checksum,
                payload
            FROM graph_type_published_versions
            """
        ).fetchall()

        conn.executescript(
            """
            DROP TABLE graph_type_published_versions;
            DROP TABLE graph_types;

            CREATE TABLE graph_types (
                graph_type_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                draft_version INTEGER NOT NULL,
                draft_updated_at TEXT NOT NULL,
                draft_checksum TEXT NOT NULL,
                draft_runtime_checksum TEXT NOT NULL,
                draft_payload TEXT NOT NULL
            );

            CREATE TABLE graph_type_published_versions (
                graph_type_id TEXT NOT NULL,
                graph_type_version INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                checksum TEXT NOT NULL,
                runtime_checksum TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (graph_type_id, graph_type_version),
                FOREIGN KEY (graph_type_id) REFERENCES graph_types(graph_type_id) ON DELETE CASCADE
            );
            """
        )

        conn.executemany(
            """
            INSERT INTO graph_types (
                graph_type_id,
                name,
                draft_version,
                draft_updated_at,
                draft_checksum,
                draft_runtime_checksum,
                draft_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(row["graph_type_id"]),
                    str(row["name"]),
                    int(row["draft_version"]),
                    str(row["draft_updated_at"]),
                    str(row["draft_checksum"]),
                    str(row["draft_runtime_checksum"]),
                    str(row["draft_payload"]),
                )
                for row in graph_type_rows
            ],
        )
        conn.executemany(
            """
            INSERT INTO graph_type_published_versions (
                graph_type_id,
                graph_type_version,
                updated_at,
                checksum,
                runtime_checksum,
                payload
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(row["graph_type_id"]),
                    int(row["graph_type_version"]),
                    str(row["updated_at"]),
                    str(row["checksum"]),
                    str(row["runtime_checksum"]),
                    str(row["payload"]),
                )
                for row in published_rows
            ],
        )

    migrated_store = GraphTypeStore(runtime_db_path, iconset_store, layout_set_store, link_set_store)
    graph_types = migrated_store.list_graph_types().graphTypes
    assert len(graph_types) == 1
    assert graph_types[0].iconSetResolutionChecksum == published_bundle.iconSetResolutionChecksum

    restored_bundle = migrated_store.get_bundle("default", stage="published")
    assert restored_bundle.checksum == published_bundle.checksum
    assert restored_bundle.iconSetResolutionChecksum == published_bundle.iconSetResolutionChecksum

    with sqlite3.connect(runtime_db_path) as conn:
        assert "draft_icon_set_resolution_checksum" in _column_names(conn, "graph_types")
        assert "icon_set_resolution_checksum" in _column_names(conn, "graph_type_published_versions")


def test_layoutset_payload_schema_migrates_without_data_loss(tmp_path) -> None:
    source_runtime_db_path = tmp_path / "runtime.source.v1.sqlite3"
    _, source_layout_set_store, _, _ = _bootstrap_runtime_store(source_runtime_db_path)
    draft_bundle = source_layout_set_store.get_bundle("default", stage="draft")
    published_bundle = source_layout_set_store.get_bundle("default", stage="published")

    legacy_runtime_db_path = tmp_path / "runtime.legacy.v1.sqlite3"
    with sqlite3.connect(legacy_runtime_db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE layout_sets (
                layout_set_id TEXT PRIMARY KEY,
                draft_version INTEGER NOT NULL,
                draft_updated_at TEXT NOT NULL,
                draft_checksum TEXT NOT NULL,
                draft_payload TEXT NOT NULL
            );

            CREATE TABLE layout_set_published_versions (
                layout_set_id TEXT NOT NULL,
                layout_set_version INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                checksum TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (layout_set_id, layout_set_version)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO layout_sets (
                layout_set_id,
                draft_version,
                draft_updated_at,
                draft_checksum,
                draft_payload
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                draft_bundle.layoutSetId,
                draft_bundle.layoutSetVersion,
                draft_bundle.updatedAt.isoformat(),
                draft_bundle.checksum,
                json.dumps(draft_bundle.model_dump(mode="json")),
            ),
        )
        conn.execute(
            """
            INSERT INTO layout_set_published_versions (
                layout_set_id,
                layout_set_version,
                updated_at,
                checksum,
                payload
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                published_bundle.layoutSetId,
                published_bundle.layoutSetVersion,
                published_bundle.updatedAt.isoformat(),
                published_bundle.checksum,
                json.dumps(published_bundle.model_dump(mode="json")),
            ),
        )

    migrated_store = LayoutSetStore(legacy_runtime_db_path)
    record = migrated_store.get_layout_set("default")
    assert record.draft.layoutSetVersion == draft_bundle.layoutSetVersion
    assert record.draft.checksum == draft_bundle.checksum
    assert len(record.publishedVersions) == 1
    assert record.publishedVersions[0].checksum == published_bundle.checksum

    with sqlite3.connect(legacy_runtime_db_path) as conn:
        layout_columns = _column_names(conn, "layout_sets")
        published_columns = _column_names(conn, "layout_set_published_versions")
        assert "draft_payload" not in layout_columns
        assert "payload" not in published_columns
        draft_entry_count = conn.execute(
            "SELECT COUNT(*) FROM layout_set_draft_entries WHERE layout_set_id = 'default'"
        ).fetchone()
        published_entry_count = conn.execute(
            "SELECT COUNT(*) FROM layout_set_published_entries WHERE layout_set_id = 'default'"
        ).fetchone()
        assert draft_entry_count is not None
        assert int(draft_entry_count[0]) > 0
        assert published_entry_count is not None
        assert int(published_entry_count[0]) > 0
