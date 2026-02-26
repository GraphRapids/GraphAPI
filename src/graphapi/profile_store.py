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

from .iconset_store import IconsetStore, IconsetStoreError
from .profile_contract import (
    AutocompleteCatalogResponseV1,
    IconsetSourceRefV1,
    NodeTypeSourceV1,
    ProfileBundleV1,
    ProfileCreateRequestV1,
    ProfileEditableFieldsV1,
    ProfileIconsetResolutionResponseV1,
    ProfileListResponseV1,
    ProfileRecordV1,
    ProfileSummaryV1,
    ProfileUpdateRequestV1,
    compute_autocomplete_checksum,
    compute_iconset_resolution_checksum,
    compute_profile_checksum,
    normalize_type_key,
    utcnow,
)


class ProfileStoreError(Exception):
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


class ProfileStore:
    def __init__(self, storage_path: Path, iconset_store: IconsetStore) -> None:
        self._storage_path = storage_path
        self._iconset_store = iconset_store
        self._lock = RLock()
        self._schema_ready = False

    @classmethod
    def from_env(cls, iconset_store: IconsetStore) -> "ProfileStore":
        raw = os.getenv("GRAPHAPI_RUNTIME_DB_PATH", "").strip()
        if not raw:
            raw = os.getenv("GRAPHAPI_PROFILE_STORE_PATH", "").strip()
        if raw:
            return cls(Path(raw).expanduser(), iconset_store)
        return cls(Path.home() / ".cache" / "graphapi" / "runtime.v1.sqlite3", iconset_store)

    def ensure_default_profile(self, request: ProfileCreateRequestV1) -> None:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT 1 FROM profiles WHERE profile_id = ?",
                    (request.profileId,),
                ).fetchone()
                if row is not None:
                    return

                bundle = self._build_bundle(
                    profile_id=request.profileId,
                    profile_version=1,
                    editable=request,
                )
                self._insert_profile(conn, bundle, publish=True)

    def list_profiles(self) -> ProfileListResponseV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                rows = conn.execute(
                    """
                    SELECT
                        p.profile_id,
                        p.name,
                        p.draft_version,
                        p.draft_updated_at,
                        p.draft_checksum,
                        p.draft_iconset_resolution_checksum,
                        (
                            SELECT MAX(v.profile_version)
                            FROM profile_published_versions v
                            WHERE v.profile_id = p.profile_id
                        ) AS published_version
                    FROM profiles p
                    ORDER BY p.profile_id ASC
                    """
                ).fetchall()

                summaries = [
                    ProfileSummaryV1(
                        profileId=str(row["profile_id"]),
                        name=str(row["name"]),
                        draftVersion=int(row["draft_version"]),
                        publishedVersion=(
                            int(row["published_version"]) if row["published_version"] is not None else None
                        ),
                        updatedAt=self._parse_dt(str(row["draft_updated_at"])),
                        checksum=str(row["draft_checksum"]),
                        iconsetResolutionChecksum=str(row["draft_iconset_resolution_checksum"]),
                    )
                    for row in rows
                ]
                return ProfileListResponseV1(profiles=summaries)

    def get_profile(self, profile_id: str) -> ProfileRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, profile_id)
                published_rows = conn.execute(
                    """
                    SELECT payload
                    FROM profile_published_versions
                    WHERE profile_id = ?
                    ORDER BY profile_version ASC
                    """,
                    (profile_id,),
                ).fetchall()
                published = [self._bundle_from_json(str(row["payload"])) for row in published_rows]
                return ProfileRecordV1(
                    profileId=profile_id,
                    draft=draft,
                    publishedVersions=published,
                )

    def create_profile(self, request: ProfileCreateRequestV1) -> ProfileRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                exists = conn.execute(
                    "SELECT 1 FROM profiles WHERE profile_id = ?",
                    (request.profileId,),
                ).fetchone()
                if exists is not None:
                    raise ProfileStoreError(
                        status_code=409,
                        code="PROFILE_ALREADY_EXISTS",
                        message=f"Profile '{request.profileId}' already exists.",
                    )

                bundle = self._build_bundle(
                    profile_id=request.profileId,
                    profile_version=1,
                    editable=request,
                )
                self._insert_profile(conn, bundle, publish=False)

        return self.get_profile(request.profileId)

    def update_profile(
        self,
        profile_id: str,
        request: ProfileUpdateRequestV1,
    ) -> ProfileRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT draft_version FROM profiles WHERE profile_id = ?",
                    (profile_id,),
                ).fetchone()
                if row is None:
                    raise ProfileStoreError(
                        status_code=404,
                        code="PROFILE_NOT_FOUND",
                        message=f"Profile '{profile_id}' was not found.",
                    )

                next_version = int(row["draft_version"]) + 1
                bundle = self._build_bundle(
                    profile_id=profile_id,
                    profile_version=next_version,
                    editable=request,
                )
                self._replace_draft(conn, bundle)

        return self.get_profile(profile_id)

    def publish_profile(self, profile_id: str) -> ProfileBundleV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, profile_id)
                exists = conn.execute(
                    """
                    SELECT 1
                    FROM profile_published_versions
                    WHERE profile_id = ?
                      AND profile_version = ?
                    """,
                    (profile_id, draft.profileVersion),
                ).fetchone()
                if exists is not None:
                    raise ProfileStoreError(
                        status_code=409,
                        code="PROFILE_VERSION_ALREADY_PUBLISHED",
                        message=(
                            f"Profile '{profile_id}' version {draft.profileVersion} is already published."
                        ),
                    )

                conn.execute(
                    """
                    INSERT INTO profile_published_versions (
                        profile_id,
                        profile_version,
                        updated_at,
                        checksum,
                        iconset_resolution_checksum,
                        payload
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        draft.profileId,
                        draft.profileVersion,
                        self._serialize_dt(draft.updatedAt),
                        draft.checksum,
                        draft.iconsetResolutionChecksum,
                        self._bundle_to_json(draft),
                    ),
                )
                return draft

    def get_bundle(
        self,
        profile_id: str,
        *,
        stage: Literal["draft", "published"] = "published",
        profile_version: int | None = None,
    ) -> ProfileBundleV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)

                if stage == "draft":
                    draft = self._load_draft_bundle(conn, profile_id)
                    if profile_version is not None and draft.profileVersion != profile_version:
                        raise ProfileStoreError(
                            status_code=404,
                            code="PROFILE_VERSION_NOT_FOUND",
                            message=(
                                f"Profile '{profile_id}' draft version {profile_version} was not found."
                            ),
                        )
                    return draft

                rows = conn.execute(
                    """
                    SELECT profile_version, payload
                    FROM profile_published_versions
                    WHERE profile_id = ?
                    ORDER BY profile_version ASC
                    """,
                    (profile_id,),
                ).fetchall()

                if not rows:
                    self._assert_profile_exists(conn, profile_id)
                    raise ProfileStoreError(
                        status_code=404,
                        code="PROFILE_NOT_PUBLISHED",
                        message=f"Profile '{profile_id}' has no published version.",
                    )

                selected = None
                if profile_version is None:
                    selected = rows[-1]
                else:
                    for row in rows:
                        if int(row["profile_version"]) == profile_version:
                            selected = row
                            break
                    if selected is None:
                        raise ProfileStoreError(
                            status_code=404,
                            code="PROFILE_VERSION_NOT_FOUND",
                            message=(
                                f"Profile '{profile_id}' published version {profile_version} was not found."
                            ),
                        )

                return self._bundle_from_json(str(selected["payload"]))

    def get_iconset_resolution(
        self,
        profile_id: str,
        *,
        stage: Literal["draft", "published"] = "published",
        profile_version: int | None = None,
    ) -> ProfileIconsetResolutionResponseV1:
        bundle = self.get_bundle(
            profile_id,
            stage=stage,
            profile_version=profile_version,
        )

        resolved_entries, sources, key_sources, checksum = self._resolve_iconsets(
            bundle.iconsetRefs,
            bundle.iconConflictPolicy,
        )

        return ProfileIconsetResolutionResponseV1(
            profileId=bundle.profileId,
            profileVersion=bundle.profileVersion,
            profileChecksum=bundle.checksum,
            conflictPolicy=bundle.iconConflictPolicy,
            resolvedEntries=resolved_entries,
            sources=sources,
            keySources=key_sources,
            checksum=checksum,
        )

    def get_autocomplete_catalog(
        self,
        profile_id: str,
        *,
        stage: Literal["draft", "published"] = "published",
        profile_version: int | None = None,
    ) -> AutocompleteCatalogResponseV1:
        bundle = self.get_bundle(
            profile_id,
            stage=stage,
            profile_version=profile_version,
        )

        return AutocompleteCatalogResponseV1(
            profileId=bundle.profileId,
            profileVersion=bundle.profileVersion,
            profileChecksum=bundle.checksum,
            iconsetResolutionChecksum=bundle.iconsetResolutionChecksum,
            checksum=compute_autocomplete_checksum(bundle),
            nodeTypes=bundle.nodeTypes,
            linkTypes=bundle.linkTypes,
        )

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
            CREATE TABLE IF NOT EXISTS profiles (
                profile_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                draft_version INTEGER NOT NULL,
                draft_updated_at TEXT NOT NULL,
                draft_checksum TEXT NOT NULL,
                draft_iconset_resolution_checksum TEXT NOT NULL,
                draft_payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS profile_published_versions (
                profile_id TEXT NOT NULL,
                profile_version INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                checksum TEXT NOT NULL,
                iconset_resolution_checksum TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (profile_id, profile_version),
                FOREIGN KEY (profile_id) REFERENCES profiles(profile_id) ON DELETE CASCADE
            );
            """
        )
        self._schema_ready = True

    def _assert_profile_exists(self, conn: sqlite3.Connection, profile_id: str) -> None:
        row = conn.execute(
            "SELECT 1 FROM profiles WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise ProfileStoreError(
                status_code=404,
                code="PROFILE_NOT_FOUND",
                message=f"Profile '{profile_id}' was not found.",
            )

    def _validate_elk_settings(self, elk_settings: dict) -> dict:
        try:
            canonical = dict(elk_settings)
            canonical["type_icon_map"] = {}
            normalized = ElkSettings.model_validate(canonical)
        except ValidationError as exc:
            raise ProfileStoreError(
                status_code=400,
                code="INVALID_ELK_SETTINGS",
                message="elkSettings failed GraphLoom validation.",
                details={"errors": exc.errors()},
            ) from exc
        return normalized.model_dump(by_alias=True, exclude_none=True, mode="json")

    def _resolve_iconsets(
        self,
        refs,
        conflict_policy,
    ) -> tuple[dict[str, str], list[IconsetSourceRefV1], dict[str, NodeTypeSourceV1], str]:
        resolved_entries: dict[str, str] = {}
        source_refs: list[IconsetSourceRefV1] = []
        key_sources_payload: dict[str, dict] = {}

        for ref in refs:
            try:
                bundle = self._iconset_store.get_bundle(
                    ref.iconsetId,
                    stage="published",
                    iconset_version=ref.iconsetVersion,
                )
            except IconsetStoreError as exc:
                if exc.code in {
                    "ICONSET_NOT_FOUND",
                    "ICONSET_NOT_PUBLISHED",
                    "ICONSET_VERSION_NOT_FOUND",
                }:
                    raise ProfileStoreError(
                        status_code=404,
                        code="PROFILE_ICONSET_REF_INVALID",
                        message=(
                            f"Profile iconset reference '{ref.iconsetId}@{ref.iconsetVersion}' could not be resolved."
                        ),
                        details={
                            "iconsetId": ref.iconsetId,
                            "iconsetVersion": ref.iconsetVersion,
                            "cause": exc.code,
                        },
                    ) from exc
                raise ProfileStoreError(
                    status_code=500,
                    code="ICONSET_RESOLUTION_FAILED",
                    message="Failed to resolve iconset references.",
                ) from exc

            if ref.checksum and ref.checksum != bundle.checksum:
                raise ProfileStoreError(
                    status_code=409,
                    code="PROFILE_ICONSET_REF_INVALID",
                    message=(
                        f"Profile iconset reference '{ref.iconsetId}@{ref.iconsetVersion}' checksum mismatch."
                    ),
                    details={
                        "iconsetId": ref.iconsetId,
                        "iconsetVersion": ref.iconsetVersion,
                        "expectedChecksum": ref.checksum,
                        "actualChecksum": bundle.checksum,
                    },
                )

            source = IconsetSourceRefV1(
                iconsetId=bundle.iconsetId,
                iconsetVersion=bundle.iconsetVersion,
                checksum=bundle.checksum,
            )
            source_refs.append(source)

            for key, icon in bundle.entries.items():
                normalized_key = normalize_type_key(key)
                existing_icon = resolved_entries.get(normalized_key)
                source_payload = source.model_dump()

                if normalized_key not in key_sources_payload:
                    key_sources_payload[normalized_key] = {
                        "key": normalized_key,
                        "icon": icon,
                        "selectedFrom": source_payload,
                        "candidates": [source_payload],
                    }
                else:
                    key_sources_payload[normalized_key]["candidates"].append(source_payload)

                if existing_icon is None:
                    resolved_entries[normalized_key] = icon
                    continue

                if existing_icon == icon:
                    continue

                if conflict_policy == "reject":
                    raise ProfileStoreError(
                        status_code=409,
                        code="ICONSET_KEY_CONFLICT",
                        message=(
                            f"Node type key '{normalized_key}' maps to multiple icons under reject policy."
                        ),
                        details={
                            "key": normalized_key,
                            "existingIcon": existing_icon,
                            "incomingIcon": icon,
                            "conflictPolicy": conflict_policy,
                        },
                    )

                if conflict_policy == "last-wins":
                    resolved_entries[normalized_key] = icon
                    key_sources_payload[normalized_key]["icon"] = icon
                    key_sources_payload[normalized_key]["selectedFrom"] = source_payload

        resolved_entries = dict(sorted(resolved_entries.items(), key=lambda item: item[0]))
        if not resolved_entries:
            raise ProfileStoreError(
                status_code=400,
                code="PROFILE_ICONSET_REF_INVALID",
                message="Resolved iconset map is empty.",
            )

        key_sources = {
            key: NodeTypeSourceV1.model_validate(payload)
            for key, payload in sorted(key_sources_payload.items(), key=lambda item: item[0])
        }

        checksum = compute_iconset_resolution_checksum(
            conflict_policy=conflict_policy,
            sources=[item.model_dump() for item in source_refs],
            resolved_entries=resolved_entries,
        )

        return resolved_entries, source_refs, key_sources, checksum

    def _build_bundle(
        self,
        *,
        profile_id: str,
        profile_version: int,
        editable: ProfileEditableFieldsV1,
    ) -> ProfileBundleV1:
        normalized_elk_settings = self._validate_elk_settings(editable.elkSettings)
        resolved_entries, source_refs, _key_sources, resolution_checksum = self._resolve_iconsets(
            editable.iconsetRefs,
            editable.iconConflictPolicy,
        )

        timestamp = utcnow()
        payload = {
            "schemaVersion": "v1",
            "profileId": profile_id,
            "profileVersion": profile_version,
            "name": editable.name,
            "linkTypes": editable.linkTypes,
            "elkSettings": normalized_elk_settings,
            "iconsetRefs": [
                {
                    "iconsetId": source.iconsetId,
                    "iconsetVersion": source.iconsetVersion,
                    "checksum": source.checksum,
                }
                for source in source_refs
            ],
            "iconConflictPolicy": editable.iconConflictPolicy,
            "typeIconMap": resolved_entries,
            "nodeTypes": sorted(resolved_entries.keys()),
            "iconsetResolutionChecksum": resolution_checksum,
            "updatedAt": timestamp,
        }
        payload["checksum"] = compute_profile_checksum(payload)
        return ProfileBundleV1.model_validate(payload)

    def _insert_profile(
        self,
        conn: sqlite3.Connection,
        bundle: ProfileBundleV1,
        *,
        publish: bool,
    ) -> None:
        conn.execute(
            """
            INSERT INTO profiles (
                profile_id,
                name,
                draft_version,
                draft_updated_at,
                draft_checksum,
                draft_iconset_resolution_checksum,
                draft_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bundle.profileId,
                bundle.name,
                bundle.profileVersion,
                self._serialize_dt(bundle.updatedAt),
                bundle.checksum,
                bundle.iconsetResolutionChecksum,
                self._bundle_to_json(bundle),
            ),
        )
        if publish:
            conn.execute(
                """
                INSERT INTO profile_published_versions (
                    profile_id,
                    profile_version,
                    updated_at,
                    checksum,
                    iconset_resolution_checksum,
                    payload
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    bundle.profileId,
                    bundle.profileVersion,
                    self._serialize_dt(bundle.updatedAt),
                    bundle.checksum,
                    bundle.iconsetResolutionChecksum,
                    self._bundle_to_json(bundle),
                ),
            )

    def _replace_draft(self, conn: sqlite3.Connection, bundle: ProfileBundleV1) -> None:
        conn.execute(
            """
            UPDATE profiles
            SET
                name = ?,
                draft_version = ?,
                draft_updated_at = ?,
                draft_checksum = ?,
                draft_iconset_resolution_checksum = ?,
                draft_payload = ?
            WHERE profile_id = ?
            """,
            (
                bundle.name,
                bundle.profileVersion,
                self._serialize_dt(bundle.updatedAt),
                bundle.checksum,
                bundle.iconsetResolutionChecksum,
                self._bundle_to_json(bundle),
                bundle.profileId,
            ),
        )

    def _load_draft_bundle(self, conn: sqlite3.Connection, profile_id: str) -> ProfileBundleV1:
        row = conn.execute(
            "SELECT draft_payload FROM profiles WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise ProfileStoreError(
                status_code=404,
                code="PROFILE_NOT_FOUND",
                message=f"Profile '{profile_id}' was not found.",
            )
        return self._bundle_from_json(str(row["draft_payload"]))

    @staticmethod
    def _bundle_to_json(bundle: ProfileBundleV1) -> str:
        return json.dumps(bundle.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _bundle_from_json(raw: str) -> ProfileBundleV1:
        try:
            payload = json.loads(raw)
            return ProfileBundleV1.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ProfileStoreError(
                status_code=500,
                code="PROFILE_STORAGE_CORRUPTED",
                message="Profile storage payload is unreadable or invalid.",
            ) from exc

    @staticmethod
    def _serialize_dt(value) -> str:
        return value.isoformat()

    @staticmethod
    def _parse_dt(value: str):
        return datetime.fromisoformat(value)
