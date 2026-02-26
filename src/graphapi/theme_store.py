from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import ValidationError

from .profile_contract import (
    ThemeBundleV1,
    ThemeCreateRequestV1,
    ThemeEditableFieldsV1,
    ThemeListResponseV1,
    ThemeRecordV1,
    ThemeStoreDocumentV1,
    ThemeSummaryV1,
    ThemeUpdateRequestV1,
    _StoredThemeDocument,
    compute_theme_checksum,
    utcnow,
)


class ThemeStoreError(Exception):
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


class ThemeStore:
    def __init__(self, storage_path: Path) -> None:
        self._storage_path = storage_path
        self._lock = RLock()

    @classmethod
    def from_env(cls) -> "ThemeStore":
        raw = os.getenv("GRAPHAPI_THEME_STORE_PATH", "").strip()
        if raw:
            return cls(Path(raw).expanduser())
        return cls(Path.home() / ".cache" / "graphapi" / "themes.v1.json")

    def ensure_default_theme(self, request: ThemeCreateRequestV1) -> None:
        with self._lock:
            document = self._load_document()
            if request.themeId in document.themes:
                return
            bundle = self._build_bundle(
                theme_id=request.themeId,
                theme_version=1,
                editable=request,
            )
            document.themes[request.themeId] = _StoredThemeDocument(
                themeId=request.themeId,
                draft=bundle,
                publishedVersions=[bundle.model_copy(deep=True)],
            )
            self._write_document(document)

    def list_themes(self) -> ThemeListResponseV1:
        with self._lock:
            document = self._load_document()
            summaries: list[ThemeSummaryV1] = []
            for theme_id in sorted(document.themes.keys()):
                stored = document.themes[theme_id]
                latest_published = self._latest_published(stored)
                summaries.append(
                    ThemeSummaryV1(
                        themeId=theme_id,
                        name=stored.draft.name,
                        draftVersion=stored.draft.themeVersion,
                        publishedVersion=(
                            latest_published.themeVersion if latest_published else None
                        ),
                        updatedAt=stored.draft.updatedAt,
                        checksum=stored.draft.checksum,
                    )
                )
            return ThemeListResponseV1(themes=summaries)

    def get_theme(self, theme_id: str) -> ThemeRecordV1:
        with self._lock:
            document = self._load_document()
            stored = document.themes.get(theme_id)
            if stored is None:
                raise ThemeStoreError(
                    status_code=404,
                    code="THEME_NOT_FOUND",
                    message=f"Theme '{theme_id}' was not found.",
                )
            return ThemeRecordV1(
                themeId=theme_id,
                draft=stored.draft.model_copy(deep=True),
                publishedVersions=[
                    bundle.model_copy(deep=True)
                    for bundle in self._sorted_published(stored.publishedVersions)
                ],
            )

    def create_theme(self, request: ThemeCreateRequestV1) -> ThemeRecordV1:
        with self._lock:
            document = self._load_document()
            if request.themeId in document.themes:
                raise ThemeStoreError(
                    status_code=409,
                    code="THEME_ALREADY_EXISTS",
                    message=f"Theme '{request.themeId}' already exists.",
                )

            bundle = self._build_bundle(
                theme_id=request.themeId,
                theme_version=1,
                editable=request,
            )
            document.themes[request.themeId] = _StoredThemeDocument(
                themeId=request.themeId,
                draft=bundle,
                publishedVersions=[],
            )
            self._write_document(document)
            return self.get_theme(request.themeId)

    def update_theme(
        self,
        theme_id: str,
        request: ThemeUpdateRequestV1,
    ) -> ThemeRecordV1:
        with self._lock:
            document = self._load_document()
            stored = document.themes.get(theme_id)
            if stored is None:
                raise ThemeStoreError(
                    status_code=404,
                    code="THEME_NOT_FOUND",
                    message=f"Theme '{theme_id}' was not found.",
                )

            next_version = int(stored.draft.themeVersion) + 1
            stored.draft = self._build_bundle(
                theme_id=theme_id,
                theme_version=next_version,
                editable=request,
            )
            document.themes[theme_id] = stored
            self._write_document(document)
            return self.get_theme(theme_id)

    def publish_theme(self, theme_id: str) -> ThemeBundleV1:
        with self._lock:
            document = self._load_document()
            stored = document.themes.get(theme_id)
            if stored is None:
                raise ThemeStoreError(
                    status_code=404,
                    code="THEME_NOT_FOUND",
                    message=f"Theme '{theme_id}' was not found.",
                )

            draft = stored.draft
            if any(
                bundle.themeVersion == draft.themeVersion
                for bundle in stored.publishedVersions
            ):
                raise ThemeStoreError(
                    status_code=409,
                    code="THEME_VERSION_ALREADY_PUBLISHED",
                    message=(
                        f"Theme '{theme_id}' version {draft.themeVersion} is already published."
                    ),
                )

            stored.publishedVersions.append(draft.model_copy(deep=True))
            document.themes[theme_id] = stored
            self._write_document(document)
            return draft.model_copy(deep=True)

    def get_bundle(
        self,
        theme_id: str,
        *,
        stage: Literal["draft", "published"] = "published",
        theme_version: int | None = None,
    ) -> ThemeBundleV1:
        with self._lock:
            document = self._load_document()
            stored = document.themes.get(theme_id)
            if stored is None:
                raise ThemeStoreError(
                    status_code=404,
                    code="THEME_NOT_FOUND",
                    message=f"Theme '{theme_id}' was not found.",
                )

            if stage == "draft":
                draft = stored.draft
                if theme_version is not None and draft.themeVersion != theme_version:
                    raise ThemeStoreError(
                        status_code=404,
                        code="THEME_VERSION_NOT_FOUND",
                        message=(
                            f"Theme '{theme_id}' draft version {theme_version} was not found."
                        ),
                    )
                return draft.model_copy(deep=True)

            published = self._sorted_published(stored.publishedVersions)
            if not published:
                raise ThemeStoreError(
                    status_code=404,
                    code="THEME_NOT_PUBLISHED",
                    message=f"Theme '{theme_id}' has no published version.",
                )

            if theme_version is None:
                return published[-1].model_copy(deep=True)

            for bundle in published:
                if bundle.themeVersion == theme_version:
                    return bundle.model_copy(deep=True)

            raise ThemeStoreError(
                status_code=404,
                code="THEME_VERSION_NOT_FOUND",
                message=(
                    f"Theme '{theme_id}' published version {theme_version} was not found."
                ),
            )

    def _latest_published(
        self, stored: _StoredThemeDocument
    ) -> ThemeBundleV1 | None:
        published = self._sorted_published(stored.publishedVersions)
        return published[-1] if published else None

    @staticmethod
    def _sorted_published(published: list[ThemeBundleV1]) -> list[ThemeBundleV1]:
        return sorted(published, key=lambda item: item.themeVersion)

    def _build_bundle(
        self,
        *,
        theme_id: str,
        theme_version: int,
        editable: ThemeEditableFieldsV1,
    ) -> ThemeBundleV1:
        timestamp = utcnow()

        payload = {
            "schemaVersion": "v1",
            "themeId": theme_id,
            "themeVersion": theme_version,
            "name": editable.name,
            "renderCss": editable.renderCss,
            "updatedAt": timestamp,
        }
        payload["checksum"] = compute_theme_checksum(payload)
        return ThemeBundleV1.model_validate(payload)

    def _load_document(self) -> ThemeStoreDocumentV1:
        if not self._storage_path.exists():
            return ThemeStoreDocumentV1()

        try:
            raw = self._storage_path.read_text(encoding="utf-8")
            if not raw.strip():
                return ThemeStoreDocumentV1()
            data = json.loads(raw)
            document = ThemeStoreDocumentV1.model_validate(data)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ThemeStoreError(
                status_code=500,
                code="THEME_STORAGE_CORRUPTED",
                message="Theme storage file is unreadable or invalid.",
            ) from exc

        for key, stored in document.themes.items():
            if key != stored.themeId:
                raise ThemeStoreError(
                    status_code=500,
                    code="THEME_STORAGE_CORRUPTED",
                    message="Theme storage contains mismatched theme ids.",
                )

        return document

    def _write_document(self, document: ThemeStoreDocumentV1) -> None:
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(document.model_dump(mode="json"), indent=2) + "\n",
                encoding="utf-8",
            )
            tmp_path.replace(self._storage_path)
        except OSError as exc:
            raise ThemeStoreError(
                status_code=500,
                code="THEME_STORAGE_WRITE_FAILED",
                message="Failed to persist theme storage.",
            ) from exc
