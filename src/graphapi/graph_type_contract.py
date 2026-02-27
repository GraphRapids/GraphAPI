from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .profile_contract import (
    CHECKSUM_PATTERN,
    IconConflictPolicy,
    IconsetSourceRefV1,
    MAX_ICONSET_REFS,
    MAX_LINK_TYPES,
    MAX_RESOLVED_TYPE_KEYS,
    NodeTypeSourceV1,
    PROFILE_ID_PATTERN,
    ProfileIconsetRefV1,
    compute_icon_set_resolution_checksum,
    normalize_checksum,
    normalize_type_key,
    utcnow,
)

LAYOUT_SET_SCHEMA_VERSION = "v1"
LINK_SET_SCHEMA_VERSION = "v1"
GRAPH_TYPE_SCHEMA_VERSION = "v1"
AUTOCOMPLETE_SCHEMA_VERSION = "v1"
GRAPH_TYPE_RUNTIME_SCHEMA_VERSION = "v1"

LAYOUT_SET_ID_PATTERN = PROFILE_ID_PATTERN
LINK_SET_ID_PATTERN = PROFILE_ID_PATTERN
GRAPH_TYPE_ID_PATTERN = PROFILE_ID_PATTERN

MAX_LINK_SET_ENTRIES = MAX_LINK_TYPES
MAX_LAYOUT_SET_BYTES = 512_000
MAX_LAYOUT_SET_ENTRIES = 1024
MAX_ELK_PROPERTIES = 256
ELK_EDGE_TYPE_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
LAYOUT_SETTING_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
RESERVED_LAYOUT_SETTING_KEYS = {"type_icon_map", "edge_type_overrides"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def normalize_layout_set_id(value: str) -> str:
    normalized = str(value).strip().lower()
    if not LAYOUT_SET_ID_PATTERN.fullmatch(normalized):
        raise ValueError("layoutSetId must match ^[a-z0-9][a-z0-9_-]{1,63}$")
    return normalized


def normalize_layout_setting_key(value: str) -> str:
    normalized = str(value).strip()
    if not LAYOUT_SETTING_KEY_PATTERN.fullmatch(normalized):
        raise ValueError("layout setting key must match ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    if normalized in RESERVED_LAYOUT_SETTING_KEYS:
        raise ValueError(f"layout setting key '{normalized}' is reserved and cannot be set directly.")
    return normalized


def normalize_link_set_id(value: str) -> str:
    normalized = str(value).strip().lower()
    if not LINK_SET_ID_PATTERN.fullmatch(normalized):
        raise ValueError("linkSetId must match ^[a-z0-9][a-z0-9_-]{1,63}$")
    return normalized


def normalize_graph_type_id(value: str) -> str:
    normalized = str(value).strip().lower()
    if not GRAPH_TYPE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("graphTypeId must match ^[a-z0-9][a-z0-9_-]{1,63}$")
    return normalized


class LayoutSetEditableFieldsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    elkSettings: dict[str, Any]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("name must not be empty.")
        return text

    @field_validator("elkSettings")
    @classmethod
    def validate_elk_settings_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("elkSettings must not be empty.")
        if len(value) > MAX_LAYOUT_SET_ENTRIES:
            raise ValueError(f"elkSettings exceeds max size {MAX_LAYOUT_SET_ENTRIES}.")

        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = normalize_layout_setting_key(raw_key)
            if key in normalized:
                raise ValueError(f"Duplicate layout setting key '{raw_key}'.")
            normalized[key] = raw_value

        try:
            encoded = _canonical_json(normalized).encode("utf-8")
        except TypeError as exc:
            raise ValueError("elkSettings contains non-JSON-serializable values.") from exc
        if len(encoded) > MAX_LAYOUT_SET_BYTES:
            raise ValueError(f"elkSettings exceeds max payload size {MAX_LAYOUT_SET_BYTES} bytes.")
        return dict(sorted(normalized.items(), key=lambda item: item[0]))


class LayoutSetEntryUpsertRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any

    @field_validator("value")
    @classmethod
    def validate_value_json_serializable(cls, value: Any) -> Any:
        try:
            _canonical_json(value)
        except TypeError as exc:
            raise ValueError("value must be JSON-serializable.") from exc
        return value


class LayoutSetCreateRequestV1(LayoutSetEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    layoutSetId: str

    @field_validator("layoutSetId")
    @classmethod
    def validate_layout_set_id(cls, value: str) -> str:
        return normalize_layout_set_id(value)


class LayoutSetUpdateRequestV1(LayoutSetEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")


class LayoutSetBundleV1(LayoutSetEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = LAYOUT_SET_SCHEMA_VERSION
    layoutSetId: str
    layoutSetVersion: int = Field(ge=1)
    updatedAt: datetime
    checksum: str = Field(min_length=64, max_length=64)


class LayoutSetSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = LAYOUT_SET_SCHEMA_VERSION
    layoutSetId: str
    name: str
    draftVersion: int
    publishedVersion: int | None = None
    updatedAt: datetime
    checksum: str


class LayoutSetRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = LAYOUT_SET_SCHEMA_VERSION
    layoutSetId: str
    draft: LayoutSetBundleV1
    publishedVersions: list[LayoutSetBundleV1] = Field(default_factory=list)


class LayoutSetListResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layoutSets: list[LayoutSetSummaryV1]


class LayoutSetEntriesResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = LAYOUT_SET_SCHEMA_VERSION
    layoutSetId: str
    layoutSetVersion: int = Field(ge=1)
    stage: Literal["draft", "published"]
    checksum: str = Field(min_length=64, max_length=64)
    entries: dict[str, Any]


class LinkTypeDefinitionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    elkEdgeType: str | None = None
    elkProperties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("label must not be empty.")
        return text

    @field_validator("elkEdgeType")
    @classmethod
    def validate_elk_edge_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        if not ELK_EDGE_TYPE_PATTERN.fullmatch(normalized):
            raise ValueError("elkEdgeType must match ^[A-Z_][A-Z0-9_]*$.")
        return normalized

    @field_validator("elkProperties")
    @classmethod
    def validate_elk_properties(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > MAX_ELK_PROPERTIES:
            raise ValueError(f"elkProperties exceeds max size {MAX_ELK_PROPERTIES}.")
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()
            if not key:
                raise ValueError("elkProperties keys must not be empty.")
            normalized[key] = raw_value
        return normalized


class LinkSetEntryUpsertRequestV1(LinkTypeDefinitionV1):
    model_config = ConfigDict(extra="forbid")


class LinkSetEditableFieldsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    entries: dict[str, LinkTypeDefinitionV1] = Field(min_length=1, max_length=MAX_LINK_SET_ENTRIES)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("name must not be empty.")
        return text

    @field_validator("entries")
    @classmethod
    def validate_entries(cls, value: dict[str, LinkTypeDefinitionV1]) -> dict[str, LinkTypeDefinitionV1]:
        if not value:
            raise ValueError("entries must not be empty.")

        normalized: dict[str, LinkTypeDefinitionV1] = {}
        for raw_key, definition in value.items():
            key = normalize_type_key(raw_key)
            if key in normalized:
                raise ValueError(f"Duplicate link type key '{raw_key}'.")
            normalized[key] = LinkTypeDefinitionV1.model_validate(definition)

        if len(normalized) > MAX_LINK_SET_ENTRIES:
            raise ValueError(f"entries exceeds max size {MAX_LINK_SET_ENTRIES}.")

        return dict(sorted(normalized.items(), key=lambda item: item[0]))


class LinkSetCreateRequestV1(LinkSetEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    linkSetId: str

    @field_validator("linkSetId")
    @classmethod
    def validate_link_set_id(cls, value: str) -> str:
        return normalize_link_set_id(value)


class LinkSetUpdateRequestV1(LinkSetEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")


class LinkSetBundleV1(LinkSetEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = LINK_SET_SCHEMA_VERSION
    linkSetId: str
    linkSetVersion: int = Field(ge=1)
    updatedAt: datetime
    checksum: str = Field(min_length=64, max_length=64)


class LinkSetSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = LINK_SET_SCHEMA_VERSION
    linkSetId: str
    name: str
    draftVersion: int
    publishedVersion: int | None = None
    updatedAt: datetime
    checksum: str


class LinkSetRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = LINK_SET_SCHEMA_VERSION
    linkSetId: str
    draft: LinkSetBundleV1
    publishedVersions: list[LinkSetBundleV1] = Field(default_factory=list)


class LinkSetListResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    linkSets: list[LinkSetSummaryV1]


class LinkSetEntriesResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = LINK_SET_SCHEMA_VERSION
    linkSetId: str
    linkSetVersion: int = Field(ge=1)
    stage: Literal["draft", "published"]
    checksum: str = Field(min_length=64, max_length=64)
    entries: dict[str, LinkTypeDefinitionV1]


class LayoutSetRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layoutSetId: str
    layoutSetVersion: int = Field(ge=1)
    checksum: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("layoutSetId")
    @classmethod
    def validate_layout_set_id(cls, value: str) -> str:
        return normalize_layout_set_id(value)

    @field_validator("checksum")
    @classmethod
    def validate_checksum(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_checksum(value)


class LinkSetRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    linkSetId: str
    linkSetVersion: int = Field(ge=1)
    checksum: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("linkSetId")
    @classmethod
    def validate_link_set_id(cls, value: str) -> str:
        return normalize_link_set_id(value)

    @field_validator("checksum")
    @classmethod
    def validate_checksum(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_checksum(value)


class GraphTypeEditableFieldsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    layoutSetRef: LayoutSetRefV1
    iconSetRefs: list[ProfileIconsetRefV1] = Field(min_length=1, max_length=MAX_ICONSET_REFS)
    linkSetRef: LinkSetRefV1
    iconConflictPolicy: IconConflictPolicy = "reject"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("name must not be empty.")
        return text

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


class GraphTypeCreateRequestV1(GraphTypeEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    graphTypeId: str

    @field_validator("graphTypeId")
    @classmethod
    def validate_graph_type_id(cls, value: str) -> str:
        return normalize_graph_type_id(value)


class GraphTypeUpdateRequestV1(GraphTypeEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")


class GraphTypeBundleV1(GraphTypeEditableFieldsV1):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = GRAPH_TYPE_SCHEMA_VERSION
    graphTypeId: str
    graphTypeVersion: int = Field(ge=1)
    nodeTypes: list[str] = Field(min_length=1, max_length=MAX_RESOLVED_TYPE_KEYS)
    linkTypes: list[str] = Field(min_length=1, max_length=MAX_LINK_TYPES)
    typeIconMap: dict[str, str] = Field(min_length=1, max_length=MAX_RESOLVED_TYPE_KEYS)
    edgeTypeOverrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    iconSetResolutionChecksum: str = Field(min_length=64, max_length=64)
    runtimeChecksum: str = Field(min_length=64, max_length=64)
    elkSettings: dict[str, Any]
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

    @field_validator("linkTypes")
    @classmethod
    def validate_link_types(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            key = normalize_type_key(raw)
            if key in seen:
                raise ValueError(f"Duplicate link type '{raw}'.")
            seen.add(key)
            normalized.append(key)

        if len(normalized) > MAX_LINK_TYPES:
            raise ValueError(f"linkTypes exceeds max size {MAX_LINK_TYPES}.")

        return sorted(normalized)

    @field_validator("typeIconMap")
    @classmethod
    def validate_type_icon_map(cls, values: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_key, raw_value in values.items():
            key = normalize_type_key(raw_key)
            icon = str(raw_value).strip().lower()
            if key in normalized:
                raise ValueError(f"Duplicate node type key '{raw_key}'.")
            normalized[key] = icon

        if len(normalized) > MAX_RESOLVED_TYPE_KEYS:
            raise ValueError(f"typeIconMap exceeds max size {MAX_RESOLVED_TYPE_KEYS}.")

        return dict(sorted(normalized.items(), key=lambda item: item[0]))

    @model_validator(mode="after")
    def validate_maps(self) -> "GraphTypeBundleV1":
        if sorted(self.nodeTypes) != sorted(self.typeIconMap.keys()):
            raise ValueError("nodeTypes must exactly match typeIconMap keys.")
        return self


class GraphTypeSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = GRAPH_TYPE_SCHEMA_VERSION
    graphTypeId: str
    name: str
    draftVersion: int
    publishedVersion: int | None = None
    updatedAt: datetime
    checksum: str
    runtimeChecksum: str
    iconSetResolutionChecksum: str


class GraphTypeRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = GRAPH_TYPE_SCHEMA_VERSION
    graphTypeId: str
    draft: GraphTypeBundleV1
    publishedVersions: list[GraphTypeBundleV1] = Field(default_factory=list)


class GraphTypeListResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graphTypes: list[GraphTypeSummaryV1]


class GraphTypeRuntimeResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = GRAPH_TYPE_RUNTIME_SCHEMA_VERSION
    graphTypeId: str
    graphTypeVersion: int = Field(ge=1)
    graphTypeChecksum: str = Field(min_length=64, max_length=64)
    runtimeChecksum: str = Field(min_length=64, max_length=64)
    conflictPolicy: IconConflictPolicy
    resolvedEntries: dict[str, str]
    sources: list[IconsetSourceRefV1]
    keySources: dict[str, NodeTypeSourceV1]
    linkTypes: list[str]
    edgeTypeOverrides: dict[str, dict[str, Any]]
    checksum: str = Field(min_length=64, max_length=64)


class AutocompleteCatalogResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = AUTOCOMPLETE_SCHEMA_VERSION
    graphTypeId: str
    graphTypeVersion: int = Field(ge=1)
    graphTypeChecksum: str = Field(min_length=64, max_length=64)
    runtimeChecksum: str = Field(min_length=64, max_length=64)
    iconSetResolutionChecksum: str = Field(min_length=64, max_length=64)
    checksum: str = Field(min_length=64, max_length=64)
    nodeTypes: list[str]
    linkTypes: list[str]


def canonical_layout_set_bundle_payload(bundle_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": LAYOUT_SET_SCHEMA_VERSION,
        "layoutSetId": bundle_data["layoutSetId"],
        "layoutSetVersion": bundle_data["layoutSetVersion"],
        "name": bundle_data["name"],
        "elkSettings": bundle_data["elkSettings"],
    }


def compute_layout_set_checksum(bundle_data: dict[str, Any]) -> str:
    return _sha256_hex(canonical_layout_set_bundle_payload(bundle_data))


def canonical_link_set_bundle_payload(bundle_data: dict[str, Any]) -> dict[str, Any]:
    def _normalize_entry(value: Any) -> dict[str, Any]:
        if isinstance(value, LinkTypeDefinitionV1):
            payload = value.model_dump(mode="json")
        elif isinstance(value, dict):
            payload = dict(value)
        else:
            payload = LinkTypeDefinitionV1.model_validate(value).model_dump(mode="json")
        return {
            "label": payload["label"],
            "elkEdgeType": payload.get("elkEdgeType"),
            "elkProperties": payload.get("elkProperties", {}),
        }

    entries = {
        key: _normalize_entry(value)
        for key, value in sorted(bundle_data["entries"].items(), key=lambda item: item[0])
    }
    return {
        "schemaVersion": LINK_SET_SCHEMA_VERSION,
        "linkSetId": bundle_data["linkSetId"],
        "linkSetVersion": bundle_data["linkSetVersion"],
        "name": bundle_data["name"],
        "entries": entries,
    }


def compute_link_set_checksum(bundle_data: dict[str, Any]) -> str:
    return _sha256_hex(canonical_link_set_bundle_payload(bundle_data))


def canonical_graph_type_runtime_payload(bundle_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": GRAPH_TYPE_RUNTIME_SCHEMA_VERSION,
        "graphTypeId": bundle_data["graphTypeId"],
        "graphTypeVersion": bundle_data["graphTypeVersion"],
        "layoutSetRef": bundle_data["layoutSetRef"],
        "iconSetRefs": bundle_data["iconSetRefs"],
        "linkSetRef": bundle_data["linkSetRef"],
        "iconConflictPolicy": bundle_data["iconConflictPolicy"],
        "nodeTypes": sorted(bundle_data["nodeTypes"]),
        "linkTypes": sorted(bundle_data["linkTypes"]),
        "typeIconMap": dict(sorted(bundle_data["typeIconMap"].items(), key=lambda item: item[0])),
        "edgeTypeOverrides": dict(sorted(bundle_data["edgeTypeOverrides"].items(), key=lambda item: item[0])),
        "iconSetResolutionChecksum": bundle_data["iconSetResolutionChecksum"],
    }


def compute_graph_type_runtime_checksum(bundle_data: dict[str, Any]) -> str:
    return _sha256_hex(canonical_graph_type_runtime_payload(bundle_data))


def canonical_graph_type_bundle_payload(bundle_data: dict[str, Any]) -> dict[str, Any]:
    payload = canonical_graph_type_runtime_payload(bundle_data)
    payload.update(
        {
            "schemaVersion": GRAPH_TYPE_SCHEMA_VERSION,
            "name": bundle_data["name"],
            "elkSettings": bundle_data["elkSettings"],
            "runtimeChecksum": bundle_data["runtimeChecksum"],
        }
    )
    return payload


def compute_graph_type_checksum(bundle_data: dict[str, Any]) -> str:
    return _sha256_hex(canonical_graph_type_bundle_payload(bundle_data))


def compute_autocomplete_checksum(bundle: GraphTypeBundleV1) -> str:
    payload = {
        "schemaVersion": AUTOCOMPLETE_SCHEMA_VERSION,
        "graphTypeId": bundle.graphTypeId,
        "graphTypeVersion": bundle.graphTypeVersion,
        "graphTypeChecksum": bundle.checksum,
        "runtimeChecksum": bundle.runtimeChecksum,
        "iconSetResolutionChecksum": bundle.iconSetResolutionChecksum,
        "nodeTypes": bundle.nodeTypes,
        "linkTypes": bundle.linkTypes,
    }
    return _sha256_hex(payload)


def build_edge_type_overrides(
    *,
    base_edge_defaults: dict[str, Any],
    link_entries: dict[str, LinkTypeDefinitionV1],
) -> dict[str, dict[str, Any]]:
    overrides: dict[str, dict[str, Any]] = {}
    for key, definition in sorted(link_entries.items(), key=lambda item: item[0]):
        payload = copy.deepcopy(base_edge_defaults)
        properties = dict(payload.get("properties", {}))
        if definition.elkEdgeType:
            properties["org.eclipse.elk.edge.type"] = definition.elkEdgeType
        properties.update(definition.elkProperties)
        payload["properties"] = properties
        overrides[key] = payload
    return overrides


__all__ = [
    "AutocompleteCatalogResponseV1",
    "GraphTypeBundleV1",
    "GraphTypeCreateRequestV1",
    "GraphTypeListResponseV1",
    "GraphTypeRecordV1",
    "GraphTypeRuntimeResponseV1",
    "GraphTypeSummaryV1",
    "GraphTypeUpdateRequestV1",
    "LAYOUT_SET_SCHEMA_VERSION",
    "LINK_SET_SCHEMA_VERSION",
    "GRAPH_TYPE_SCHEMA_VERSION",
    "AUTOCOMPLETE_SCHEMA_VERSION",
    "GRAPH_TYPE_RUNTIME_SCHEMA_VERSION",
    "LayoutSetBundleV1",
    "LayoutSetCreateRequestV1",
    "LayoutSetEntryUpsertRequestV1",
    "LayoutSetEntriesResponseV1",
    "LayoutSetListResponseV1",
    "LayoutSetRecordV1",
    "LayoutSetRefV1",
    "LayoutSetSummaryV1",
    "LayoutSetUpdateRequestV1",
    "LinkSetBundleV1",
    "LinkSetCreateRequestV1",
    "LinkSetEntryUpsertRequestV1",
    "LinkSetEntriesResponseV1",
    "LinkSetListResponseV1",
    "LinkSetRecordV1",
    "LinkSetRefV1",
    "LinkSetSummaryV1",
    "LinkSetUpdateRequestV1",
    "LinkTypeDefinitionV1",
    "build_edge_type_overrides",
    "compute_autocomplete_checksum",
    "compute_graph_type_checksum",
    "compute_graph_type_runtime_checksum",
    "compute_layout_set_checksum",
    "compute_link_set_checksum",
    "normalize_graph_type_id",
    "normalize_layout_set_id",
    "normalize_layout_setting_key",
    "normalize_link_set_id",
    "utcnow",
    "compute_icon_set_resolution_checksum",
    "IconsetSourceRefV1",
    "NodeTypeSourceV1",
]
