from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROFILE_SCHEMA_VERSION = "v1"
PROFILE_ICONSET_RESOLUTION_SCHEMA_VERSION = "v1"
AUTOCOMPLETE_SCHEMA_VERSION = "v1"

PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
THEME_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
ICONSET_ID_PATTERN = PROFILE_ID_PATTERN
TYPE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
LINK_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
ICONIFY_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*:[a-z0-9]+(?:[-_][a-z0-9]+)*$")
CHECKSUM_PATTERN = re.compile(r"^[a-f0-9]{64}$")

MIN_TYPE_KEY_LENGTH = 2
MAX_TYPE_KEY_LENGTH = 64
MAX_ICONSET_ENTRIES = 2_000
MAX_RESOLVED_TYPE_KEYS = 5_000
MAX_ICONSET_REFS = 8
MAX_LINK_TYPES = 256

IconConflictPolicy = Literal["reject", "first-wins", "last-wins"]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def normalize_profile_id(value: str) -> str:
    profile_id = str(value).strip().lower()
    if not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ValueError("profileId must match ^[a-z0-9][a-z0-9_-]{1,63}$")
    return profile_id


def normalize_theme_id(value: str) -> str:
    theme_id = str(value).strip().lower()
    if not THEME_ID_PATTERN.fullmatch(theme_id):
        raise ValueError("themeId must match ^[a-z0-9][a-z0-9_-]{1,63}$")
    return theme_id


def normalize_icon_set_id(value: str) -> str:
    icon_set_id = str(value).strip().lower()
    if not ICONSET_ID_PATTERN.fullmatch(icon_set_id):
        raise ValueError("iconSetId must match ^[a-z0-9][a-z0-9_-]{1,63}$")
    return icon_set_id


def normalize_type_key(value: str) -> str:
    key = str(value).strip().lower()
    if " " in key:
        raise ValueError(f"Invalid node type key '{value}'. Spaces are not allowed.")
    if not (MIN_TYPE_KEY_LENGTH <= len(key) <= MAX_TYPE_KEY_LENGTH):
        raise ValueError(
            f"Invalid node type key '{value}'. Length must be {MIN_TYPE_KEY_LENGTH}-{MAX_TYPE_KEY_LENGTH}."
        )
    if not TYPE_KEY_PATTERN.fullmatch(key):
        raise ValueError(f"Invalid node type key '{value}'. Use ^[a-z][a-z0-9_-]*$.")
    return key


def normalize_link_type(value: str) -> str:
    item = str(value).strip().lower()
    if not item:
        raise ValueError("linkTypes entries must not be empty.")
    if not LINK_TYPE_PATTERN.fullmatch(item):
        raise ValueError(f"Invalid link type '{value}'. Use ^[a-z][a-z0-9_-]*$.")
    return item


def normalize_iconify_name(value: str) -> str:
    icon_name = str(value).strip().lower()
    if not ICONIFY_NAME_PATTERN.fullmatch(icon_name):
        raise ValueError(
            f"Invalid iconify value '{value}'. Use <iconset>:<icon> (e.g. iconoir:airplay-solid)."
        )
    return icon_name


def normalize_checksum(value: str) -> str:
    checksum = str(value).strip().lower()
    if not CHECKSUM_PATTERN.fullmatch(checksum):
        raise ValueError("checksum must match ^[a-f0-9]{64}$")
    return checksum


class IconsetEntryUpsertRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    icon: str

    @field_validator("icon")
    @classmethod
    def validate_icon(cls, value: str) -> str:
        return normalize_iconify_name(value)


class IconsetEditableFieldsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    entries: dict[str, str] = Field(min_length=1, max_length=MAX_ICONSET_ENTRIES)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("name must not be empty.")
        return text

    @field_validator("entries")
    @classmethod
    def validate_entries(cls, values: dict[str, str]) -> dict[str, str]:
        if not values:
            raise ValueError("entries must not be empty.")

        normalized: dict[str, str] = {}
        for raw_key, raw_value in values.items():
            key = normalize_type_key(raw_key)
            icon = normalize_iconify_name(raw_value)
            if key in normalized:
                raise ValueError(f"Duplicate node type key '{raw_key}'.")
            normalized[key] = icon

        if len(normalized) > MAX_ICONSET_ENTRIES:
            raise ValueError(f"entries exceeds max size {MAX_ICONSET_ENTRIES}.")

        return dict(sorted(normalized.items(), key=lambda item: item[0]))


