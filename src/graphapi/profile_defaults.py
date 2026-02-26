from __future__ import annotations

from graphloom import sample_settings

from .profile_contract import ProfileCreateRequestV1

_DEFAULT_LINK_TYPES = [
    "directed",
    "undirected",
    "association",
    "dependency",
    "generalization",
    "none",
]


def default_profile_create_request() -> ProfileCreateRequestV1:
    defaults = sample_settings()
    node_types = sorted({str(key).lower() for key in defaults.type_icon_map.keys()})

    return ProfileCreateRequestV1.model_validate(
        {
            "profileId": "default",
            "name": "Default Layout Profile",
            "nodeTypes": node_types,
            "linkTypes": _DEFAULT_LINK_TYPES,
            "elkSettings": defaults.model_dump(by_alias=True, exclude_none=True, mode="json"),
        }
    )
