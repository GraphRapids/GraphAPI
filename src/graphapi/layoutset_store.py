from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from graphloom import ElkSettings
from pydantic import ValidationError

from .graph_type_contract import (
    LayoutSetBundleV1,
    LayoutSetCreateRequestV1,
    LayoutSetEditableFieldsV1,
    LayoutSetEntryUpsertRequestV1,
    LayoutSetListResponseV1,
    LayoutSetRecordV1,
    LayoutSetSummaryV1,
    LayoutSetUpdateRequestV1,
    compute_layout_set_checksum,
    normalize_layout_setting_key,
    utcnow,
)


class LayoutSetStoreError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class LayoutSetStore:
    def __init__(self, storage_path: Path) -> None:
        self._storage_path = storage_path
        self._lock = RLock()
        self._schema_ready = False

    @classmethod
    def from_env(cls) -> "LayoutSetStore":
        raw = os.getenv("GRAPHAPI_RUNTIME_DB_PATH", "").strip()
        if not raw:
            raw = os.getenv("GRAPHAPI_LAYOUT_SET_STORE_PATH", "").strip()
        if raw:
            return cls(Path(raw).expanduser())
        return cls(Path.home() / ".cache" / "graphapi" / "runtime.v1.sqlite3")

    def ensure_default_layout_set(self, request: LayoutSetCreateRequestV1) -> None:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT 1 FROM layout_sets WHERE layout_set_id = ?",
                    (request.layoutSetId,),
                ).fetchone()
                if row is not None:
                    return

                bundle = self._build_bundle(
                    layout_set_id=request.layoutSetId,
                    layout_set_version=1,
                    editable=request,
                )
                self._insert_layout_set(conn, bundle, publish=True)

    def list_layout_sets(self) -> LayoutSetListResponseV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                rows = conn.execute(
                    """
                    SELECT
                        s.layout_set_id,
                        s.name,
                        s.draft_version,
                        s.draft_updated_at,
                        s.draft_checksum,
                        (
                            SELECT MAX(v.layout_set_version)
                            FROM layout_set_published_versions v
                            WHERE v.layout_set_id = s.layout_set_id
                        ) AS published_version
                    FROM layout_sets s
                    ORDER BY s.layout_set_id ASC
                    """
                ).fetchall()

                return LayoutSetListResponseV1(
                    layoutSets=[
                        LayoutSetSummaryV1(
                            layoutSetId=str(row["layout_set_id"]),
                            name=str(row["name"]),
                            draftVersion=int(row["draft_version"]),
                            publishedVersion=(
                                int(row["published_version"]) if row["published_version"] is not None else None
                            ),
                            updatedAt=datetime.fromisoformat(str(row["draft_updated_at"])),
                            checksum=str(row["draft_checksum"]),
                        )
                        for row in rows
                    ]
                )

    def get_layout_set(self, layout_set_id: str) -> LayoutSetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, layout_set_id)

                published_rows = conn.execute(
                    """
                    SELECT layout_set_version, name, updated_at, checksum
                    FROM layout_set_published_versions
                    WHERE layout_set_id = ?
                    ORDER BY layout_set_version ASC
                    """,
                    (layout_set_id,),
                ).fetchall()

                published: list[LayoutSetBundleV1] = []
                for row in published_rows:
                    version = int(row["layout_set_version"])
                    entries = self._load_published_entries(conn, layout_set_id, version)
                    published.append(
                        self._bundle_from_parts(
                            layout_set_id=layout_set_id,
                            layout_set_version=version,
                            name=str(row["name"]),
                            updated_at=datetime.fromisoformat(str(row["updated_at"])),
                            checksum=str(row["checksum"]),
                            entries=entries,
                        )
                    )

                return LayoutSetRecordV1(
                    layoutSetId=layout_set_id,
                    draft=draft,
                    publishedVersions=published,
                )

    def create_layout_set(self, request: LayoutSetCreateRequestV1) -> LayoutSetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                exists = conn.execute(
                    "SELECT 1 FROM layout_sets WHERE layout_set_id = ?",
                    (request.layoutSetId,),
                ).fetchone()
                if exists is not None:
                    raise LayoutSetStoreError(
                        status_code=409,
                        code="LAYOUT_SET_ALREADY_EXISTS",
                        message=f"Layout set '{request.layoutSetId}' already exists.",
                    )

                bundle = self._build_bundle(
                    layout_set_id=request.layoutSetId,
                    layout_set_version=1,
                    editable=request,
                )
                self._insert_layout_set(conn, bundle, publish=False)

        return self.get_layout_set(request.layoutSetId)

    def update_layout_set(self, layout_set_id: str, request: LayoutSetUpdateRequestV1) -> LayoutSetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT draft_version FROM layout_sets WHERE layout_set_id = ?",
                    (layout_set_id,),
                ).fetchone()
                if row is None:
                    raise LayoutSetStoreError(
                        status_code=404,
                        code="LAYOUT_SET_NOT_FOUND",
                        message=f"Layout set '{layout_set_id}' was not found.",
                    )

                next_version = int(row["draft_version"]) + 1
                bundle = self._build_bundle(
                    layout_set_id=layout_set_id,
                    layout_set_version=next_version,
                    editable=request,
                )
                self._replace_draft(conn, bundle)

        return self.get_layout_set(layout_set_id)

    def delete_layout_set(self, layout_set_id: str) -> None:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                result = conn.execute(
                    "DELETE FROM layout_sets WHERE layout_set_id = ?",
                    (layout_set_id,),
                )
                if int(result.rowcount or 0) < 1:
                    raise LayoutSetStoreError(
                        status_code=404,
                        code="LAYOUT_SET_NOT_FOUND",
                        message=f"Layout set '{layout_set_id}' was not found.",
                    )

    def upsert_layout_set_entry(
        self,
        layout_set_id: str,
        setting_key: str,
        request: LayoutSetEntryUpsertRequestV1,
    ) -> LayoutSetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                base = conn.execute(
                    "SELECT name, draft_version FROM layout_sets WHERE layout_set_id = ?",
                    (layout_set_id,),
                ).fetchone()
                if base is None:
                    raise LayoutSetStoreError(
                        status_code=404,
                        code="LAYOUT_SET_NOT_FOUND",
                        message=f"Layout set '{layout_set_id}' was not found.",
                    )

                entries = self._load_draft_entries(conn, layout_set_id)
                entries[normalize_layout_setting_key(setting_key)] = request.value

                editable = LayoutSetEditableFieldsV1.model_validate(
                    {
                        "name": str(base["name"]),
                        "elkSettings": entries,
                    }
                )
                bundle = self._build_bundle(
                    layout_set_id=layout_set_id,
                    layout_set_version=int(base["draft_version"]) + 1,
                    editable=editable,
                )
                self._replace_draft(conn, bundle)

        return self.get_layout_set(layout_set_id)

    def delete_layout_set_entry(self, layout_set_id: str, setting_key: str) -> LayoutSetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                base = conn.execute(
                    "SELECT name, draft_version FROM layout_sets WHERE layout_set_id = ?",
                    (layout_set_id,),
                ).fetchone()
                if base is None:
                    raise LayoutSetStoreError(
                        status_code=404,
                        code="LAYOUT_SET_NOT_FOUND",
                        message=f"Layout set '{layout_set_id}' was not found.",
                    )

                entries = self._load_draft_entries(conn, layout_set_id)
                normalized_key = normalize_layout_setting_key(setting_key)
                if normalized_key not in entries:
                    raise LayoutSetStoreError(
                        status_code=404,
                        code="LAYOUT_SET_ENTRY_NOT_FOUND",
                        message=(
                            f"Layout setting key '{normalized_key}' was not found in layout set '{layout_set_id}'."
                        ),
                    )

                if len(entries) <= 1:
                    raise LayoutSetStoreError(
                        status_code=400,
                        code="LAYOUT_SET_ENTRIES_EMPTY",
                        message="Layout set entries must not be empty.",
                    )

                entries.pop(normalized_key, None)
                editable = LayoutSetEditableFieldsV1.model_validate(
                    {
                        "name": str(base["name"]),
                        "elkSettings": entries,
                    }
                )
                bundle = self._build_bundle(
                    layout_set_id=layout_set_id,
                    layout_set_version=int(base["draft_version"]) + 1,
                    editable=editable,
                )
                self._replace_draft(conn, bundle)

        return self.get_layout_set(layout_set_id)

    def publish_layout_set(self, layout_set_id: str) -> LayoutSetBundleV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, layout_set_id)
                exists = conn.execute(
                    """
                    SELECT 1
                    FROM layout_set_published_versions
                    WHERE layout_set_id = ?
                      AND layout_set_version = ?
                    """,
                    (layout_set_id, draft.layoutSetVersion),
                ).fetchone()
                if exists is not None:
                    raise LayoutSetStoreError(
                        status_code=409,
                        code="LAYOUT_SET_VERSION_ALREADY_PUBLISHED",
                        message=(
                            f"Layout set '{layout_set_id}' version {draft.layoutSetVersion} is already published."
                        ),
                    )

                self._insert_published_bundle(conn, draft)
                return draft

    def get_bundle(
        self,
        layout_set_id: str,
        *,
        stage: Literal["draft", "published"] = "published",
        layout_set_version: int | None = None,
    ) -> LayoutSetBundleV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                if stage == "draft":
                    draft = self._load_draft_bundle(conn, layout_set_id)
                    if layout_set_version is not None and draft.layoutSetVersion != layout_set_version:
                        raise LayoutSetStoreError(
                            status_code=404,
                            code="LAYOUT_SET_VERSION_NOT_FOUND",
                            message=(
                                f"Layout set '{layout_set_id}' draft version {layout_set_version} was not found."
                            ),
                        )
                    return draft

                rows = conn.execute(
                    """
                    SELECT layout_set_version, name, updated_at, checksum
                    FROM layout_set_published_versions
                    WHERE layout_set_id = ?
                    ORDER BY layout_set_version ASC
                    """,
                    (layout_set_id,),
                ).fetchall()

                if not rows:
                    self._assert_layout_set_exists(conn, layout_set_id)
                    raise LayoutSetStoreError(
                        status_code=404,
                        code="LAYOUT_SET_NOT_PUBLISHED",
                        message=f"Layout set '{layout_set_id}' has no published version.",
                    )

                selected = None
                if layout_set_version is None:
                    selected = rows[-1]
                else:
                    for row in rows:
                        if int(row["layout_set_version"]) == layout_set_version:
                            selected = row
                            break
                    if selected is None:
                        raise LayoutSetStoreError(
                            status_code=404,
                            code="LAYOUT_SET_VERSION_NOT_FOUND",
                            message=(
                                f"Layout set '{layout_set_id}' published version {layout_set_version} was not found."
                            ),
                        )

                version = int(selected["layout_set_version"])
                entries = self._load_published_entries(conn, layout_set_id, version)
                return self._bundle_from_parts(
                    layout_set_id=layout_set_id,
                    layout_set_version=version,
                    name=str(selected["name"]),
                    updated_at=datetime.fromisoformat(str(selected["updated_at"])),
                    checksum=str(selected["checksum"]),
                    entries=entries,
                )

    def _connect(self) -> sqlite3.Connection:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._storage_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._schema_ready:
            return

        layout_set_columns = self._table_columns(conn, "layout_sets")
        published_columns = self._table_columns(conn, "layout_set_published_versions")
        legacy_layout_schema = (
            "draft_payload" in layout_set_columns
            or ("payload" in published_columns)
            or (published_columns and "name" not in published_columns)
        )
        if legacy_layout_schema:
            self._migrate_legacy_payload_schema(conn)

        self._create_schema(conn)
        self._assert_schema_compatible(conn)
        self._schema_ready = True

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS layout_sets (
                layout_set_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                draft_version INTEGER NOT NULL,
                draft_updated_at TEXT NOT NULL,
                draft_checksum TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS layout_set_draft_entries (
                layout_set_id TEXT NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value_json TEXT NOT NULL,
                PRIMARY KEY (layout_set_id, setting_key),
                FOREIGN KEY (layout_set_id) REFERENCES layout_sets(layout_set_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS layout_set_published_versions (
                layout_set_id TEXT NOT NULL,
                layout_set_version INTEGER NOT NULL,
                name TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                checksum TEXT NOT NULL,
                PRIMARY KEY (layout_set_id, layout_set_version),
                FOREIGN KEY (layout_set_id) REFERENCES layout_sets(layout_set_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS layout_set_published_entries (
                layout_set_id TEXT NOT NULL,
                layout_set_version INTEGER NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value_json TEXT NOT NULL,
                PRIMARY KEY (layout_set_id, layout_set_version, setting_key),
                FOREIGN KEY (layout_set_id, layout_set_version)
                    REFERENCES layout_set_published_versions(layout_set_id, layout_set_version)
                    ON DELETE CASCADE
            );
            """
        )

    def _migrate_legacy_payload_schema(self, conn: sqlite3.Connection) -> None:
        layout_set_columns = self._table_columns(conn, "layout_sets")
        published_columns = self._table_columns(conn, "layout_set_published_versions")
        if "draft_payload" not in layout_set_columns or "payload" not in published_columns:
            raise LayoutSetStoreError(
                status_code=500,
                code="LAYOUT_SET_SCHEMA_MIGRATION_REQUIRED",
                message="Layout set schema is legacy but missing required payload columns for migration.",
            )

        conn.executescript(
            """
            DROP TABLE IF EXISTS layout_sets_legacy_v0;
            DROP TABLE IF EXISTS layout_set_published_versions_legacy_v0;
            DROP TABLE IF EXISTS layout_set_draft_entries_legacy_v0;
            DROP TABLE IF EXISTS layout_set_published_entries_legacy_v0;
            """
        )

        if self._table_columns(conn, "layout_set_published_entries"):
            conn.execute("ALTER TABLE layout_set_published_entries RENAME TO layout_set_published_entries_legacy_v0")
        if self._table_columns(conn, "layout_set_draft_entries"):
            conn.execute("ALTER TABLE layout_set_draft_entries RENAME TO layout_set_draft_entries_legacy_v0")
        if self._table_columns(conn, "layout_set_published_versions"):
            conn.execute("ALTER TABLE layout_set_published_versions RENAME TO layout_set_published_versions_legacy_v0")
        if self._table_columns(conn, "layout_sets"):
            conn.execute("ALTER TABLE layout_sets RENAME TO layout_sets_legacy_v0")

        self._create_schema(conn)

        draft_rows = conn.execute(
            """
            SELECT layout_set_id, draft_payload
            FROM layout_sets_legacy_v0
            ORDER BY layout_set_id ASC
            """
        ).fetchall()
        for row in draft_rows:
            bundle = self._legacy_bundle_from_json(str(row["draft_payload"]))
            conn.execute(
                """
                INSERT INTO layout_sets (
                    layout_set_id,
                    name,
                    draft_version,
                    draft_updated_at,
                    draft_checksum
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    bundle.layoutSetId,
                    bundle.name,
                    bundle.layoutSetVersion,
                    bundle.updatedAt.isoformat(),
                    bundle.checksum,
                ),
            )
            conn.executemany(
                """
                INSERT INTO layout_set_draft_entries (layout_set_id, setting_key, setting_value_json)
                VALUES (?, ?, ?)
                """,
                [
                    (bundle.layoutSetId, key, self._encode_json(value))
                    for key, value in bundle.elkSettings.items()
                ],
            )

        published_rows = conn.execute(
            """
            SELECT payload
            FROM layout_set_published_versions_legacy_v0
            ORDER BY layout_set_id ASC, layout_set_version ASC
            """
        ).fetchall()
        for row in published_rows:
            bundle = self._legacy_bundle_from_json(str(row["payload"]))
            conn.execute(
                """
                INSERT INTO layout_set_published_versions (
                    layout_set_id,
                    layout_set_version,
                    name,
                    updated_at,
                    checksum
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    bundle.layoutSetId,
                    bundle.layoutSetVersion,
                    bundle.name,
                    bundle.updatedAt.isoformat(),
                    bundle.checksum,
                ),
            )
            conn.executemany(
                """
                INSERT INTO layout_set_published_entries (
                    layout_set_id,
                    layout_set_version,
                    setting_key,
                    setting_value_json
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (bundle.layoutSetId, bundle.layoutSetVersion, key, self._encode_json(value))
                    for key, value in bundle.elkSettings.items()
                ],
            )

        conn.executescript(
            """
            DROP TABLE IF EXISTS layout_set_published_entries_legacy_v0;
            DROP TABLE IF EXISTS layout_set_draft_entries_legacy_v0;
            DROP TABLE IF EXISTS layout_set_published_versions_legacy_v0;
            DROP TABLE IF EXISTS layout_sets_legacy_v0;
            """
        )

    @staticmethod
    def _legacy_bundle_from_json(raw: str) -> LayoutSetBundleV1:
        try:
            parsed = json.loads(raw)
            return LayoutSetBundleV1.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LayoutSetStoreError(
                status_code=500,
                code="LAYOUT_SET_STORAGE_CORRUPTED",
                message="Layout set storage payload is unreadable or invalid. Manual migration required.",
            ) from exc

    def _assert_schema_compatible(self, conn: sqlite3.Connection) -> None:
        expected_columns = {
            "layout_sets": {
                "layout_set_id",
                "name",
                "draft_version",
                "draft_updated_at",
                "draft_checksum",
            },
            "layout_set_draft_entries": {
                "layout_set_id",
                "setting_key",
                "setting_value_json",
            },
            "layout_set_published_versions": {
                "layout_set_id",
                "layout_set_version",
                "name",
                "updated_at",
                "checksum",
            },
            "layout_set_published_entries": {
                "layout_set_id",
                "layout_set_version",
                "setting_key",
                "setting_value_json",
            },
        }
        for table_name, required_columns in expected_columns.items():
            actual_columns = self._table_columns(conn, table_name)
            missing = required_columns - actual_columns
            if missing:
                raise LayoutSetStoreError(
                    status_code=500,
                    code="LAYOUT_SET_SCHEMA_MIGRATION_REQUIRED",
                    message=(
                        f"Layout set store schema is incompatible for table '{table_name}'. "
                        "Manual migration required."
                    ),
                    details={"missingColumns": sorted(missing)},
                )

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

    def _assert_layout_set_exists(self, conn: sqlite3.Connection, layout_set_id: str) -> None:
        row = conn.execute(
            "SELECT 1 FROM layout_sets WHERE layout_set_id = ?",
            (layout_set_id,),
        ).fetchone()
        if row is None:
            raise LayoutSetStoreError(
                status_code=404,
                code="LAYOUT_SET_NOT_FOUND",
                message=f"Layout set '{layout_set_id}' was not found.",
            )

    def _validate_elk_settings(self, elk_settings: dict[str, Any]) -> dict[str, Any]:
        try:
            canonical = dict(elk_settings)
            canonical["type_icon_map"] = {}
            canonical["edge_type_overrides"] = {}
            validated = ElkSettings.model_validate(canonical)
        except ValidationError as exc:
            raise LayoutSetStoreError(
                status_code=400,
                code="INVALID_ELK_SETTINGS",
                message="elkSettings failed GraphLoom validation.",
                details={"errors": exc.errors()},
            ) from exc

        dumped = validated.model_dump(by_alias=True, exclude_none=True, mode="json")
        dumped.pop("type_icon_map", None)
        dumped.pop("edge_type_overrides", None)
        return dumped

    def _build_bundle(
        self,
        *,
        layout_set_id: str,
        layout_set_version: int,
        editable: LayoutSetEditableFieldsV1,
    ) -> LayoutSetBundleV1:
        settings = self._validate_elk_settings(editable.elkSettings)
        payload = {
            "schemaVersion": "v1",
            "layoutSetId": layout_set_id,
            "layoutSetVersion": layout_set_version,
            "name": editable.name,
            "elkSettings": settings,
            "updatedAt": utcnow(),
        }
        payload["checksum"] = compute_layout_set_checksum(payload)
        return LayoutSetBundleV1.model_validate(payload)

    def _bundle_from_parts(
        self,
        *,
        layout_set_id: str,
        layout_set_version: int,
        name: str,
        updated_at: datetime,
        checksum: str,
        entries: dict[str, Any],
    ) -> LayoutSetBundleV1:
        settings = self._validate_elk_settings(entries)
        payload = {
            "schemaVersion": "v1",
            "layoutSetId": layout_set_id,
            "layoutSetVersion": layout_set_version,
            "name": name,
            "elkSettings": settings,
            "updatedAt": updated_at,
        }
        expected_checksum = compute_layout_set_checksum(payload)
        if checksum != expected_checksum:
            raise LayoutSetStoreError(
                status_code=500,
                code="LAYOUT_SET_STORAGE_CORRUPTED",
                message="Layout set checksum does not match stored entries.",
            )
        payload["checksum"] = checksum
        return LayoutSetBundleV1.model_validate(payload)

    def _insert_layout_set(self, conn: sqlite3.Connection, bundle: LayoutSetBundleV1, *, publish: bool) -> None:
        conn.execute(
            """
            INSERT INTO layout_sets (
                layout_set_id,
                name,
                draft_version,
                draft_updated_at,
                draft_checksum
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                bundle.layoutSetId,
                bundle.name,
                bundle.layoutSetVersion,
                bundle.updatedAt.isoformat(),
                bundle.checksum,
            ),
        )
        conn.executemany(
            """
            INSERT INTO layout_set_draft_entries (layout_set_id, setting_key, setting_value_json)
            VALUES (?, ?, ?)
            """,
            [
                (bundle.layoutSetId, key, self._encode_json(value))
                for key, value in bundle.elkSettings.items()
            ],
        )
        if publish:
            self._insert_published_bundle(conn, bundle)

    def _replace_draft(self, conn: sqlite3.Connection, bundle: LayoutSetBundleV1) -> None:
        conn.execute(
            """
            UPDATE layout_sets
            SET
                name = ?,
                draft_version = ?,
                draft_updated_at = ?,
                draft_checksum = ?
            WHERE layout_set_id = ?
            """,
            (
                bundle.name,
                bundle.layoutSetVersion,
                bundle.updatedAt.isoformat(),
                bundle.checksum,
                bundle.layoutSetId,
            ),
        )
        conn.execute(
            "DELETE FROM layout_set_draft_entries WHERE layout_set_id = ?",
            (bundle.layoutSetId,),
        )
        conn.executemany(
            """
            INSERT INTO layout_set_draft_entries (layout_set_id, setting_key, setting_value_json)
            VALUES (?, ?, ?)
            """,
            [
                (bundle.layoutSetId, key, self._encode_json(value))
                for key, value in bundle.elkSettings.items()
            ],
        )

    def _insert_published_bundle(self, conn: sqlite3.Connection, bundle: LayoutSetBundleV1) -> None:
        conn.execute(
            """
            INSERT INTO layout_set_published_versions (
                layout_set_id,
                layout_set_version,
                name,
                updated_at,
                checksum
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                bundle.layoutSetId,
                bundle.layoutSetVersion,
                bundle.name,
                bundle.updatedAt.isoformat(),
                bundle.checksum,
            ),
        )
        conn.executemany(
            """
            INSERT INTO layout_set_published_entries (
                layout_set_id,
                layout_set_version,
                setting_key,
                setting_value_json
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (bundle.layoutSetId, bundle.layoutSetVersion, key, self._encode_json(value))
                for key, value in bundle.elkSettings.items()
            ],
        )

    def _load_draft_bundle(self, conn: sqlite3.Connection, layout_set_id: str) -> LayoutSetBundleV1:
        row = conn.execute(
            """
            SELECT name, draft_version, draft_updated_at, draft_checksum
            FROM layout_sets
            WHERE layout_set_id = ?
            """,
            (layout_set_id,),
        ).fetchone()
        if row is None:
            raise LayoutSetStoreError(
                status_code=404,
                code="LAYOUT_SET_NOT_FOUND",
                message=f"Layout set '{layout_set_id}' was not found.",
            )

        entries = self._load_draft_entries(conn, layout_set_id)
        return self._bundle_from_parts(
            layout_set_id=layout_set_id,
            layout_set_version=int(row["draft_version"]),
            name=str(row["name"]),
            updated_at=datetime.fromisoformat(str(row["draft_updated_at"])),
            checksum=str(row["draft_checksum"]),
            entries=entries,
        )

    def _load_draft_entries(self, conn: sqlite3.Connection, layout_set_id: str) -> dict[str, Any]:
        rows = conn.execute(
            """
            SELECT setting_key, setting_value_json
            FROM layout_set_draft_entries
            WHERE layout_set_id = ?
            ORDER BY setting_key ASC
            """,
            (layout_set_id,),
        ).fetchall()
        return {str(row["setting_key"]): self._decode_json(str(row["setting_value_json"])) for row in rows}

    def _load_published_entries(
        self,
        conn: sqlite3.Connection,
        layout_set_id: str,
        layout_set_version: int,
    ) -> dict[str, Any]:
        rows = conn.execute(
            """
            SELECT setting_key, setting_value_json
            FROM layout_set_published_entries
            WHERE layout_set_id = ? AND layout_set_version = ?
            ORDER BY setting_key ASC
            """,
            (layout_set_id, layout_set_version),
        ).fetchall()
        return {str(row["setting_key"]): self._decode_json(str(row["setting_value_json"])) for row in rows}

    @staticmethod
    def _encode_json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _decode_json(value: str) -> Any:
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise LayoutSetStoreError(
                status_code=500,
                code="LAYOUT_SET_STORAGE_CORRUPTED",
                message="Layout set entry payload is unreadable.",
            ) from exc
