from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .profile_contract import PROFILE_ID_PATTERN, utcnow

ICONSET_SCHEMA_VERSION = "v1"
PROFILE_V2_SCHEMA_VERSION = "v2"
AUTOCOMPLETE_V2_SCHEMA_VERSION = "v2"
PROFILE_ICONSET_RESOLUTION_SCHEMA_VERSION = "v1"

ICONSET_ID_PATTERN = PROFILE_ID_PATTERN
TYPE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
ICONIFY_NAME_PATTERN = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*:[a-z0-9]+(?:[-_][a-z0-9]+)*$"
)
LINK_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")

MIN_TYPE_KEY_LENGTH = 2
MAX_TYPE_KEY_LENGTH = 64
MAX_ICONSET_ENTRIES = 2_000
MAX_RESOLVED_TYPE_KEYS = 5_000
MAX_ICONSET_REFS = 8
MAX_LINK_TYPES = 256


IconConflictPolicy = Literal["reject", "first-wins", "last-wins"]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def normalize_id(value: str, *, field: str) -> str:
    normalized = str(value).strip().lower()
    if not ICONSET_ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must match ^[a-z0-9][a-z0-9_-]{{1,63}}$")
    return normalized


def normalize_type_key(value: str) -> str:
    key = str(value).strip().lower()
    if " " in key:
        raise ValueError(f"Invalid node type key '{value}'. Spaces are not allowed.")
    if not (MIN_TYPE_KEY_LENGTH <= len(key) <= MAX_TYPE_KEY_LENGTH):
        raise ValueError(
            f"Invalid node type key '{value}'. Length must be {MIN_TYPE_KEY_LENGTH}-{MAX_TYPE_KEY_LENGTH}."
        )
    if not TYPE_KEY_PATTERN.fullmatch(key):
        raise ValueError(
            f"Invalid node type key '{value}'. Use ^[a-z][a-z0-9_-]*$."
        )
    return key


def normalize_iconify_name(value: str) -> str:
    icon_name = str(value).strip().lower()
    if not ICONIFY_NAME_PATTERN.fullmatch(icon_name):
        raise ValueError(
            f"Invalid iconify value '{value}'. Use <iconset>:<icon> (e.g. iconoir:airplay-solid)."
        )
    return icon_name


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
            raise ValueError(
                f"entries exceeds max size {MAX_ICONSET_ENTRIES}."
            )

        return dict(sorted(normalized.items(), key=lambda item: item[0]))


