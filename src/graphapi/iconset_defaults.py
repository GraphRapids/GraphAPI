from __future__ import annotations

from graphloom import sample_settings

from .profile_v2_contract import IconsetCreateRequestV1, normalize_iconify_name, normalize_type_key


def default_iconset_create_request() -> IconsetCreateRequestV1:
    settings = sample_settings()
    entries: dict[str, str] = {}

    for raw_key, raw_value in settings.type_icon_map.items():
        key = normalize_type_key(raw_key)
        value = normalize_iconify_name(raw_value)
        entries[key] = value

    return IconsetCreateRequestV1.model_validate(
        {
            "iconsetId": "default",
            "name": "Default Node Type Iconset",
            "entries": entries,
        }
    )
