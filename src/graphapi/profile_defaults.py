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
    settings = sample_settings().model_dump(by_alias=True, exclude_none=True, mode="json")
    settings["type_icon_map"] = {}

    return ProfileCreateRequestV1.model_validate(
        {
            "profileId": "default",
            "name": "Default Layout Profile",
            "linkTypes": _DEFAULT_LINK_TYPES,
            "elkSettings": settings,
            "iconSetRefs": [
                {
                    "iconSetId": "default",
                    "iconSetVersion": 1,
                }
            ],
            "iconConflictPolicy": "reject",
        }
    )
