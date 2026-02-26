from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Literal

from graphloom import ElkSettings
from pydantic import ValidationError

from .profile_contract import (
    AutocompleteCatalogResponseV1,
    ProfileBundleV1,
    ProfileCreateRequestV1,
    ProfileEditableFieldsV1,
    ProfileListResponseV1,
    ProfileRecordV1,
    ProfileStoreDocumentV1,
    ProfileSummaryV1,
    ProfileUpdateRequestV1,
    _StoredProfileDocument,
    compute_checksum,
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
    def __init__(self, storage_path: Path) -> None:
        self._storage_path = storage_path
        self._lock = RLock()

    @classmethod
    def from_env(cls) -> "ProfileStore":
        raw = os.getenv("GRAPHAPI_PROFILE_STORE_PATH", "").strip()
        if raw:
            return cls(Path(raw).expanduser())
        return cls(Path.home() / ".cache" / "graphapi" / "profiles.v1.json")

    def ensure_default_profile(self, request: ProfileCreateRequestV1) -> None:
        with self._lock:
            document = self._load_document()
            if request.profileId in document.profiles:
                return
            bundle = self._build_bundle(
                profile_id=request.profileId,
                profile_version=1,
                editable=request,
            )
            document.profiles[request.profileId] = _StoredProfileDocument(
                profileId=request.profileId,
                draft=bundle,
                publishedVersions=[bundle.model_copy(deep=True)],
            )
            self._write_document(document)

    def list_profiles(self) -> ProfileListResponseV1:
        with self._lock:
            document = self._load_document()
            summaries: list[ProfileSummaryV1] = []
            for profile_id in sorted(document.profiles.keys()):
                stored = document.profiles[profile_id]
                latest_published = self._latest_published(stored)
                summaries.append(
                    ProfileSummaryV1(
                        profileId=profile_id,
                        name=stored.draft.name,
                        draftVersion=stored.draft.profileVersion,
                        publishedVersion=(
                            latest_published.profileVersion if latest_published else None
                        ),
                        updatedAt=stored.draft.updatedAt,
                        checksum=stored.draft.checksum,
                    )
                )
            return ProfileListResponseV1(profiles=summaries)

    def get_profile(self, profile_id: str) -> ProfileRecordV1:
        with self._lock:
            document = self._load_document()
            stored = document.profiles.get(profile_id)
            if stored is None:
                raise ProfileStoreError(
                    status_code=404,
                    code="PROFILE_NOT_FOUND",
                    message=f"Profile '{profile_id}' was not found.",
                )
            return ProfileRecordV1(
                profileId=profile_id,
                draft=stored.draft.model_copy(deep=True),
                publishedVersions=[
                    bundle.model_copy(deep=True)
                    for bundle in self._sorted_published(stored.publishedVersions)
                ],
            )

    def create_profile(self, request: ProfileCreateRequestV1) -> ProfileRecordV1:
        with self._lock:
            document = self._load_document()
            if request.profileId in document.profiles:
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
            document.profiles[request.profileId] = _StoredProfileDocument(
                profileId=request.profileId,
                draft=bundle,
                publishedVersions=[],
            )
            self._write_document(document)
            return self.get_profile(request.profileId)

    def update_profile(
        self,
        profile_id: str,
        request: ProfileUpdateRequestV1,
    ) -> ProfileRecordV1:
        with self._lock:
            document = self._load_document()
            stored = document.profiles.get(profile_id)
            if stored is None:
                raise ProfileStoreError(
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

    def publish_profile(self, profile_id: str) -> ProfileBundleV1:
        with self._lock:
            document = self._load_document()
            stored = document.profiles.get(profile_id)
            if stored is None:
                raise ProfileStoreError(
                    status_code=404,
                    code="PROFILE_NOT_FOUND",
                    message=f"Profile '{profile_id}' was not found.",
                )

            draft = stored.draft
            if any(
                bundle.profileVersion == draft.profileVersion
                for bundle in stored.publishedVersions
            ):
                raise ProfileStoreError(
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
    ) -> ProfileBundleV1:
        with self._lock:
            document = self._load_document()
            stored = document.profiles.get(profile_id)
            if stored is None:
                raise ProfileStoreError(
                    status_code=404,
                    code="PROFILE_NOT_FOUND",
                    message=f"Profile '{profile_id}' was not found.",
                )

            if stage == "draft":
                draft = stored.draft
                if profile_version is not None and draft.profileVersion != profile_version:
                    raise ProfileStoreError(
                        status_code=404,
                        code="PROFILE_VERSION_NOT_FOUND",
                        message=(
                            f"Profile '{profile_id}' draft version {profile_version} was not found."
                        ),
                    )
                return draft.model_copy(deep=True)

            published = self._sorted_published(stored.publishedVersions)
            if not published:
                raise ProfileStoreError(
                    status_code=404,
                    code="PROFILE_NOT_PUBLISHED",
                    message=f"Profile '{profile_id}' has no published version.",
                )

            if profile_version is None:
                return published[-1].model_copy(deep=True)

            for bundle in published:
                if bundle.profileVersion == profile_version:
                    return bundle.model_copy(deep=True)

            raise ProfileStoreError(
                status_code=404,
                code="PROFILE_VERSION_NOT_FOUND",
                message=(
                    f"Profile '{profile_id}' published version {profile_version} was not found."
                ),
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
            checksum=bundle.checksum,
            nodeTypes=bundle.nodeTypes,
            linkTypes=bundle.linkTypes,
        )

    def _latest_published(
        self, stored: _StoredProfileDocument
    ) -> ProfileBundleV1 | None:
        published = self._sorted_published(stored.publishedVersions)
        return published[-1] if published else None

    @staticmethod
    def _sorted_published(published: list[ProfileBundleV1]) -> list[ProfileBundleV1]:
        return sorted(published, key=lambda item: item.profileVersion)

    def _validate_elk_settings(self, elk_settings: dict) -> dict:
        try:
            normalized = ElkSettings.model_validate(elk_settings)
        except ValidationError as exc:
            raise ProfileStoreError(
                status_code=400,
                code="INVALID_ELK_SETTINGS",
                message="elkSettings failed GraphLoom validation.",
                details={"errors": exc.errors()},
            ) from exc
        return normalized.model_dump(by_alias=True, exclude_none=True, mode="json")

    def _build_bundle(
        self,
        *,
        profile_id: str,
        profile_version: int,
        editable: ProfileEditableFieldsV1,
    ) -> ProfileBundleV1:
        normalized_elk_settings = self._validate_elk_settings(editable.elkSettings)
        timestamp = utcnow()

        payload = {
            "schemaVersion": "v1",
            "profileId": profile_id,
            "profileVersion": profile_version,
            "name": editable.name,
            "nodeTypes": editable.nodeTypes,
            "linkTypes": editable.linkTypes,
            "elkSettings": normalized_elk_settings,
            "renderCss": editable.renderCss,
            "updatedAt": timestamp,
        }
        payload["checksum"] = compute_checksum(payload)
        return ProfileBundleV1.model_validate(payload)

    def _load_document(self) -> ProfileStoreDocumentV1:
        if not self._storage_path.exists():
            return ProfileStoreDocumentV1()

        try:
            raw = self._storage_path.read_text(encoding="utf-8")
            if not raw.strip():
                return ProfileStoreDocumentV1()
            data = json.loads(raw)
            document = ProfileStoreDocumentV1.model_validate(data)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ProfileStoreError(
                status_code=500,
                code="PROFILE_STORAGE_CORRUPTED",
                message="Profile storage file is unreadable or invalid.",
            ) from exc

        # Defensive normalization in case users edited the file manually.
        for key, stored in document.profiles.items():
            if key != stored.profileId:
                raise ProfileStoreError(
                    status_code=500,
                    code="PROFILE_STORAGE_CORRUPTED",
                    message="Profile storage contains mismatched profile ids.",
                )

        return document

    def _write_document(self, document: ProfileStoreDocumentV1) -> None:
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(document.model_dump(mode="json"), indent=2) + "\n",
                encoding="utf-8",
            )
            tmp_path.replace(self._storage_path)
        except OSError as exc:
            raise ProfileStoreError(
                status_code=500,
                code="PROFILE_STORAGE_WRITE_FAILED",
                message="Failed to persist profile storage.",
            ) from exc
