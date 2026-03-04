from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROPERTY_CATALOG_SCHEMA_VERSION = "v1"

PropertyCatalogElementV1 = Literal[
    "canvas",
    "node",
    "subgraph",
    "edge",
    "port",
    "label",
]


class PropertyDefinitionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    valueType: str
    enumValues: list[str] | None = None
    defaultValue: Any = None
    writableIn: list[str] = Field(default_factory=list)


class PropertyCatalogResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["v1"] = PROPERTY_CATALOG_SCHEMA_VERSION
    checksum: str = Field(min_length=64, max_length=64)
    elements: dict[PropertyCatalogElementV1, list[PropertyDefinitionV1]]

