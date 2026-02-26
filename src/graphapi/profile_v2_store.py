from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Literal

from graphloom import ElkSettings
from pydantic import ValidationError

from .iconset_store import IconsetStore, IconsetStoreError
from .profile_v2_contract import (
    AutocompleteCatalogResponseV2,
    IconsetSourceRefV1,
    NodeTypeSourceV1,
    ProfileBundleV2,
    ProfileCreateRequestV2,
    ProfileEditableFieldsV2,
    ProfileIconsetResolutionResponseV1,
    ProfileListResponseV2,
    ProfileRecordV2,
    ProfileStoreDocumentV2,
    ProfileSummaryV2,
    ProfileUpdateRequestV2,
    _StoredProfileV2Document,
    compute_autocomplete_v2_checksum,
    compute_iconset_resolution_checksum,
    compute_profile_v2_checksum,
    normalize_type_key,
    utcnow,
)


class ProfileStoreV2Error(Exception):
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


class ProfileStoreV2:
    def __init__(self, storage_path: Path, iconset_store: IconsetStore) -> None:
        self._storage_path = storage_path
        self._iconset_store = iconset_store
        self._lock = RLock()

    @classmethod
    def from_env(cls, iconset_store: IconsetStore) -> "ProfileStoreV2":
        raw = os.getenv("GRAPHAPI_PROFILE_V2_STORE_PATH", "").strip()
        if raw:
            return cls(Path(raw).expanduser(), iconset_store)
        return cls(Path.home() / ".cache" / "graphapi" / "profiles.v2.json", iconset_store)

    def ensure_default_profile(self, request: ProfileCreateRequestV2) -> None:
        with self._lock:
            document = self._load_document()
            if request.profileId in document.profiles:
                return
            bundle = self._build_bundle(
                profile_id=request.profileId,
                profile_version=1,
                editable=request,
            )
            document.profiles[request.profileId] = _StoredProfileV2Document(
                profileId=request.profileId,
                draft=bundle,
                publishedVersions=[bundle.model_copy(deep=True)],
            )
            self._write_document(document)

    def list_profiles(self) -> ProfileListResponseV2:
        with self._lock:
            document = self._load_document()
            summaries: list[ProfileSummaryV2] = []
            for profile_id in sorted(document.profiles.keys()):
                stored = document.profiles[profile_id]
                latest_published = self._latest_published(stored)
                summaries.append(
                    ProfileSummaryV2(
                        profileId=profile_id,
                        name=stored.draft.name,
                        draftVersion=stored.draft.profileVersion,
                        publishedVersion=(
                            latest_published.profileVersion if latest_published else None
                        ),
                        updatedAt=stored.draft.updatedAt,
                        checksum=stored.draft.checksum,
                        iconsetResolutionChecksum=stored.draft.iconsetResolutionChecksum,
                    )
                )
            return ProfileListResponseV2(profiles=summaries)

    def get_profile(self, profile_id: str) -> ProfileRecordV2:
        with self._lock:
            document = self._load_document()
            stored = document.profiles.get(profile_id)
            if stored is None:
                raise ProfileStoreV2Error(
                    status_code=404,
                    code="PROFILE_NOT_FOUND",
                    message=f"Profile '{profile_id}' was not found.",
                )
            return ProfileRecordV2(
                profileId=profile_id,
                draft=stored.draft.model_copy(deep=True),
                publishedVersions=[
                    bundle.model_copy(deep=True)
                    for bundle in self._sorted_published(stored.publishedVersions)
                ],
            )

    def create_profile(self, request: ProfileCreateRequestV2) -> ProfileRecordV2:
        with self._lock:
            document = self._load_document()
            if request.profileId in document.profiles:
                raise ProfileStoreV2Error(
                    status_code=409,
                    code="PROFILE_ALREADY_EXISTS",
                    message=f"Profile '{request.profileId}' already exists.",
                )

            bundle = self._build_bundle(
                profile_id=request.profileId,
                profile_version=1,
                editable=request,
            )
            document.profiles[request.profileId] = _StoredProfileV2Document(
                profileId=request.profileId,
                draft=bundle,
                publishedVersions=[],
            )
            self._write_document(document)
            return self.get_profile(request.profileId)

    def update_profile(
        self,
        profile_id: str,
        request: ProfileUpdateRequestV2,
    ) -> ProfileRecordV2:
        with self._lock:
            document = self._load_document()
            stored = document.profiles.get(profile_id)
            if stored is None:
                raise ProfileStoreV2Error(
                    status_code=404,
                    code="PROFILE_NOT_FOUND",
                    message=f"Profile '{profile_id}' was not found.",
                )

            next_version = int(stored.draft.profileVersion) + 1
            stored.draft = self._build_bundle(
                profile_id=profile_id,
                profile_version=next_version,
                editable=request,
            )
            document.profiles[profile_id] = stored
            self._write_document(document)
            return self.get_profile(profile_id)

    def publish_profile(self, profile_id: str) -> ProfileBundleV2:
        with self._lock:
            document = self._load_document()
            stored = document.profiles.get(profile_id)
            if stored is None:
                raise ProfileStoreV2Error(
                    status_code=404,
                    code="PROFILE_NOT_FOUND",
                    message=f"Profile '{profile_id}' was not found.",
                )

            draft = stored.draft
            if any(
                bundle.profileVersion == draft.profileVersion
                for bundle in stored.publishedVersions
            ):
                raise ProfileStoreV2Error(
                    status_code=409,
                    code="PROFILE_VERSION_ALREADY_PUBLISHED",
                    message=(
                        f"Profile '{profile_id}' version {draft.profileVersion} is already published."
                    ),
                )

            stored.publishedVersions.append(draft.model_copy(deep=True))
            document.profiles[profile_id] = stored
            self._write_document(document)
            return draft.model_copy(deep=True)

    def get_bundle(
        self,
        profile_id: str,
        *,
        stage: Literal["draft", "published"] = "published",
        profile_version: int | None = None,
    ) -> ProfileBundleV2:
        with self._lock:
            document = self._load_document()
            stored = document.profiles.get(profile_id)
            if stored is None:
                raise ProfileStoreV2Error(
                    status_code=404,
                    code="PROFILE_NOT_FOUND",
                    message=f"Profile '{profile_id}' was not found.",
                )

            if stage == "draft":
                draft = stored.draft
                if profile_version is not None and draft.profileVersion != profile_version:
                    raise ProfileStoreV2Error(
                        status_code=404,
                        code="PROFILE_VERSION_NOT_FOUND",
                        message=(
                            f"Profile '{profile_id}' draft version {profile_version} was not found."
                        ),
                    )
                return draft.model_copy(deep=True)

            published = self._sorted_published(stored.publishedVersions)
            if not published:
                raise ProfileStoreV2Error(
                    status_code=404,
                    code="PROFILE_NOT_PUBLISHED",
                    message=f"Profile '{profile_id}' has no published version.",
                )

            if profile_version is None:
                return published[-1].model_copy(deep=True)

            for bundle in published:
                if bundle.profileVersion == profile_version:
                    return bundle.model_copy(deep=True)

            raise ProfileStoreV2Error(
                status_code=404,
                code="PROFILE_VERSION_NOT_FOUND",
                message=(
                    f"Profile '{profile_id}' published version {profile_version} was not found."
                ),
            )

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
    ) -> AutocompleteCatalogResponseV2:
        bundle = self.get_bundle(
            profile_id,
            stage=stage,
            profile_version=profile_version,
        )

        return AutocompleteCatalogResponseV2(
            profileId=bundle.profileId,
            profileVersion=bundle.profileVersion,
            profileChecksum=bundle.checksum,
            iconsetResolutionChecksum=bundle.iconsetResolutionChecksum,
            checksum=compute_autocomplete_v2_checksum(bundle),
            nodeTypes=bundle.nodeTypes,
            linkTypes=bundle.linkTypes,
        )

    @staticmethod
    def _sorted_published(published: list[ProfileBundleV2]) -> list[ProfileBundleV2]:
        return sorted(published, key=lambda item: item.profileVersion)

    def _latest_published(
        self, stored: _StoredProfileV2Document
    ) -> ProfileBundleV2 | None:
        published = self._sorted_published(stored.publishedVersions)
        return published[-1] if published else None

    def _validate_elk_settings(self, elk_settings: dict) -> dict:
        try:
            canonical = dict(elk_settings)
            # v2 profiles own icon mappings through iconsets only.
            canonical["type_icon_map"] = {}
            normalized = ElkSettings.model_validate(canonical)
        except ValidationError as exc:
            raise ProfileStoreV2Error(
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
                code = exc.code
                if code in {
                    "ICONSET_NOT_FOUND",
                    "ICONSET_NOT_PUBLISHED",
                    "ICONSET_VERSION_NOT_FOUND",
                }:
                    raise ProfileStoreV2Error(
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
                raise ProfileStoreV2Error(
                    status_code=500,
                    code="ICONSET_RESOLUTION_FAILED",
                    message="Failed to resolve iconset references.",
                ) from exc

            if ref.checksum and ref.checksum != bundle.checksum:
                raise ProfileStoreV2Error(
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
                    raise ProfileStoreV2Error(
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
            raise ProfileStoreV2Error(
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
        editable: ProfileEditableFieldsV2,
    ) -> ProfileBundleV2:
        normalized_elk_settings = self._validate_elk_settings(editable.elkSettings)
        resolved_entries, source_refs, _key_sources, resolution_checksum = self._resolve_iconsets(
            editable.iconsetRefs,
            editable.iconConflictPolicy,
        )

        timestamp = utcnow()
        node_types = sorted(resolved_entries.keys())

        payload = {
            "schemaVersion": "v2",
            "profileId": profile_id,
            "profileVersion": profile_version,
            "name": editable.name,
            "nodeTypes": node_types,
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
            "iconsetResolutionChecksum": resolution_checksum,
            "updatedAt": timestamp,
        }
        payload["checksum"] = compute_profile_v2_checksum(payload)
        return ProfileBundleV2.model_validate(payload)

    def _load_document(self) -> ProfileStoreDocumentV2:
        if not self._storage_path.exists():
            return ProfileStoreDocumentV2()

        try:
            raw = self._storage_path.read_text(encoding="utf-8")
            if not raw.strip():
                return ProfileStoreDocumentV2()
            data = json.loads(raw)
            document = ProfileStoreDocumentV2.model_validate(data)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ProfileStoreV2Error(
                status_code=500,
                code="PROFILE_STORAGE_CORRUPTED",
                message="Profile v2 storage file is unreadable or invalid.",
            ) from exc

        for key, stored in document.profiles.items():
            if key != stored.profileId:
                raise ProfileStoreV2Error(
                    status_code=500,
                    code="PROFILE_STORAGE_CORRUPTED",
                    message="Profile v2 storage contains mismatched profile ids.",
                )

        return document

    def _write_document(self, document: ProfileStoreDocumentV2) -> None:
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(document.model_dump(mode="json"), indent=2) + "\n",
                encoding="utf-8",
            )
            tmp_path.replace(self._storage_path)
        except OSError as exc:
            raise ProfileStoreV2Error(
                status_code=500,
                code="PROFILE_STORAGE_WRITE_FAILED",
                message="Failed to persist profile v2 storage.",
            ) from exc
