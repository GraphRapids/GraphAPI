from __future__ import annotations

from graphloom import sample_settings

from .graph_type_contract import LayoutSetCreateRequestV1


def default_layout_set_create_request() -> LayoutSetCreateRequestV1:
    settings = sample_settings().model_dump(by_alias=True, exclude_none=True, mode="json")
    settings["type_icon_map"] = {}
    settings["edge_type_overrides"] = {}

    return LayoutSetCreateRequestV1.model_validate(
        {
            "layoutSetId": "default",
            "name": "Default Layout Set",
            "elkSettings": settings,
        }
    )
