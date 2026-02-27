from __future__ import annotations

import sqlite3

import pytest
from graphloom import sample_settings

from graphapi.iconset_defaults import default_iconset_create_request
from graphapi.iconset_store import IconsetStore
from graphapi.profile_contract import IconsetCreateRequestV1, ProfileCreateRequestV1, ProfileUpdateRequestV1
from graphapi.profile_defaults import default_profile_create_request
from graphapi.profile_store import ProfileStore, ProfileStoreError


def _base_elk_settings() -> dict:
    settings = sample_settings().model_dump(by_alias=True, exclude_none=True, mode="json")
    settings["type_icon_map"] = {}
    return settings


def _create_request(
    profile_id: str,
    *,
    refs: list[dict] | None = None,
    conflict_policy: str = "reject",
    elk_settings: dict | None = None,
) -> ProfileCreateRequestV1:
    return ProfileCreateRequestV1.model_validate(
        {
            "profileId": profile_id,
            "name": f"{profile_id} profile",
            "linkTypes": ["directed", "dependency"],
            "elkSettings": elk_settings if elk_settings is not None else _base_elk_settings(),
            "iconSetRefs": refs
            if refs is not None
            else [{"iconSetId": "default", "iconSetVersion": 1}],
            "iconConflictPolicy": conflict_policy,
        }
    )


@pytest.fixture()
def store_and_db(tmp_path):
    db_path = tmp_path / "runtime.v1.sqlite3"
    iconset_store = IconsetStore(db_path)
    iconset_store.ensure_default_iconset(default_iconset_create_request())
    store = ProfileStore(db_path, iconset_store)
    return store, iconset_store, db_path


def test_default_profile_is_created_once_and_published(store_and_db) -> None:
    store, _, _ = store_and_db

    request = default_profile_create_request()
    store.ensure_default_profile(request)
    store.ensure_default_profile(request)

    listed = store.list_profiles()
    assert [item.profileId for item in listed.profiles] == ["default"]
    assert listed.profiles[0].draftVersion == 1
    assert listed.profiles[0].publishedVersion == 1

    profile = store.get_profile("default")
    assert profile.draft.profileVersion == 1
    assert len(profile.publishedVersions) == 1


def test_profile_crud_publish_resolution_and_catalog(store_and_db) -> None:
    store, _, _ = store_and_db

    created = store.create_profile(_create_request("team"))
    assert created.profileId == "team"
    assert created.draft.profileVersion == 1
    assert created.publishedVersions == []

    updated = store.update_profile(
        "team",
        ProfileUpdateRequestV1.model_validate(
            {
                "name": "team profile v2",
                "linkTypes": ["directed", "association"],
                "elkSettings": _base_elk_settings(),
                "iconSetRefs": [{"iconSetId": "default", "iconSetVersion": 1}],
                "iconConflictPolicy": "reject",
            }
        ),
    )
    assert updated.draft.profileVersion == 2
    assert updated.draft.linkTypes == ["directed", "association"]

    published = store.publish_profile("team")
    assert published.profileVersion == 2

    latest_published = store.get_bundle("team", stage="published")
    assert latest_published.profileVersion == 2

    draft_bundle = store.get_bundle("team", stage="draft", profile_version=2)
    assert draft_bundle.profileVersion == 2

    resolution = store.get_iconset_resolution("team", stage="published")
    assert resolution.profileId == "team"
    assert resolution.profileVersion == 2
    assert resolution.resolvedEntries
    assert resolution.checksum

    catalog = store.get_autocomplete_catalog("team", stage="published")
    assert catalog.profileId == "team"
    assert catalog.profileVersion == 2
    assert catalog.nodeTypes
    assert catalog.linkTypes == ["directed", "association"]