class IconsetCreateRequestV1(IconsetEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    iconSetId: str

    @field_validator("iconSetId")
    @classmethod
    def validate_icon_set_id(cls, value: str) -> str:
        return normalize_icon_set_id(value)


class IconsetUpdateRequestV1(IconsetEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")


class IconsetBundleV1(IconsetEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    iconSetId: str
    iconSetVersion: int = Field(ge=1)
    updatedAt: datetime
    checksum: str = Field(min_length=64, max_length=64)


class IconsetSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    iconSetId: str
    name: str
    draftVersion: int
    publishedVersion: int | None = None
    updatedAt: datetime
    checksum: str


class IconsetRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    iconSetId: str
    draft: IconsetBundleV1
    publishedVersions: list[IconsetBundleV1] = Field(default_factory=list)


class IconsetListResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iconSets: list[IconsetSummaryV1]


class IconsetSourceRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iconSetId: str
    iconSetVersion: int = Field(ge=1)
    checksum: str = Field(min_length=64, max_length=64)


class NodeTypeSourceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    icon: str
    selectedFrom: IconsetSourceRefV1
    candidates: list[IconsetSourceRefV1] = Field(default_factory=list)


class IconsetResolutionRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iconSetId: str
    stage: Literal["draft", "published"] = "published"
    iconSetVersion: int | None = Field(default=None, ge=1)

    @field_validator("iconSetId")
    @classmethod
    def validate_icon_set_id(cls, value: str) -> str:
        return normalize_icon_set_id(value)


class IconsetResolveRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iconSetRefs: list[IconsetResolutionRefV1] = Field(min_length=1, max_length=MAX_ICONSET_REFS)
    conflictPolicy: IconConflictPolicy = "reject"


class IconsetResolutionResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_ICONSET_RESOLUTION_SCHEMA_VERSION
    conflictPolicy: IconConflictPolicy
    resolvedEntries: dict[str, str]
    sources: list[IconsetSourceRefV1]
    keySources: dict[str, NodeTypeSourceV1]
    checksum: str = Field(min_length=64, max_length=64)


class ProfileIconsetRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iconSetId: str
    iconSetVersion: int = Field(ge=1)
    checksum: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("iconSetId")
    @classmethod
    def validate_icon_set_id(cls, value: str) -> str:
        return normalize_icon_set_id(value)

    @field_validator("checksum")
    @classmethod
    def validate_checksum(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_checksum(value)


class ProfileEditableFieldsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    linkTypes: list[str] = Field(min_length=1, max_length=MAX_LINK_TYPES)
    elkSettings: dict[str, Any]
    iconSetRefs: list[ProfileIconsetRefV1] = Field(min_length=1, max_length=MAX_ICONSET_REFS)
    iconConflictPolicy: IconConflictPolicy = "reject"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("name must not be empty.")
        return text

    @field_validator("linkTypes")
    @classmethod
    def validate_link_types(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("linkTypes must not be empty.")

        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            item = normalize_link_type(raw)
            if item in seen:
                raise ValueError(f"Duplicate link type '{raw}'.")
            seen.add(item)
            normalized.append(item)

        if len(normalized) > MAX_LINK_TYPES:
            raise ValueError(f"linkTypes exceeds max size {MAX_LINK_TYPES}.")

        return normalized

    @field_validator("iconSetRefs")
    @classmethod
    def validate_iconset_refs(cls, values: list[ProfileIconsetRefV1]) -> list[ProfileIconsetRefV1]:
        if not values:
            raise ValueError("iconSetRefs must not be empty.")

        seen: set[tuple[str, int]] = set()
        for item in values:
            key = (item.iconSetId, item.iconSetVersion)
            if key in seen:
                raise ValueError(
                    f"Duplicate iconset reference '{item.iconSetId}@{item.iconSetVersion}'."
                )
            seen.add(key)

        if len(values) > MAX_ICONSET_REFS:
            raise ValueError(f"iconSetRefs exceeds max size {MAX_ICONSET_REFS}.")

        return values


class ProfileCreateRequestV1(ProfileEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    profileId: str

    @field_validator("profileId")
    @classmethod
    def validate_profile_id(cls, value: str) -> str:
        return normalize_profile_id(value)


class ProfileUpdateRequestV1(ProfileEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")


class ProfileBundleV1(ProfileEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    profileId: str
    profileVersion: int = Field(ge=1)
    nodeTypes: list[str] = Field(min_length=1, max_length=MAX_RESOLVED_TYPE_KEYS)
    typeIconMap: dict[str, str] = Field(min_length=1, max_length=MAX_RESOLVED_TYPE_KEYS)
    iconSetResolutionChecksum: str = Field(min_length=64, max_length=64)
    updatedAt: datetime
    checksum: str = Field(min_length=64, max_length=64)

    @field_validator("nodeTypes")
    @classmethod
    def validate_node_types(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            key = normalize_type_key(raw)
            if key in seen:
                raise ValueError(f"Duplicate node type '{raw}'.")
            seen.add(key)
            normalized.append(key)

        if len(normalized) > MAX_RESOLVED_TYPE_KEYS:
            raise ValueError(f"nodeTypes exceeds max size {MAX_RESOLVED_TYPE_KEYS}.")

        return sorted(normalized)

    @field_validator("typeIconMap")
    @classmethod
    def validate_type_icon_map(cls, values: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_key, raw_value in values.items():
            key = normalize_type_key(raw_key)
            icon = normalize_iconify_name(raw_value)
            if key in normalized:
                raise ValueError(f"Duplicate node type key '{raw_key}'.")
            normalized[key] = icon

        if len(normalized) > MAX_RESOLVED_TYPE_KEYS:
            raise ValueError(f"typeIconMap exceeds max size {MAX_RESOLVED_TYPE_KEYS}.")

        return dict(sorted(normalized.items(), key=lambda item: item[0]))

    @model_validator(mode="after")
    def validate_node_types_match_type_map(self) -> "ProfileBundleV1":
        if sorted(self.nodeTypes) != sorted(self.typeIconMap.keys()):
            raise ValueError("nodeTypes must exactly match typeIconMap keys.")
        return self


class ProfileSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    profileId: str
    name: str
    draftVersion: int
    publishedVersion: int | None = None
    updatedAt: datetime
    checksum: str
    iconSetResolutionChecksum: str


class ProfileRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    profileId: str
    draft: ProfileBundleV1
    publishedVersions: list[ProfileBundleV1] = Field(default_factory=list)


class ProfileListResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: list[ProfileSummaryV1]


class ProfileIconsetResolutionResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_ICONSET_RESOLUTION_SCHEMA_VERSION
    profileId: str
    profileVersion: int = Field(ge=1)
    profileChecksum: str = Field(min_length=64, max_length=64)
    conflictPolicy: IconConflictPolicy
    resolvedEntries: dict[str, str]
    sources: list[IconsetSourceRefV1]
    keySources: dict[str, NodeTypeSourceV1]
    checksum: str = Field(min_length=64, max_length=64)


class AutocompleteCatalogResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = AUTOCOMPLETE_SCHEMA_VERSION
    profileId: str
    profileVersion: int = Field(ge=1)
    profileChecksum: str = Field(min_length=64, max_length=64)
    iconSetResolutionChecksum: str = Field(min_length=64, max_length=64)
    checksum: str = Field(min_length=64, max_length=64)
    nodeTypes: list[str]
    linkTypes: list[str]


class ThemeEditableFieldsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
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


class ThemeCreateRequestV1(ThemeEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    themeId: str

    @field_validator("themeId")
    @classmethod
    def validate_theme_id(cls, value: str) -> str:
        return normalize_theme_id(value)


class ThemeUpdateRequestV1(ThemeEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")


class ThemeBundleV1(ThemeEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    themeId: str
    themeVersion: int = Field(ge=1)
    updatedAt: datetime
    checksum: str = Field(min_length=64, max_length=64)


class ThemeSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    themeId: str
    name: str
    draftVersion: int
    publishedVersion: int | None = None
    updatedAt: datetime
    checksum: str


class ThemeRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    themeId: str
    draft: ThemeBundleV1
    publishedVersions: list[ThemeBundleV1] = Field(default_factory=list)


class ThemeListResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    themes: list[ThemeSummaryV1]


class _StoredThemeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    themeId: str
    draft: ThemeBundleV1
    publishedVersions: list[ThemeBundleV1] = Field(default_factory=list)


class ThemeStoreDocumentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROFILE_SCHEMA_VERSION
    themes: dict[str, _StoredThemeDocument] = Field(default_factory=dict)


def canonical_iconset_bundle_payload(bundle_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": PROFILE_SCHEMA_VERSION,
        "iconSetId": bundle_data["iconSetId"],
        "iconSetVersion": bundle_data["iconSetVersion"],
        "name": bundle_data["name"],
        "entries": dict(sorted(bundle_data["entries"].items(), key=lambda item: item[0])),
    }


def compute_iconset_checksum(bundle_data: dict[str, Any]) -> str:
    return _sha256_hex(canonical_iconset_bundle_payload(bundle_data))


def canonical_iconset_resolution_payload(
    *,
    conflict_policy: IconConflictPolicy,
    sources: list[dict[str, Any]],
    resolved_entries: dict[str, str],
) -> dict[str, Any]:
    return {
        "schemaVersion": PROFILE_ICONSET_RESOLUTION_SCHEMA_VERSION,
        "conflictPolicy": conflict_policy,
        "sources": [
            {
                "iconSetId": item["iconSetId"],
                "iconSetVersion": item["iconSetVersion"],
                "checksum": item["checksum"],
            }
            for item in sources
        ],
        "resolvedEntries": dict(sorted(resolved_entries.items(), key=lambda item: item[0])),
    }


def compute_icon_set_resolution_checksum(
    *,
    conflict_policy: IconConflictPolicy,
    sources: list[dict[str, Any]],
    resolved_entries: dict[str, str],
) -> str:
    return _sha256_hex(
        canonical_iconset_resolution_payload(
            conflict_policy=conflict_policy,
            sources=sources,
            resolved_entries=resolved_entries,
        )
    )


def canonical_profile_bundle_payload(bundle_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": PROFILE_SCHEMA_VERSION,
        "profileId": bundle_data["profileId"],
        "profileVersion": bundle_data["profileVersion"],
        "name": bundle_data["name"],
        "nodeTypes": sorted(bundle_data["nodeTypes"]),
        "linkTypes": list(bundle_data["linkTypes"]),
        "elkSettings": bundle_data["elkSettings"],
        "iconSetRefs": [
            {
                "iconSetId": item["iconSetId"],
                "iconSetVersion": item["iconSetVersion"],
                "checksum": item.get("checksum"),
            }
            for item in bundle_data["iconSetRefs"]
        ],
        "iconConflictPolicy": bundle_data["iconConflictPolicy"],
        "typeIconMap": dict(sorted(bundle_data["typeIconMap"].items(), key=lambda item: item[0])),
        "iconSetResolutionChecksum": bundle_data["iconSetResolutionChecksum"],
    }


def compute_profile_checksum(bundle_data: dict[str, Any]) -> str:
    return _sha256_hex(canonical_profile_bundle_payload(bundle_data))


def compute_autocomplete_checksum(bundle: ProfileBundleV1) -> str:
    payload = {
        "schemaVersion": AUTOCOMPLETE_SCHEMA_VERSION,
        "profileId": bundle.profileId,
        "profileVersion": bundle.profileVersion,
        "profileChecksum": bundle.checksum,
        "iconSetResolutionChecksum": bundle.iconSetResolutionChecksum,
        "nodeTypes": bundle.nodeTypes,
        "linkTypes": bundle.linkTypes,
    }
    return _sha256_hex(payload)


def canonical_theme_bundle_payload(bundle_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": PROFILE_SCHEMA_VERSION,
        "themeId": bundle_data["themeId"],
        "themeVersion": bundle_data["themeVersion"],
        "name": bundle_data["name"],
        "renderCss": bundle_data["renderCss"],
    }


def compute_theme_checksum(bundle_data: dict[str, Any]) -> str:
    return _sha256_hex(canonical_theme_bundle_payload(bundle_data))