class IconsetCreateRequestV1(IconsetEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    iconsetId: str

    @field_validator("iconsetId")
    @classmethod
    def validate_iconset_id(cls, value: str) -> str:
        return normalize_id(value, field="iconsetId")


class IconsetUpdateRequestV1(IconsetEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")


class IconsetBundleV1(IconsetEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = ICONSET_SCHEMA_VERSION
    iconsetId: str
    iconsetVersion: int = Field(ge=1)
    updatedAt: datetime
    checksum: str = Field(min_length=64, max_length=64)


class IconsetSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = ICONSET_SCHEMA_VERSION
    iconsetId: str
    name: str
    draftVersion: int
    publishedVersion: int | None = None
    updatedAt: datetime
    checksum: str


class IconsetRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = ICONSET_SCHEMA_VERSION
    iconsetId: str
    draft: IconsetBundleV1
    publishedVersions: list[IconsetBundleV1] = Field(default_factory=list)


class IconsetListResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iconsets: list[IconsetSummaryV1]


class IconsetSourceRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iconsetId: str
    iconsetVersion: int = Field(ge=1)
    checksum: str = Field(min_length=64, max_length=64)


class NodeTypeSourceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    icon: str
    selectedFrom: IconsetSourceRefV1
    candidates: list[IconsetSourceRefV1] = Field(default_factory=list)


class IconsetResolutionRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iconsetId: str
    stage: Literal["draft", "published"] = "published"
    iconsetVersion: int | None = Field(default=None, ge=1)

    @field_validator("iconsetId")
    @classmethod
    def validate_iconset_id(cls, value: str) -> str:
        return normalize_id(value, field="iconsetId")


class IconsetResolveRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iconsetRefs: list[IconsetResolutionRefV1] = Field(min_length=1, max_length=MAX_ICONSET_REFS)
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

    iconsetId: str
    iconsetVersion: int = Field(ge=1)
    checksum: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("iconsetId")
    @classmethod
    def validate_iconset_id(cls, value: str) -> str:
        return normalize_id(value, field="iconsetId")

    @field_validator("checksum")
    @classmethod
    def validate_checksum(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if not re.fullmatch(r"^[a-f0-9]{64}$", normalized):
            raise ValueError("checksum must match ^[a-f0-9]{64}$")
        return normalized


class ProfileEditableFieldsV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    linkTypes: list[str] = Field(min_length=1, max_length=MAX_LINK_TYPES)
    elkSettings: dict[str, Any]
    iconsetRefs: list[ProfileIconsetRefV1] = Field(min_length=1, max_length=MAX_ICONSET_REFS)
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
            item = str(raw).strip().lower()
            if not item:
                raise ValueError("linkTypes entries must not be empty.")
            if not LINK_TYPE_PATTERN.fullmatch(item):
                raise ValueError(
                    f"Invalid link type '{raw}'. Use ^[a-z][a-z0-9_-]*$."
                )
            if item in seen:
                raise ValueError(f"Duplicate link type '{raw}'.")
            seen.add(item)
            normalized.append(item)

        if len(normalized) > MAX_LINK_TYPES:
            raise ValueError(
                f"linkTypes exceeds max size {MAX_LINK_TYPES}."
            )

        return normalized

    @field_validator("iconsetRefs")
    @classmethod
    def validate_iconset_refs(cls, values: list[ProfileIconsetRefV1]) -> list[ProfileIconsetRefV1]:
        if not values:
            raise ValueError("iconsetRefs must not be empty.")

        seen: set[tuple[str, int]] = set()
        for item in values:
            key = (item.iconsetId, item.iconsetVersion)
            if key in seen:
                raise ValueError(
                    f"Duplicate iconset reference '{item.iconsetId}@{item.iconsetVersion}'."
                )
            seen.add(key)

        if len(values) > MAX_ICONSET_REFS:
            raise ValueError(
                f"iconsetRefs exceeds max size {MAX_ICONSET_REFS}."
            )

        return values


class ProfileCreateRequestV2(ProfileEditableFieldsV2):
    model_config = ConfigDict(extra="forbid")

    profileId: str

    @field_validator("profileId")
    @classmethod
    def validate_profile_id(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if not PROFILE_ID_PATTERN.fullmatch(normalized):
            raise ValueError("profileId must match ^[a-z0-9][a-z0-9_-]{1,63}$")
        return normalized


class ProfileUpdateRequestV2(ProfileEditableFieldsV2):
    model_config = ConfigDict(extra="forbid")


class ProfileBundleV2(ProfileEditableFieldsV2):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v2"] = PROFILE_V2_SCHEMA_VERSION
    profileId: str
    profileVersion: int = Field(ge=1)
    nodeTypes: list[str] = Field(min_length=1, max_length=MAX_RESOLVED_TYPE_KEYS)
    typeIconMap: dict[str, str] = Field(min_length=1, max_length=MAX_RESOLVED_TYPE_KEYS)
    iconsetResolutionChecksum: str = Field(min_length=64, max_length=64)
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
            raise ValueError(
                f"nodeTypes exceeds max size {MAX_RESOLVED_TYPE_KEYS}."
            )

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
            raise ValueError(
                f"typeIconMap exceeds max size {MAX_RESOLVED_TYPE_KEYS}."
            )

        return dict(sorted(normalized.items(), key=lambda item: item[0]))

    @model_validator(mode="after")
    def validate_node_types_match_type_map(self) -> "ProfileBundleV2":
        if sorted(self.nodeTypes) != sorted(self.typeIconMap.keys()):
            raise ValueError("nodeTypes must exactly match typeIconMap keys.")
        return self


class ProfileSummaryV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v2"] = PROFILE_V2_SCHEMA_VERSION
    profileId: str
    name: str
    draftVersion: int
    publishedVersion: int | None = None
    updatedAt: datetime
    checksum: str
    iconsetResolutionChecksum: str


class ProfileRecordV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v2"] = PROFILE_V2_SCHEMA_VERSION
    profileId: str
    draft: ProfileBundleV2
    publishedVersions: list[ProfileBundleV2] = Field(default_factory=list)


class ProfileListResponseV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: list[ProfileSummaryV2]


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


class AutocompleteCatalogResponseV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v2"] = AUTOCOMPLETE_V2_SCHEMA_VERSION
    profileId: str
    profileVersion: int = Field(ge=1)
    profileChecksum: str = Field(min_length=64, max_length=64)
    iconsetResolutionChecksum: str = Field(min_length=64, max_length=64)
    checksum: str = Field(min_length=64, max_length=64)
    nodeTypes: list[str]
    linkTypes: list[str]


class _StoredIconsetDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iconsetId: str
    draft: IconsetBundleV1
    publishedVersions: list[IconsetBundleV1] = Field(default_factory=list)


class IconsetStoreDocumentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = ICONSET_SCHEMA_VERSION
    iconsets: dict[str, _StoredIconsetDocument] = Field(default_factory=dict)


class _StoredProfileV2Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profileId: str
    draft: ProfileBundleV2
    publishedVersions: list[ProfileBundleV2] = Field(default_factory=list)


class ProfileStoreDocumentV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v2"] = PROFILE_V2_SCHEMA_VERSION
    profiles: dict[str, _StoredProfileV2Document] = Field(default_factory=dict)


def canonical_iconset_bundle_payload(bundle_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": ICONSET_SCHEMA_VERSION,
        "iconsetId": bundle_data["iconsetId"],
        "iconsetVersion": bundle_data["iconsetVersion"],
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
                "iconsetId": item["iconsetId"],
                "iconsetVersion": item["iconsetVersion"],
                "checksum": item["checksum"],
            }
            for item in sources
        ],
        "resolvedEntries": dict(sorted(resolved_entries.items(), key=lambda item: item[0])),
    }


def compute_iconset_resolution_checksum(
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


def canonical_profile_v2_bundle_payload(bundle_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": PROFILE_V2_SCHEMA_VERSION,
        "profileId": bundle_data["profileId"],
        "profileVersion": bundle_data["profileVersion"],
        "name": bundle_data["name"],
        "nodeTypes": sorted(bundle_data["nodeTypes"]),
        "linkTypes": list(bundle_data["linkTypes"]),
        "elkSettings": bundle_data["elkSettings"],
        "iconsetRefs": [
            {
                "iconsetId": item["iconsetId"],
                "iconsetVersion": item["iconsetVersion"],
                "checksum": item.get("checksum"),
            }
            for item in bundle_data["iconsetRefs"]
        ],
        "iconConflictPolicy": bundle_data["iconConflictPolicy"],
        "typeIconMap": dict(sorted(bundle_data["typeIconMap"].items(), key=lambda item: item[0])),
        "iconsetResolutionChecksum": bundle_data["iconsetResolutionChecksum"],
    }


def compute_profile_v2_checksum(bundle_data: dict[str, Any]) -> str:
    return _sha256_hex(canonical_profile_v2_bundle_payload(bundle_data))


def compute_autocomplete_v2_checksum(bundle: ProfileBundleV2) -> str:
    payload = {
        "schemaVersion": AUTOCOMPLETE_V2_SCHEMA_VERSION,
        "profileId": bundle.profileId,
        "profileVersion": bundle.profileVersion,
        "profileChecksum": bundle.checksum,
        "iconsetResolutionChecksum": bundle.iconsetResolutionChecksum,
        "nodeTypes": bundle.nodeTypes,
        "linkTypes": bundle.linkTypes,
    }
    return _sha256_hex(payload)


__all__ = [
    "AUTOCOMPLETE_V2_SCHEMA_VERSION",
    "ICONSET_SCHEMA_VERSION",
    "PROFILE_ICONSET_RESOLUTION_SCHEMA_VERSION",
    "PROFILE_V2_SCHEMA_VERSION",
    "AutocompleteCatalogResponseV2",
    "IconConflictPolicy",
    "IconsetBundleV1",
    "IconsetCreateRequestV1",
    "IconsetListResponseV1",
    "IconsetRecordV1",
    "IconsetResolutionRefV1",
    "IconsetResolutionResultV1",
    "IconsetSourceRefV1",
    "IconsetStoreDocumentV1",
    "IconsetSummaryV1",
    "IconsetUpdateRequestV1",
    "NodeTypeSourceV1",
    "ProfileBundleV2",
    "ProfileCreateRequestV2",
    "ProfileEditableFieldsV2",
    "ProfileIconsetRefV1",
    "ProfileIconsetResolutionResponseV1",
    "ProfileListResponseV2",
    "ProfileRecordV2",
    "ProfileStoreDocumentV2",
    "ProfileSummaryV2",
    "ProfileUpdateRequestV2",
    "_StoredIconsetDocument",
    "_StoredProfileV2Document",
    "compute_autocomplete_v2_checksum",
    "compute_iconset_checksum",
    "compute_iconset_resolution_checksum",
    "compute_profile_v2_checksum",
    "normalize_iconify_name",
    "normalize_type_key",
    "utcnow",
]