def test_profile_store_error_paths(store_and_db) -> None:
    store, iconset_store, _ = store_and_db

    create_request = _create_request("errors")
    store.create_profile(create_request)

    with pytest.raises(ProfileStoreError) as duplicate_exc:
        store.create_profile(create_request)
    assert duplicate_exc.value.status_code == 409
    assert duplicate_exc.value.code == "PROFILE_ALREADY_EXISTS"

    with pytest.raises(ProfileStoreError) as missing_update_exc:
        store.update_profile(
            "missing",
            ProfileUpdateRequestV1.model_validate(
                {
                    "name": "missing",
                    "linkTypes": ["directed"],
                    "elkSettings": _base_elk_settings(),
                    "iconSetRefs": [{"iconSetId": "default", "iconSetVersion": 1}],
                    "iconConflictPolicy": "reject",
                }
            ),
        )
    assert missing_update_exc.value.status_code == 404
    assert missing_update_exc.value.code == "PROFILE_NOT_FOUND"

    with pytest.raises(ProfileStoreError) as unpublished_exc:
        store.get_bundle("errors", stage="published")
    assert unpublished_exc.value.status_code == 404
    assert unpublished_exc.value.code == "PROFILE_NOT_PUBLISHED"

    store.publish_profile("errors")

    with pytest.raises(ProfileStoreError) as duplicate_publish_exc:
        store.publish_profile("errors")
    assert duplicate_publish_exc.value.status_code == 409
    assert duplicate_publish_exc.value.code == "PROFILE_VERSION_ALREADY_PUBLISHED"

    with pytest.raises(ProfileStoreError) as missing_version_exc:
        store.get_bundle("errors", stage="draft", profile_version=999)
    assert missing_version_exc.value.status_code == 404
    assert missing_version_exc.value.code == "PROFILE_VERSION_NOT_FOUND"

    with pytest.raises(ProfileStoreError) as missing_published_version_exc:
        store.get_bundle("errors", stage="published", profile_version=999)
    assert missing_published_version_exc.value.status_code == 404
    assert missing_published_version_exc.value.code == "PROFILE_VERSION_NOT_FOUND"

    with pytest.raises(ProfileStoreError) as invalid_ref_exc:
        store.create_profile(
            _create_request(
                "missing-ref",
                refs=[{"iconSetId": "does-not-exist", "iconSetVersion": 1}],
            )
        )
    assert invalid_ref_exc.value.status_code == 404
    assert invalid_ref_exc.value.code == "PROFILE_ICONSET_REF_INVALID"

    with pytest.raises(ProfileStoreError) as bad_checksum_exc:
        store.create_profile(
            _create_request(
                "bad-checksum",
                refs=[
                    {
                        "iconSetId": "default",
                        "iconSetVersion": 1,
                        "checksum": "0" * 64,
                    }
                ],
            )
        )
    assert bad_checksum_exc.value.status_code == 409
    assert bad_checksum_exc.value.code == "PROFILE_ICONSET_REF_INVALID"

    iconset_store.create_iconset(
        IconsetCreateRequestV1.model_validate(
            {
                "iconSetId": "conflict-a",
                "name": "Conflict A",
                "entries": {"router": "mdi:router"},
            }
        )
    )
    iconset_store.publish_iconset("conflict-a")

    iconset_store.create_iconset(
        IconsetCreateRequestV1.model_validate(
            {
                "iconSetId": "conflict-b",
                "name": "Conflict B",
                "entries": {"router": "mdi:router-wireless"},
            }
        )
    )
    iconset_store.publish_iconset("conflict-b")

    with pytest.raises(ProfileStoreError) as conflict_exc:
        store.create_profile(
            _create_request(
                "conflict",
                refs=[
                    {"iconSetId": "conflict-a", "iconSetVersion": 1},
                    {"iconSetId": "conflict-b", "iconSetVersion": 1},
                ],
                conflict_policy="reject",
            )
        )
    assert conflict_exc.value.status_code == 409
    assert conflict_exc.value.code == "ICONSET_KEY_CONFLICT"

    with pytest.raises(ProfileStoreError) as invalid_elk_exc:
        store.create_profile(
            _create_request(
                "bad-elk",
                elk_settings={"type_icon_map": "not-a-dict"},
            )
        )
    assert invalid_elk_exc.value.status_code == 400
    assert invalid_elk_exc.value.code == "INVALID_ELK_SETTINGS"


def test_profile_store_reports_corrupted_payloads(store_and_db) -> None:
    store, _, db_path = store_and_db

    store.create_profile(_create_request("corrupt"))

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE profiles SET draft_payload = ? WHERE profile_id = ?", ("{not-json", "corrupt"))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ProfileStoreError) as exc:
        store.get_profile("corrupt")
    assert exc.value.status_code == 500
    assert exc.value.code == "PROFILE_STORAGE_CORRUPTED"
