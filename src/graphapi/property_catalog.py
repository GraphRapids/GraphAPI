from __future__ import annotations

import hashlib
import json
from enum import Enum
from functools import lru_cache
from types import UnionType
from typing import Any, Union, get_args, get_origin

from graphloom import (
    EdgeLayoutOptions,
    LabelLayoutOptions,
    NodeLayoutOptions,
    ParentLayoutOptions,
    PortLayoutOptions,
    sample_settings,
)

from .graph_type_contract import (
    EDGE_MARKER_VALUES,
    EDGE_STYLE_VALUES,
    GRAPH_RAPIDS_EDGE_MARKER_END_KEY,
    GRAPH_RAPIDS_EDGE_MARKER_START_KEY,
    GRAPH_RAPIDS_EDGE_STYLE_KEY,
)
from .property_catalog_contract import (
    PROPERTY_CATALOG_SCHEMA_VERSION,
    PropertyCatalogElementV1,
    PropertyCatalogResponseV1,
    PropertyDefinitionV1,
)

_MODEL_BY_ELEMENT: dict[PropertyCatalogElementV1, type[Any]] = {
    "canvas": ParentLayoutOptions,
    "node": NodeLayoutOptions,
    "subgraph": NodeLayoutOptions,
    "edge": EdgeLayoutOptions,
    "port": PortLayoutOptions,
    "label": LabelLayoutOptions,
}

_WRITABLE_IN_BY_ELEMENT: dict[PropertyCatalogElementV1, list[str]] = {
    "canvas": ["layoutSet.elkSettings"],
    "node": ["layoutSet.elkSettings"],
    "subgraph": ["layoutSet.elkSettings"],
    "edge": ["layoutSet.elkSettings", "linkSet.entries[*].elkProperties"],
    "port": ["layoutSet.elkSettings"],
    "label": ["layoutSet.elkSettings"],
}


def build_property_catalog(
    *,
    element: PropertyCatalogElementV1 | None = None,
) -> PropertyCatalogResponseV1:
    return _build_catalog_cached(element)


@lru_cache(maxsize=16)
def _build_catalog_cached(element: PropertyCatalogElementV1 | None) -> PropertyCatalogResponseV1:
    all_elements = _all_elements_catalog()
    if element is not None:
        elements = {element: all_elements[element]}
    else:
        elements = all_elements

    payload = {
        "schemaVersion": PROPERTY_CATALOG_SCHEMA_VERSION,
        "elements": elements,
    }
    payload["checksum"] = _sha256_hex(payload)
    return PropertyCatalogResponseV1.model_validate(payload)


@lru_cache(maxsize=1)
def _all_elements_catalog() -> dict[PropertyCatalogElementV1, list[dict[str, Any]]]:
    defaults = _defaults_by_element()
    catalog: dict[PropertyCatalogElementV1, list[dict[str, Any]]] = {}
    for element, model_cls in _MODEL_BY_ELEMENT.items():
        properties = _definitions_for_model(
            model_cls=model_cls,
            element=element,
            defaults=defaults.get(element, {}),
        )
        if element == "edge":
            properties = _inject_graphrapids_edge_properties(
                properties=properties,
                defaults=defaults.get("edge", {}),
            )
        catalog[element] = properties
    return catalog


def _definitions_for_model(
    *,
    model_cls: type[Any],
    element: PropertyCatalogElementV1,
    defaults: dict[str, Any],
) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    writable_in = _WRITABLE_IN_BY_ELEMENT[element]
    for name, field in model_cls.model_fields.items():
        key = field.alias or name
        value_type, enum_values = _analyze_annotation(field.annotation)
        definition = PropertyDefinitionV1(
            key=key,
            valueType=value_type,
            enumValues=enum_values or None,
            defaultValue=defaults.get(key),
            writableIn=writable_in,
        )
        definitions.append(definition.model_dump(mode="json", exclude_none=True))
    definitions.sort(key=lambda item: item["key"])
    return definitions


