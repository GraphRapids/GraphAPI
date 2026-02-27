from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import ValidationError

from .graph_type_contract import (
    LinkSetBundleV1,
    LinkSetCreateRequestV1,
    LinkSetEditableFieldsV1,
    LinkSetEntryUpsertRequestV1,
    LinkSetListResponseV1,
    LinkSetRecordV1,
    LinkSetSummaryV1,
    LinkSetUpdateRequestV1,
    compute_link_set_checksum,
    normalize_type_key,
    utcnow,
)


class LinkSetStoreError(Exception):
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


class LinkSetStore:
    def __init__(self, storage_path: Path) -> None:
        self._storage_path = storage_path
        self._lock = RLock()
        self._schema_ready = False

    @classmethod
    def from_env(cls) -> "LinkSetStore":
        raw = os.getenv("GRAPHAPI_RUNTIME_DB_PATH", "").strip()
        if not raw:
            raw = os.getenv("GRAPHAPI_LINK_SET_STORE_PATH", "").strip()
        if raw:
            return cls(Path(raw).expanduser())
        return cls(Path.home() / ".cache" / "graphapi" / "runtime.v1.sqlite3")

    def ensure_default_link_set(self, request: LinkSetCreateRequestV1) -> None:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT 1 FROM link_sets WHERE link_set_id = ?",
                    (request.linkSetId,),
                ).fetchone()
                if row is not None:
                    return

                bundle = self._build_bundle(
                    link_set_id=request.linkSetId,
                    link_set_version=1,
                    editable=request,
                )
                self._insert_link_set(conn, bundle, publish=True)

    def list_link_sets(self) -> LinkSetListResponseV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                rows = conn.execute(
                    """
                    SELECT
                        s.link_set_id,
                        s.name,
                        s.draft_version,
                        s.draft_updated_at,
                        s.draft_checksum,
                        (
                            SELECT MAX(v.link_set_version)
                            FROM link_set_published_versions v
                            WHERE v.link_set_id = s.link_set_id
                        ) AS published_version
                    FROM link_sets s
                    ORDER BY s.link_set_id ASC
                    """
                ).fetchall()

                return LinkSetListResponseV1(
                    linkSets=[
                        LinkSetSummaryV1(
                            linkSetId=str(row["link_set_id"]),
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

    def get_link_set(self, link_set_id: str) -> LinkSetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, link_set_id)
                published_rows = conn.execute(
                    """
                    SELECT payload
                    FROM link_set_published_versions
                    WHERE link_set_id = ?
                    ORDER BY link_set_version ASC
                    """,
                    (link_set_id,),
                ).fetchall()
                published = [self._bundle_from_json(str(row["payload"])) for row in published_rows]
                return LinkSetRecordV1(
                    linkSetId=link_set_id,
                    draft=draft,
                    publishedVersions=published,
                )

    def create_link_set(self, request: LinkSetCreateRequestV1) -> LinkSetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                exists = conn.execute(
                    "SELECT 1 FROM link_sets WHERE link_set_id = ?",
                    (request.linkSetId,),
                ).fetchone()
                if exists is not None:
                    raise LinkSetStoreError(
                        status_code=409,
                        code="LINK_SET_ALREADY_EXISTS",
                        message=f"Link set '{request.linkSetId}' already exists.",
                    )

                bundle = self._build_bundle(
                    link_set_id=request.linkSetId,
                    link_set_version=1,
                    editable=request,
                )
                self._insert_link_set(conn, bundle, publish=False)

        return self.get_link_set(request.linkSetId)

    def update_link_set(self, link_set_id: str, request: LinkSetUpdateRequestV1) -> LinkSetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT draft_version FROM link_sets WHERE link_set_id = ?",
                    (link_set_id,),
                ).fetchone()
                if row is None:
                    raise LinkSetStoreError(
                        status_code=404,
                        code="LINK_SET_NOT_FOUND",
                        message=f"Link set '{link_set_id}' was not found.",
                    )

                next_version = int(row["draft_version"]) + 1
                bundle = self._build_bundle(
                    link_set_id=link_set_id,
                    link_set_version=next_version,
                    editable=request,
                )
                self._replace_draft(conn, bundle)

        return self.get_link_set(link_set_id)

    def delete_link_set(self, link_set_id: str) -> None:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                result = conn.execute(
                    "DELETE FROM link_sets WHERE link_set_id = ?",
                    (link_set_id,),
                )
                if int(result.rowcount or 0) < 1:
                    raise LinkSetStoreError(
                        status_code=404,
                        code="LINK_SET_NOT_FOUND",
                        message=f"Link set '{link_set_id}' was not found.",
                    )

    def upsert_link_entry(
        self,
        link_set_id: str,
        key: str,
        request: LinkSetEntryUpsertRequestV1,
    ) -> LinkSetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, link_set_id)
                entries = dict(draft.entries)
                entries[normalize_type_key(key)] = request

                editable = LinkSetEditableFieldsV1.model_validate(
                    {
                        "name": draft.name,
                        "entries": entries,
                    }
                )
                bundle = self._build_bundle(
                    link_set_id=link_set_id,
                    link_set_version=draft.linkSetVersion + 1,
                    editable=editable,
                )
                self._replace_draft(conn, bundle)

        return self.get_link_set(link_set_id)

    def delete_link_entry(self, link_set_id: str, key: str) -> LinkSetRecordV1:
        normalized_key = normalize_type_key(key)
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, link_set_id)
                entries = dict(draft.entries)
                if normalized_key not in entries:
                    raise LinkSetStoreError(
                        status_code=404,
                        code="LINK_TYPE_NOT_FOUND",
                        message=(
                            f"Link type key '{normalized_key}' was not found in link set '{link_set_id}'."
                        ),
                    )
                if len(entries) <= 1:
                    raise LinkSetStoreError(
                        status_code=400,
                        code="LINK_SET_ENTRIES_EMPTY",
                        message="Link set entries must not be empty.",
                    )
                entries.pop(normalized_key, None)

                editable = LinkSetEditableFieldsV1.model_validate(
                    {
                        "name": draft.name,
                        "entries": entries,
                    }
                )
                bundle = self._build_bundle(
                    link_set_id=link_set_id,
                    link_set_version=draft.linkSetVersion + 1,
                    editable=editable,
                )
                self._replace_draft(conn, bundle)

        return self.get_link_set(link_set_id)

    def publish_link_set(self, link_set_id: str) -> LinkSetBundleV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, link_set_id)
                exists = conn.execute(
                    """
                    SELECT 1
                    FROM link_set_published_versions
                    WHERE link_set_id = ?
                      AND link_set_version = ?
                    """,
                    (link_set_id, draft.linkSetVersion),
                ).fetchone()
                if exists is not None:
                    raise LinkSetStoreError(
                        status_code=409,
                        code="LINK_SET_VERSION_ALREADY_PUBLISHED",
                        message=(
                            f"Link set '{link_set_id}' version {draft.linkSetVersion} is already published."
                        ),
                    )

                conn.execute(
                    """
                    INSERT INTO link_set_published_versions (
                        link_set_id,
                        link_set_version,
                        updated_at,
                        checksum,
                        payload
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        draft.linkSetId,
                        draft.linkSetVersion,
                        draft.updatedAt.isoformat(),
                        draft.checksum,
                        self._bundle_to_json(draft),
                    ),
                )
                return draft

    def get_bundle(
        self,
        link_set_id: str,
        *,
        stage: Literal["draft", "published"] = "published",
        link_set_version: int | None = None,
    ) -> LinkSetBundleV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                if stage == "draft":
                    draft = self._load_draft_bundle(conn, link_set_id)
                    if link_set_version is not None and draft.linkSetVersion != link_set_version:
                        raise LinkSetStoreError(
                            status_code=404,
                            code="LINK_SET_VERSION_NOT_FOUND",
                            message=(
                                f"Link set '{link_set_id}' draft version {link_set_version} was not found."
                            ),
                        )
                    return draft

                rows = conn.execute(
                    """
                    SELECT link_set_version, payload
                    FROM link_set_published_versions
                    WHERE link_set_id = ?
                    ORDER BY link_set_version ASC
                    """,
                    (link_set_id,),
                ).fetchall()

                if not rows:
                    self._assert_link_set_exists(conn, link_set_id)
                    raise LinkSetStoreError(
                        status_code=404,
                        code="LINK_SET_NOT_PUBLISHED",
                        message=f"Link set '{link_set_id}' has no published version.",
                    )

                selected = None
                if link_set_version is None:
                    selected = rows[-1]
                else:
                    for row in rows:
                        if int(row["link_set_version"]) == link_set_version:
                            selected = row
                            break
                    if selected is None:
                        raise LinkSetStoreError(
                            status_code=404,
                            code="LINK_SET_VERSION_NOT_FOUND",
                            message=(
                                f"Link set '{link_set_id}' published version {link_set_version} was not found."
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
            CREATE TABLE IF NOT EXISTS link_sets (
                link_set_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                draft_version INTEGER NOT NULL,
                draft_updated_at TEXT NOT NULL,
                draft_checksum TEXT NOT NULL,
                draft_payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS link_set_published_versions (
                link_set_id TEXT NOT NULL,
                link_set_version INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                checksum TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (link_set_id, link_set_version),
                FOREIGN KEY (link_set_id) REFERENCES link_sets(link_set_id) ON DELETE CASCADE
            );
            """
        )
        self._schema_ready = True

    def _assert_link_set_exists(self, conn: sqlite3.Connection, link_set_id: str) -> None:
        row = conn.execute(
            "SELECT 1 FROM link_sets WHERE link_set_id = ?",
            (link_set_id,),
        ).fetchone()
        if row is None:
            raise LinkSetStoreError(
                status_code=404,
                code="LINK_SET_NOT_FOUND",
                message=f"Link set '{link_set_id}' was not found.",
            )

    def _build_bundle(self, *, link_set_id: str, link_set_version: int, editable: LinkSetEditableFieldsV1) -> LinkSetBundleV1:
        payload = {
            "schemaVersion": "v1",
            "linkSetId": link_set_id,
            "linkSetVersion": link_set_version,
            "name": editable.name,
            "entries": editable.entries,
            "updatedAt": utcnow(),
        }
        payload["checksum"] = compute_link_set_checksum(payload)
        return LinkSetBundleV1.model_validate(payload)

    def _insert_link_set(self, conn: sqlite3.Connection, bundle: LinkSetBundleV1, *, publish: bool) -> None:
        conn.execute(
            """
            INSERT INTO link_sets (
                link_set_id,
                name,
                draft_version,
                draft_updated_at,
                draft_checksum,
                draft_payload
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                bundle.linkSetId,
                bundle.name,
                bundle.linkSetVersion,
                bundle.updatedAt.isoformat(),
                bundle.checksum,
                self._bundle_to_json(bundle),
            ),
        )
        if publish:
            conn.execute(
                """
                INSERT INTO link_set_published_versions (
                    link_set_id,
                    link_set_version,
                    updated_at,
                    checksum,
                    payload
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    bundle.linkSetId,
                    bundle.linkSetVersion,
                    bundle.updatedAt.isoformat(),
                    bundle.checksum,
                    self._bundle_to_json(bundle),
                ),
            )

    def _replace_draft(self, conn: sqlite3.Connection, bundle: LinkSetBundleV1) -> None:
        conn.execute(
            """
            UPDATE link_sets
            SET
                name = ?,
                draft_version = ?,
                draft_updated_at = ?,
                draft_checksum = ?,
                draft_payload = ?
            WHERE link_set_id = ?
            """,
            (
                bundle.name,
                bundle.linkSetVersion,
                bundle.updatedAt.isoformat(),
                bundle.checksum,
                self._bundle_to_json(bundle),
                bundle.linkSetId,
            ),
        )

    def _load_draft_bundle(self, conn: sqlite3.Connection, link_set_id: str) -> LinkSetBundleV1:
        row = conn.execute(
            "SELECT draft_payload FROM link_sets WHERE link_set_id = ?",
            (link_set_id,),
        ).fetchone()
        if row is None:
            raise LinkSetStoreError(
                status_code=404,
                code="LINK_SET_NOT_FOUND",
                message=f"Link set '{link_set_id}' was not found.",
            )
        return self._bundle_from_json(str(row["draft_payload"]))

    @staticmethod
    def _bundle_to_json(bundle: LinkSetBundleV1) -> str:
        return json.dumps(bundle.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _bundle_from_json(raw: str) -> LinkSetBundleV1:
        try:
            return LinkSetBundleV1.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LinkSetStoreError(
                status_code=500,
                code="LINK_SET_STORAGE_CORRUPTED",
                message="Link set storage payload is unreadable or invalid.",
            ) from exc
