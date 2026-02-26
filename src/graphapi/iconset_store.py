from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Literal

from .profile_contract import (
    IconsetBundleV1,
    IconsetCreateRequestV1,
    IconsetEditableFieldsV1,
    IconsetEntryUpsertRequestV1,
    IconsetListResponseV1,
    IconsetRecordV1,
    IconsetSummaryV1,
    IconsetUpdateRequestV1,
    compute_iconset_checksum,
    normalize_type_key,
    utcnow,
)


class IconsetStoreError(Exception):
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


class IconsetStore:
    def __init__(self, storage_path: Path) -> None:
        self._storage_path = storage_path
        self._lock = RLock()
        self._schema_ready = False

    @classmethod
    def from_env(cls) -> "IconsetStore":
        raw = os.getenv("GRAPHAPI_RUNTIME_DB_PATH", "").strip()
        if not raw:
            raw = os.getenv("GRAPHAPI_ICONSET_STORE_PATH", "").strip()
        if raw:
            return cls(Path(raw).expanduser())
        return cls(Path.home() / ".cache" / "graphapi" / "runtime.v1.sqlite3")

    def ensure_default_iconset(self, request: IconsetCreateRequestV1) -> None:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT 1 FROM iconsets WHERE iconset_id = ?",
                    (request.iconsetId,),
                ).fetchone()
                if row is not None:
                    return

                bundle = self._build_bundle(
                    iconset_id=request.iconsetId,
                    iconset_version=1,
                    editable=request,
                )
                self._insert_iconset(conn, bundle, publish=True)

    def list_iconsets(self) -> IconsetListResponseV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                rows = conn.execute(
                    """
                    SELECT
                        i.iconset_id,
                        i.name,
                        i.draft_version,
                        i.draft_updated_at,
                        i.draft_checksum,
                        (
                            SELECT MAX(p.iconset_version)
                            FROM iconset_published_versions p
                            WHERE p.iconset_id = i.iconset_id
                        ) AS published_version
                    FROM iconsets i
                    ORDER BY i.iconset_id ASC
                    """
                ).fetchall()

                summaries = [
                    IconsetSummaryV1(
                        iconsetId=str(row["iconset_id"]),
                        name=str(row["name"]),
                        draftVersion=int(row["draft_version"]),
                        publishedVersion=(
                            int(row["published_version"]) if row["published_version"] is not None else None
                        ),
                        updatedAt=self._parse_dt(str(row["draft_updated_at"])),
                        checksum=str(row["draft_checksum"]),
                    )
                    for row in rows
                ]

                return IconsetListResponseV1(iconsets=summaries)

    def get_iconset(self, iconset_id: str) -> IconsetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, iconset_id)
                published = self._load_published_bundles(conn, iconset_id)
                return IconsetRecordV1(
                    iconsetId=iconset_id,
                    draft=draft,
                    publishedVersions=published,
                )

    def create_iconset(self, request: IconsetCreateRequestV1) -> IconsetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                exists = conn.execute(
                    "SELECT 1 FROM iconsets WHERE iconset_id = ?",
                    (request.iconsetId,),
                ).fetchone()
                if exists is not None:
                    raise IconsetStoreError(
                        status_code=409,
                        code="ICONSET_ALREADY_EXISTS",
                        message=f"Iconset '{request.iconsetId}' already exists.",
                    )

                bundle = self._build_bundle(
                    iconset_id=request.iconsetId,
                    iconset_version=1,
                    editable=request,
                )
                self._insert_iconset(conn, bundle, publish=False)

        return self.get_iconset(request.iconsetId)

    def update_iconset(
        self,
        iconset_id: str,
        request: IconsetUpdateRequestV1,
    ) -> IconsetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT draft_version FROM iconsets WHERE iconset_id = ?",
                    (iconset_id,),
                ).fetchone()
                if row is None:
                    raise IconsetStoreError(
                        status_code=404,
                        code="ICONSET_NOT_FOUND",
                        message=f"Iconset '{iconset_id}' was not found.",
                    )

                next_version = int(row["draft_version"]) + 1
                bundle = self._build_bundle(
                    iconset_id=iconset_id,
                    iconset_version=next_version,
                    editable=request,
                )
                self._replace_draft(conn, bundle)

        return self.get_iconset(iconset_id)

    def upsert_iconset_entry(
        self,
        iconset_id: str,
        type_key: str,
        request: IconsetEntryUpsertRequestV1,
    ) -> IconsetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                base = conn.execute(
                    "SELECT name, draft_version FROM iconsets WHERE iconset_id = ?",
                    (iconset_id,),
                ).fetchone()
                if base is None:
                    raise IconsetStoreError(
                        status_code=404,
                        code="ICONSET_NOT_FOUND",
                        message=f"Iconset '{iconset_id}' was not found.",
                    )

                entries = self._load_entries(conn, iconset_id)
                entries[normalize_type_key(type_key)] = request.icon

                editable = IconsetEditableFieldsV1.model_validate(
                    {
                        "name": str(base["name"]),
                        "entries": entries,
                    }
                )
                bundle = self._build_bundle(
                    iconset_id=iconset_id,
                    iconset_version=int(base["draft_version"]) + 1,
                    editable=editable,
                )
                self._replace_draft(conn, bundle)

        return self.get_iconset(iconset_id)

    def delete_iconset_entry(
        self,
        iconset_id: str,
        type_key: str,
    ) -> IconsetRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                base = conn.execute(
                    "SELECT name, draft_version FROM iconsets WHERE iconset_id = ?",
                    (iconset_id,),
                ).fetchone()
                if base is None:
                    raise IconsetStoreError(
                        status_code=404,
                        code="ICONSET_NOT_FOUND",
                        message=f"Iconset '{iconset_id}' was not found.",
                    )

                normalized_key = normalize_type_key(type_key)
                entries = self._load_entries(conn, iconset_id)
                if normalized_key not in entries:
                    raise IconsetStoreError(
                        status_code=404,
                        code="ICONSET_ENTRY_NOT_FOUND",
                        message=(
                            f"Node type key '{normalized_key}' was not found in iconset '{iconset_id}'."
                        ),
                    )

                if len(entries) <= 1:
                    raise IconsetStoreError(
                        status_code=400,
                        code="ICONSET_ENTRIES_EMPTY",
                        message="Iconset entries must not be empty.",
                    )

                entries.pop(normalized_key, None)
                editable = IconsetEditableFieldsV1.model_validate(
                    {
                        "name": str(base["name"]),
                        "entries": entries,
                    }
                )
                bundle = self._build_bundle(
                    iconset_id=iconset_id,
                    iconset_version=int(base["draft_version"]) + 1,
                    editable=editable,
                )
                self._replace_draft(conn, bundle)

        return self.get_iconset(iconset_id)

    def publish_iconset(self, iconset_id: str) -> IconsetBundleV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, iconset_id)
                exists = conn.execute(
                    """
                    SELECT 1
                    FROM iconset_published_versions
                    WHERE iconset_id = ?
                      AND iconset_version = ?
                    """,
                    (iconset_id, draft.iconsetVersion),
                ).fetchone()
                if exists is not None:
                    raise IconsetStoreError(
                        status_code=409,
                        code="ICONSET_VERSION_ALREADY_PUBLISHED",
                        message=(
                            f"Iconset '{iconset_id}' version {draft.iconsetVersion} is already published."
                        ),
                    )

                self._insert_published_bundle(conn, draft)
                return draft

    def get_bundle(
        self,
        iconset_id: str,
        *,
        stage: Literal["draft", "published"] = "published",
        iconset_version: int | None = None,
    ) -> IconsetBundleV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)

                if stage == "draft":
                    draft = self._load_draft_bundle(conn, iconset_id)
                    if iconset_version is not None and draft.iconsetVersion != iconset_version:
                        raise IconsetStoreError(
                            status_code=404,
                            code="ICONSET_VERSION_NOT_FOUND",
                            message=(
                                f"Iconset '{iconset_id}' draft version {iconset_version} was not found."
                            ),
                        )
                    return draft

                rows = conn.execute(
                    """
                    SELECT iconset_version, name, updated_at, checksum
                    FROM iconset_published_versions
                    WHERE iconset_id = ?
                    ORDER BY iconset_version ASC
                    """,
                    (iconset_id,),
                ).fetchall()

                if not rows:
                    self._assert_iconset_exists(conn, iconset_id)
                    raise IconsetStoreError(
                        status_code=404,
                        code="ICONSET_NOT_PUBLISHED",
                        message=f"Iconset '{iconset_id}' has no published version.",
                    )

                selected = None
                if iconset_version is None:
                    selected = rows[-1]
                else:
                    for row in rows:
                        if int(row["iconset_version"]) == iconset_version:
                            selected = row
                            break
                    if selected is None:
                        raise IconsetStoreError(
                            status_code=404,
                            code="ICONSET_VERSION_NOT_FOUND",
                            message=(
                                f"Iconset '{iconset_id}' published version {iconset_version} was not found."
                            ),
                        )

                entries = self._load_published_entries(conn, iconset_id, int(selected["iconset_version"]))
                payload = {
                    "schemaVersion": "v1",
                    "iconsetId": iconset_id,
                    "iconsetVersion": int(selected["iconset_version"]),
                    "name": str(selected["name"]),
                    "entries": entries,
                    "updatedAt": self._parse_dt(str(selected["updated_at"])),
                    "checksum": str(selected["checksum"]),
                }
                return IconsetBundleV1.model_validate(payload)

    def _connect(self) -> sqlite3.Connection:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self._storage_path), timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._schema_ready:
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS iconsets (
                iconset_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                draft_version INTEGER NOT NULL,
                draft_updated_at TEXT NOT NULL,
                draft_checksum TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS iconset_draft_entries (
                iconset_id TEXT NOT NULL,
                type_key TEXT NOT NULL,
                icon_name TEXT NOT NULL,
                PRIMARY KEY (iconset_id, type_key),
                FOREIGN KEY (iconset_id) REFERENCES iconsets(iconset_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS iconset_published_versions (
                iconset_id TEXT NOT NULL,
                iconset_version INTEGER NOT NULL,
                name TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                checksum TEXT NOT NULL,
                PRIMARY KEY (iconset_id, iconset_version),
                FOREIGN KEY (iconset_id) REFERENCES iconsets(iconset_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS iconset_published_entries (
                iconset_id TEXT NOT NULL,
                iconset_version INTEGER NOT NULL,
                type_key TEXT NOT NULL,
                icon_name TEXT NOT NULL,
                PRIMARY KEY (iconset_id, iconset_version, type_key),
                FOREIGN KEY (iconset_id, iconset_version)
                    REFERENCES iconset_published_versions(iconset_id, iconset_version)
                    ON DELETE CASCADE
            );
            """
        )
        self._schema_ready = True

    def _assert_iconset_exists(self, conn: sqlite3.Connection, iconset_id: str) -> None:
        row = conn.execute(
            "SELECT 1 FROM iconsets WHERE iconset_id = ?",
            (iconset_id,),
        ).fetchone()
        if row is None:
            raise IconsetStoreError(
                status_code=404,
                code="ICONSET_NOT_FOUND",
                message=f"Iconset '{iconset_id}' was not found.",
            )

    def _build_bundle(
        self,
        *,
        iconset_id: str,
        iconset_version: int,
        editable: IconsetEditableFieldsV1,
    ) -> IconsetBundleV1:
        timestamp = utcnow()
        payload = {
            "schemaVersion": "v1",
            "iconsetId": iconset_id,
            "iconsetVersion": iconset_version,
            "name": editable.name,
            "entries": editable.entries,
            "updatedAt": timestamp,
        }
        payload["checksum"] = compute_iconset_checksum(payload)
        return IconsetBundleV1.model_validate(payload)

    def _insert_iconset(
        self,
        conn: sqlite3.Connection,
        bundle: IconsetBundleV1,
        *,
        publish: bool,
    ) -> None:
        conn.execute(
            """
            INSERT INTO iconsets (iconset_id, name, draft_version, draft_updated_at, draft_checksum)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                bundle.iconsetId,
                bundle.name,
                bundle.iconsetVersion,
                self._serialize_dt(bundle.updatedAt),
                bundle.checksum,
            ),
        )
        conn.executemany(
            """
            INSERT INTO iconset_draft_entries (iconset_id, type_key, icon_name)
            VALUES (?, ?, ?)
            """,
            [
                (bundle.iconsetId, key, icon)
                for key, icon in bundle.entries.items()
            ],
        )
        if publish:
            self._insert_published_bundle(conn, bundle)

    def _replace_draft(self, conn: sqlite3.Connection, bundle: IconsetBundleV1) -> None:
        conn.execute(
            """
            UPDATE iconsets
            SET name = ?, draft_version = ?, draft_updated_at = ?, draft_checksum = ?
            WHERE iconset_id = ?
            """,
            (
                bundle.name,
                bundle.iconsetVersion,
                self._serialize_dt(bundle.updatedAt),
                bundle.checksum,
                bundle.iconsetId,
            ),
        )
        conn.execute(
            "DELETE FROM iconset_draft_entries WHERE iconset_id = ?",
            (bundle.iconsetId,),
        )
        conn.executemany(
            """
            INSERT INTO iconset_draft_entries (iconset_id, type_key, icon_name)
            VALUES (?, ?, ?)
            """,
            [
                (bundle.iconsetId, key, icon)
                for key, icon in bundle.entries.items()
            ],
        )

    def _insert_published_bundle(self, conn: sqlite3.Connection, bundle: IconsetBundleV1) -> None:
        conn.execute(
            """
            INSERT INTO iconset_published_versions (
                iconset_id,
                iconset_version,
                name,
                updated_at,
                checksum
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                bundle.iconsetId,
                bundle.iconsetVersion,
                bundle.name,
                self._serialize_dt(bundle.updatedAt),
                bundle.checksum,
            ),
        )
        conn.executemany(
            """
            INSERT INTO iconset_published_entries (iconset_id, iconset_version, type_key, icon_name)
            VALUES (?, ?, ?, ?)
            """,
            [
                (bundle.iconsetId, bundle.iconsetVersion, key, icon)
                for key, icon in bundle.entries.items()
            ],
        )

    def _load_entries(self, conn: sqlite3.Connection, iconset_id: str) -> dict[str, str]:
        rows = conn.execute(
            """
            SELECT type_key, icon_name
            FROM iconset_draft_entries
            WHERE iconset_id = ?
            ORDER BY type_key ASC
            """,
            (iconset_id,),
        ).fetchall()
        return {str(row["type_key"]): str(row["icon_name"]) for row in rows}

    def _load_published_entries(
        self,
        conn: sqlite3.Connection,
        iconset_id: str,
        iconset_version: int,
    ) -> dict[str, str]:
        rows = conn.execute(
            """
            SELECT type_key, icon_name
            FROM iconset_published_entries
            WHERE iconset_id = ?
              AND iconset_version = ?
            ORDER BY type_key ASC
            """,
            (iconset_id, iconset_version),
        ).fetchall()
        return {str(row["type_key"]): str(row["icon_name"]) for row in rows}

    def _load_draft_bundle(self, conn: sqlite3.Connection, iconset_id: str) -> IconsetBundleV1:
        row = conn.execute(
            """
            SELECT name, draft_version, draft_updated_at, draft_checksum
            FROM iconsets
            WHERE iconset_id = ?
            """,
            (iconset_id,),
        ).fetchone()
        if row is None:
            raise IconsetStoreError(
                status_code=404,
                code="ICONSET_NOT_FOUND",
                message=f"Iconset '{iconset_id}' was not found.",
            )

        entries = self._load_entries(conn, iconset_id)
        payload = {
            "schemaVersion": "v1",
            "iconsetId": iconset_id,
            "iconsetVersion": int(row["draft_version"]),
            "name": str(row["name"]),
            "entries": entries,
            "updatedAt": self._parse_dt(str(row["draft_updated_at"])),
            "checksum": str(row["draft_checksum"]),
        }
        return IconsetBundleV1.model_validate(payload)

    def _load_published_bundles(self, conn: sqlite3.Connection, iconset_id: str) -> list[IconsetBundleV1]:
        rows = conn.execute(
            """
            SELECT iconset_version, name, updated_at, checksum
            FROM iconset_published_versions
            WHERE iconset_id = ?
            ORDER BY iconset_version ASC
            """,
            (iconset_id,),
        ).fetchall()

        bundles: list[IconsetBundleV1] = []
        for row in rows:
            version = int(row["iconset_version"])
            bundles.append(
                IconsetBundleV1.model_validate(
                    {
                        "schemaVersion": "v1",
                        "iconsetId": iconset_id,
                        "iconsetVersion": version,
                        "name": str(row["name"]),
                        "entries": self._load_published_entries(conn, iconset_id, version),
                        "updatedAt": self._parse_dt(str(row["updated_at"])),
                        "checksum": str(row["checksum"]),
                    }
                )
            )

        return bundles

    @staticmethod
    def _serialize_dt(value: datetime) -> str:
        return value.isoformat()

    @staticmethod
    def _parse_dt(value: str) -> datetime:
        return datetime.fromisoformat(value)
