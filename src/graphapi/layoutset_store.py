from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Literal

from graphloom import ElkSettings
from pydantic import ValidationError

from .graph_type_contract import (
    LayoutSetBundleV1,
    LayoutSetCreateRequestV1,
    LayoutSetEditableFieldsV1,
    LayoutSetListResponseV1,
    LayoutSetRecordV1,
    LayoutSetSummaryV1,
    LayoutSetUpdateRequestV1,
    compute_layout_set_checksum,
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
                    SELECT payload
                    FROM layout_set_published_versions
                    WHERE layout_set_id = ?
                    ORDER BY layout_set_version ASC
                    """,
                    (layout_set_id,),
                ).fetchall()
                published = [self._bundle_from_json(str(row["payload"])) for row in published_rows]
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
                        draft.layoutSetId,
                        draft.layoutSetVersion,
                        draft.updatedAt.isoformat(),
                        draft.checksum,
                        self._bundle_to_json(draft),
                    ),
                )
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
                    SELECT layout_set_version, payload
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

                return self._bundle_from_json(str(selected["payload"]))

    def _connect(self) -> sqlite3.Connection:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._storage_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._schema_ready:
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS layout_sets (
                layout_set_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                draft_version INTEGER NOT NULL,
                draft_updated_at TEXT NOT NULL,
                draft_checksum TEXT NOT NULL,
                draft_payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS layout_set_published_versions (
                layout_set_id TEXT NOT NULL,
                layout_set_version INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                checksum TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (layout_set_id, layout_set_version),
                FOREIGN KEY (layout_set_id) REFERENCES layout_sets(layout_set_id) ON DELETE CASCADE
            );
            """
        )
        self._schema_ready = True

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

    def _validate_elk_settings(self, elk_settings: dict[str, object]) -> dict[str, object]:
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
        return validated.model_dump(by_alias=True, exclude_none=True, mode="json")

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

    def _insert_layout_set(self, conn: sqlite3.Connection, bundle: LayoutSetBundleV1, *, publish: bool) -> None:
        conn.execute(
            """
            INSERT INTO layout_sets (
                layout_set_id,
                name,
                draft_version,
                draft_updated_at,
                draft_checksum,
                draft_payload
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                bundle.layoutSetId,
                bundle.name,
                bundle.layoutSetVersion,
                bundle.updatedAt.isoformat(),
                bundle.checksum,
                self._bundle_to_json(bundle),
            ),
        )
        if publish:
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
                    bundle.layoutSetId,
                    bundle.layoutSetVersion,
                    bundle.updatedAt.isoformat(),
                    bundle.checksum,
                    self._bundle_to_json(bundle),
                ),
            )

    def _replace_draft(self, conn: sqlite3.Connection, bundle: LayoutSetBundleV1) -> None:
        conn.execute(
            """
            UPDATE layout_sets
            SET
                name = ?,
                draft_version = ?,
                draft_updated_at = ?,
                draft_checksum = ?,
                draft_payload = ?
            WHERE layout_set_id = ?
            """,
            (
                bundle.name,
                bundle.layoutSetVersion,
                bundle.updatedAt.isoformat(),
                bundle.checksum,
                self._bundle_to_json(bundle),
                bundle.layoutSetId,
            ),
        )

    def _load_draft_bundle(self, conn: sqlite3.Connection, layout_set_id: str) -> LayoutSetBundleV1:
        row = conn.execute(
            "SELECT draft_payload FROM layout_sets WHERE layout_set_id = ?",
            (layout_set_id,),
        ).fetchone()
        if row is None:
            raise LayoutSetStoreError(
                status_code=404,
                code="LAYOUT_SET_NOT_FOUND",
                message=f"Layout set '{layout_set_id}' was not found.",
            )
        return self._bundle_from_json(str(row["draft_payload"]))

    @staticmethod
    def _bundle_to_json(bundle: LayoutSetBundleV1) -> str:
        return json.dumps(bundle.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _bundle_from_json(raw: str) -> LayoutSetBundleV1:
        try:
            return LayoutSetBundleV1.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LayoutSetStoreError(
                status_code=500,
                code="LAYOUT_SET_STORAGE_CORRUPTED",
                message="Layout set storage payload is unreadable or invalid.",
            ) from exc