def _inject_graphrapids_edge_properties(
    *,
    properties: list[dict[str, Any]],
    defaults: dict[str, Any],
) -> list[dict[str, Any]]:
    by_key = {item["key"]: item for item in properties}
    writable_in = _WRITABLE_IN_BY_ELEMENT["edge"]

    by_key[GRAPH_RAPIDS_EDGE_MARKER_START_KEY] = PropertyDefinitionV1(
        key=GRAPH_RAPIDS_EDGE_MARKER_START_KEY,
        valueType="enum",
        enumValues=sorted(EDGE_MARKER_VALUES),
        defaultValue=defaults.get(GRAPH_RAPIDS_EDGE_MARKER_START_KEY, "NONE"),
        writableIn=writable_in,
    ).model_dump(mode="json", exclude_none=True)
    by_key[GRAPH_RAPIDS_EDGE_MARKER_END_KEY] = PropertyDefinitionV1(
        key=GRAPH_RAPIDS_EDGE_MARKER_END_KEY,
        valueType="enum",
        enumValues=sorted(EDGE_MARKER_VALUES),
        defaultValue=defaults.get(GRAPH_RAPIDS_EDGE_MARKER_END_KEY, "NONE"),
        writableIn=writable_in,
    ).model_dump(mode="json", exclude_none=True)
    by_key[GRAPH_RAPIDS_EDGE_STYLE_KEY] = PropertyDefinitionV1(
        key=GRAPH_RAPIDS_EDGE_STYLE_KEY,
        valueType="enum",
        enumValues=sorted(EDGE_STYLE_VALUES),
        defaultValue=defaults.get(GRAPH_RAPIDS_EDGE_STYLE_KEY, "SOLID"),
        writableIn=writable_in,
    ).model_dump(mode="json", exclude_none=True)

    return sorted(by_key.values(), key=lambda item: item["key"])


def _defaults_by_element() -> dict[PropertyCatalogElementV1, dict[str, Any]]:
    settings = sample_settings().model_dump(by_alias=True, exclude_none=True, mode="json")
    node_defaults = settings.get("node_defaults", {})
    subgraph_defaults = settings.get("subgraph_defaults", {})
    edge_defaults = settings.get("edge_defaults", {})

    return {
        "canvas": dict(settings.get("layout_options", {})),
        "node": dict(node_defaults.get("properties", {})),
        "subgraph": dict(subgraph_defaults.get("properties", {})),
        "edge": dict(edge_defaults.get("properties", {})),
        "port": _merge_default_maps(
            dict(node_defaults.get("port", {}).get("properties", {})),
            dict(subgraph_defaults.get("port", {}).get("properties", {})),
        ),
        "label": _merge_default_maps(
            dict(node_defaults.get("label", {}).get("properties", {})),
            dict(subgraph_defaults.get("label", {}).get("properties", {})),
            dict(edge_defaults.get("label", {}).get("properties", {})),
            dict(node_defaults.get("port", {}).get("label", {}).get("properties", {})),
            dict(subgraph_defaults.get("port", {}).get("label", {}).get("properties", {})),
        ),
    }


def _merge_default_maps(*items: dict[str, Any]) -> dict[str, Any]:
    if not items:
        return {}
    all_keys = {key for item in items for key in item}
    merged: dict[str, Any] = {}
    for key in all_keys:
        candidates = [item[key] for item in items if key in item]
        if not candidates:
            continue
        first = candidates[0]
        if all(value == first for value in candidates):
            merged[key] = first
    return merged


def _analyze_annotation(annotation: Any) -> tuple[str, list[str] | None]:
    enum_cls, is_array, primitive = _resolve_annotation(annotation)
    if enum_cls is not None:
        value_type = "enum_array" if is_array else "enum"
        return value_type, [str(item.value) for item in enum_cls]

    if primitive is bool:
        return "boolean", None
    if primitive is int:
        return "integer", None
    if primitive is float:
        return "number", None
    if primitive is str:
        return "string", None
    if primitive is list:
        return "array", None
    if primitive is dict:
        return "object", None
    return "json", None


def _resolve_annotation(annotation: Any) -> tuple[type[Enum] | None, bool, type[Any] | None]:
    origin = get_origin(annotation)
    if origin is None:
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            return annotation, False, None
        if annotation in {bool, int, float, str, list, dict}:
            return None, False, annotation
        return None, False, None

    if origin in {list, tuple, set}:
        args = [arg for arg in get_args(annotation) if arg is not type(None)]  # noqa: E721
        if not args:
            return None, True, list
        enum_cls, _, primitive = _resolve_annotation(args[0])
        if enum_cls is not None:
            return enum_cls, True, None
        return None, True, primitive or list

    if origin is dict:
        return None, False, dict

    if origin in {Union, UnionType}:
        args = [arg for arg in get_args(annotation) if arg is not type(None)]  # noqa: E721
        enum_cls: type[Enum] | None = None
        is_array = False
        primitive: type[Any] | None = None
        for arg in args:
            arg_enum, arg_is_array, arg_primitive = _resolve_annotation(arg)
            if arg_enum is not None:
                enum_cls = arg_enum
                is_array = is_array or arg_is_array
            if arg_primitive is not None:
                primitive = arg_primitive
        return enum_cls, is_array, primitive

    return None, False, None


def _sha256_hex(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
