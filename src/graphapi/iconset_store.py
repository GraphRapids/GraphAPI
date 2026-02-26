from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import ValidationError

from .profile_v2_contract import (
    IconsetBundleV1,
    IconsetCreateRequestV1,
    IconsetEditableFieldsV1,
    IconsetListResponseV1,
    IconsetRecordV1,
    IconsetStoreDocumentV1,
    IconsetSummaryV1,
    IconsetUpdateRequestV1,
    _StoredIconsetDocument,
    compute_iconset_checksum,
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

    @classmethod
    def from_env(cls) -> "IconsetStore":
        raw = os.getenv("GRAPHAPI_ICONSET_STORE_PATH", "").strip()
        if raw:
            return cls(Path(raw).expanduser())
        return cls(Path.home() / ".cache" / "graphapi" / "iconsets.v1.json")

    def ensure_default_iconset(self, request: IconsetCreateRequestV1) -> None:
        with self._lock:
            document = self._load_document()
            if request.iconsetId in document.iconsets:
                return
            bundle = self._build_bundle(
                iconset_id=request.iconsetId,
                iconset_version=1,
                editable=request,
            )
            document.iconsets[request.iconsetId] = _StoredIconsetDocument(
                iconsetId=request.iconsetId,
                draft=bundle,
                publishedVersions=[bundle.model_copy(deep=True)],
            )
            self._write_document(document)

    def list_iconsets(self) -> IconsetListResponseV1:
        with self._lock:
            document = self._load_document()
            summaries: list[IconsetSummaryV1] = []
            for iconset_id in sorted(document.iconsets.keys()):
                stored = document.iconsets[iconset_id]
                latest_published = self._latest_published(stored)
                summaries.append(
                    IconsetSummaryV1(
                        iconsetId=iconset_id,
                        name=stored.draft.name,
                        draftVersion=stored.draft.iconsetVersion,
                        publishedVersion=(
                            latest_published.iconsetVersion if latest_published else None
                        ),
                        updatedAt=stored.draft.updatedAt,
                        checksum=stored.draft.checksum,
                    )
                )
            return IconsetListResponseV1(iconsets=summaries)

    def get_iconset(self, iconset_id: str) -> IconsetRecordV1:
        with self._lock:
            document = self._load_document()
            stored = document.iconsets.get(iconset_id)
            if stored is None:
                raise IconsetStoreError(
                    status_code=404,
                    code="ICONSET_NOT_FOUND",
                    message=f"Iconset '{iconset_id}' was not found.",
                )
            return IconsetRecordV1(
                iconsetId=iconset_id,
                draft=stored.draft.model_copy(deep=True),
                publishedVersions=[
                    bundle.model_copy(deep=True)
                    for bundle in self._sorted_published(stored.publishedVersions)
                ],
            )

    def create_iconset(self, request: IconsetCreateRequestV1) -> IconsetRecordV1:
        with self._lock:
            document = self._load_document()
            if request.iconsetId in document.iconsets:
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
            document.iconsets[request.iconsetId] = _StoredIconsetDocument(
                iconsetId=request.iconsetId,
                draft=bundle,
                publishedVersions=[],
            )
            self._write_document(document)
            return self.get_iconset(request.iconsetId)

    def update_iconset(
        self,
        iconset_id: str,
        request: IconsetUpdateRequestV1,
    ) -> IconsetRecordV1:
        with self._lock:
            document = self._load_document()
            stored = document.iconsets.get(iconset_id)
            if stored is None:
                raise IconsetStoreError(
                    status_code=404,
                    code="ICONSET_NOT_FOUND",
                    message=f"Iconset '{iconset_id}' was not found.",
                )

            next_version = int(stored.draft.iconsetVersion) + 1
            stored.draft = self._build_bundle(
                iconset_id=iconset_id,
                iconset_version=next_version,
                editable=request,
            )
            document.iconsets[iconset_id] = stored
            self._write_document(document)
            return self.get_iconset(iconset_id)

    def publish_iconset(self, iconset_id: str) -> IconsetBundleV1:
        with self._lock:
            document = self._load_document()
            stored = document.iconsets.get(iconset_id)
            if stored is None:
                raise IconsetStoreError(
                    status_code=404,
                    code="ICONSET_NOT_FOUND",
                    message=f"Iconset '{iconset_id}' was not found.",
                )

            draft = stored.draft
            if any(
                bundle.iconsetVersion == draft.iconsetVersion
                for bundle in stored.publishedVersions
            ):
                raise IconsetStoreError(
                    status_code=409,
                    code="ICONSET_VERSION_ALREADY_PUBLISHED",
                    message=(
                        f"Iconset '{iconset_id}' version {draft.iconsetVersion} is already published."
                    ),
                )

            stored.publishedVersions.append(draft.model_copy(deep=True))
            document.iconsets[iconset_id] = stored
            self._write_document(document)
            return draft.model_copy(deep=True)

    def get_bundle(
        self,
        iconset_id: str,
        *,
        stage: Literal["draft", "published"] = "published",
        iconset_version: int | None = None,
    ) -> IconsetBundleV1:
        with self._lock:
            document = self._load_document()
            stored = document.iconsets.get(iconset_id)
            if stored is None:
                raise IconsetStoreError(
                    status_code=404,
                    code="ICONSET_NOT_FOUND",
                    message=f"Iconset '{iconset_id}' was not found.",
                )

            if stage == "draft":
                draft = stored.draft
                if iconset_version is not None and draft.iconsetVersion != iconset_version:
                    raise IconsetStoreError(
                        status_code=404,
                        code="ICONSET_VERSION_NOT_FOUND",
                        message=(
                            f"Iconset '{iconset_id}' draft version {iconset_version} was not found."
                        ),
                    )
                return draft.model_copy(deep=True)

            published = self._sorted_published(stored.publishedVersions)
            if not published:
                raise IconsetStoreError(
                    status_code=404,
                    code="ICONSET_NOT_PUBLISHED",
                    message=f"Iconset '{iconset_id}' has no published version.",
                )

            if iconset_version is None:
                return published[-1].model_copy(deep=True)

            for bundle in published:
                if bundle.iconsetVersion == iconset_version:
                    return bundle.model_copy(deep=True)

            raise IconsetStoreError(
                status_code=404,
                code="ICONSET_VERSION_NOT_FOUND",
                message=(
                    f"Iconset '{iconset_id}' published version {iconset_version} was not found."
                ),
            )

    def _latest_published(
        self, stored: _StoredIconsetDocument
    ) -> IconsetBundleV1 | None:
        published = self._sorted_published(stored.publishedVersions)
        return published[-1] if published else None

    @staticmethod
    def _sorted_published(published: list[IconsetBundleV1]) -> list[IconsetBundleV1]:
        return sorted(published, key=lambda item: item.iconsetVersion)

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

    def _load_document(self) -> IconsetStoreDocumentV1:
        if not self._storage_path.exists():
            return IconsetStoreDocumentV1()

        try:
            raw = self._storage_path.read_text(encoding="utf-8")
            if not raw.strip():
                return IconsetStoreDocumentV1()
            data = json.loads(raw)
            document = IconsetStoreDocumentV1.model_validate(data)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise IconsetStoreError(
                status_code=500,
                code="ICONSET_STORAGE_CORRUPTED",
                message="Iconset storage file is unreadable or invalid.",
            ) from exc

        for key, stored in document.iconsets.items():
            if key != stored.iconsetId:
                raise IconsetStoreError(
                    status_code=500,
                    code="ICONSET_STORAGE_CORRUPTED",
                    message="Iconset storage contains mismatched iconset ids.",
                )

        return document

    def _write_document(self, document: IconsetStoreDocumentV1) -> None:
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(document.model_dump(mode="json"), indent=2) + "\n",
                encoding="utf-8",
            )
            tmp_path.replace(self._storage_path)
        except OSError as exc:
            raise IconsetStoreError(
                status_code=500,
                code="ICONSET_STORAGE_WRITE_FAILED",
                message="Failed to persist iconset storage.",
            ) from exc
