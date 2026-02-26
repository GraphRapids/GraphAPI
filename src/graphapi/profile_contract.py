from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROFILE_SCHEMA_VERSION = "v1"
PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
TYPE_TOKEN_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ProfileEditableFieldsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    nodeTypes: list[str] = Field(min_length=1)
    linkTypes: list[str] = Field(min_length=1)
    elkSettings: dict[str, Any]
    renderCss: str = Field(min_length=1, max_length=500_000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("name must not be empty.")
        return text

    @field_validator("renderCss")
    @classmethod
    def validate_render_css(cls, value: str) -> str:
        if not str(value).strip():
            raise ValueError("renderCss must not be empty.")
        return value

    @field_validator("nodeTypes", "linkTypes")
    @classmethod
    def validate_type_tokens(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for raw in values:
            item = str(raw).strip()
            if not item:
                raise ValueError("catalog entries must not be empty.")
            if not TYPE_TOKEN_PATTERN.fullmatch(item):
                raise ValueError(
                    f"Invalid catalog token '{item}'. Use [A-Za-z][A-Za-z0-9_-]*."
                )
            key = item.lower()
            if key in seen:
                raise ValueError(f"Duplicate catalog token '{item}'.")
            seen.add(key)
            normalized.append(key)
        return normalized


class ProfileCreateRequestV1(ProfileEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    profileId: str

    @field_validator("profileId")
    @classmethod
    def validate_profile_id(cls, value: str) -> str:
        profile_id = str(value).strip().lower()
        if not PROFILE_ID_PATTERN.fullmatch(profile_id):
            raise ValueError(
                "profileId must match ^[a-z0-9][a-z0-9_-]{1,63}$"
            )
        return profile_id


class ProfileUpdateRequestV1(ProfileEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")


class ProfileBundleV1(ProfileEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    profileId: str
    profileVersion: int = Field(ge=1)
    updatedAt: datetime
    checksum: str = Field(min_length=64, max_length=64)


class ProfileSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    profileId: str
    name: str
    draftVersion: int
    publishedVersion: int | None = None
    updatedAt: datetime
    checksum: str


class ProfileRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    profileId: str
    draft: ProfileBundleV1
    publishedVersions: list[ProfileBundleV1] = Field(default_factory=list)


class ProfileListResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: list[ProfileSummaryV1]


class PublishResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: ProfileBundleV1


class AutocompleteCatalogResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    profileId: str
    profileVersion: int = Field(ge=1)
    checksum: str = Field(min_length=64, max_length=64)
    nodeTypes: list[str]
    linkTypes: list[str]


class _StoredProfileDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profileId: str
    draft: ProfileBundleV1
    publishedVersions: list[ProfileBundleV1] = Field(default_factory=list)


class ProfileStoreDocumentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    profiles: dict[str, _StoredProfileDocument] = Field(default_factory=dict)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_bundle_payload(bundle_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": PROFILE_SCHEMA_VERSION,
        "profileId": bundle_data["profileId"],
        "profileVersion": bundle_data["profileVersion"],
        "name": bundle_data["name"],
        "nodeTypes": bundle_data["nodeTypes"],
        "linkTypes": bundle_data["linkTypes"],
        "elkSettings": bundle_data["elkSettings"],
        "renderCss": bundle_data["renderCss"],
    }


def compute_checksum(bundle_data: dict[str, Any]) -> str:
    canonical = json.dumps(
        canonical_bundle_payload(bundle_data),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
