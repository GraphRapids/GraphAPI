from __future__ import annotations

import hashlib
import json
import os
from importlib import resources
from typing import Literal

import anyio
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from graphloom import ElkSettings, MinimalGraphIn, build_canvas, layout_with_elkjs, sample_settings
from graphrender import GraphRender

from .iconset_defaults import default_iconset_create_request
from .iconset_store import IconsetStore, IconsetStoreError
from .profile_contract import (
    AutocompleteCatalogResponseV1,
    ErrorBody,
    ErrorResponse,
    IconsetBundleV1,
    IconsetCreateRequestV1,
    IconsetEntryUpsertRequestV1,
    IconsetListResponseV1,
    IconsetRecordV1,
    IconsetResolutionResultV1,
    IconsetResolveRequestV1,
    IconsetUpdateRequestV1,
    ProfileBundleV1,
    ProfileCreateRequestV1,
    ProfileIconsetResolutionResponseV1,
    ProfileListResponseV1,
    ProfileRecordV1,
    ProfileUpdateRequestV1,
    ThemeBundleV1,
    ThemeCreateRequestV1,
    ThemeListResponseV1,
    ThemeRecordV1,
    ThemeUpdateRequestV1,
    compute_iconset_resolution_checksum,
)
from .profile_defaults import default_profile_create_request
from .profile_store import ProfileStore, ProfileStoreError
from .theme_defaults import default_theme_create_request
from .theme_store import ThemeStore, ThemeStoreError

REQUEST_TIMEOUT_SECONDS = float(os.getenv("GRAPHAPI_REQUEST_TIMEOUT_SECONDS", "15"))
MAX_REQUEST_BYTES = int(os.getenv("GRAPHAPI_MAX_REQUEST_BYTES", "1048576"))

app = FastAPI(
    title="GraphAPI",
    description=(
        "GraphRapids runtime API. Includes layout profile + iconset + render theme management (v1) "
        "and graph render orchestration over GraphLoom + GraphRender."
    ),
    version="1.0.0",
)

cors_origins = [origin.strip() for origin in os.getenv("GRAPHAPI_CORS_ORIGINS", "*").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

iconset_store = IconsetStore.from_env()
iconset_store.ensure_default_iconset(default_iconset_create_request())

profile_store = ProfileStore.from_env(iconset_store)
profile_store.ensure_default_profile(default_profile_create_request())

theme_store = ThemeStore.from_env()
theme_store.ensure_default_theme(default_theme_create_request())


def _profile_http_error(exc: ProfileStoreError) -> HTTPException:
    body = ErrorResponse(
        error=ErrorBody(code=exc.code, message=exc.message, details=exc.details)
    )
    return HTTPException(status_code=exc.status_code, detail=body.model_dump()["error"])


def _theme_http_error(exc: ThemeStoreError) -> HTTPException:
    body = ErrorResponse(
        error=ErrorBody(code=exc.code, message=exc.message, details=exc.details)
    )
    return HTTPException(status_code=exc.status_code, detail=body.model_dump()["error"])


def _iconset_http_error(exc: IconsetStoreError) -> HTTPException:
    body = ErrorResponse(
        error=ErrorBody(code=exc.code, message=exc.message, details=exc.details)
    )
    return HTTPException(status_code=exc.status_code, detail=body.model_dump()["error"])


def _runtime_checksum(
    *,
    profile_checksum: str = "",
    theme_checksum: str = "",
    iconset_resolution_checksum: str = "",
) -> str:
    material = "|".join(
        [
            profile_checksum,
            theme_checksum,
            iconset_resolution_checksum,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def render_svg_from_graph(
    graph: MinimalGraphIn,
    *,
    profile_bundle: ProfileBundleV1 | None = None,
    theme_bundle: ThemeBundleV1 | None = None,
) -> str:
    if profile_bundle is not None:
        settings_payload = dict(profile_bundle.elkSettings)
        settings_payload["type_icon_map"] = profile_bundle.typeIconMap
        settings = ElkSettings.model_validate(settings_payload)
    else:
        settings = sample_settings()

    canvas = build_canvas(graph, settings)
    payload = canvas.model_dump(by_alias=True, exclude_none=True)

    try:
        payload = layout_with_elkjs(payload, mode="node", node_cmd="node")
    except Exception as exc:
        raise RuntimeError(f"Graph layout failed: {exc}") from exc

    try:
        if theme_bundle is not None:
            return GraphRender(
                payload,
                theme_css=theme_bundle.renderCss,
                embed_theme=True,
            ).to_string()

        return GraphRender(
            payload,
            embed_theme=True,
        ).to_string()
    except Exception as exc:
        raise RuntimeError(f"Graph render failed: {exc}") from exc


@app.middleware("http")
async def request_limits_and_timeout(request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                return JSONResponse(status_code=413, content={"detail": "Request body too large."})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header."})

    try:
        with anyio.fail_after(REQUEST_TIMEOUT_SECONDS):
            return await call_next(request)
    except TimeoutError:
        return JSONResponse(status_code=504, content={"detail": "Request timed out."})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/schemas/minimal-input.schema.json")
def minimal_input_schema() -> JSONResponse:
    schema_path = resources.files("graphloom").joinpath("schemas/minimal-input.schema.json")
    schema_text = schema_path.read_text(encoding="utf-8")
    return JSONResponse(content=json.loads(schema_text))


@app.get(
    "/v1/profiles",
    response_model=ProfileListResponseV1,
    tags=["profiles"],
)
def list_profiles_v1() -> ProfileListResponseV1:
    try:
        return profile_store.list_profiles()
    except ProfileStoreError as exc:
        raise _profile_http_error(exc) from exc


@app.get(
    "/v1/profiles/{id}",
    response_model=ProfileRecordV1,
    tags=["profiles"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_profile_v1(id: str) -> ProfileRecordV1:
    try:
        return profile_store.get_profile(id)
    except ProfileStoreError as exc:
        raise _profile_http_error(exc) from exc


@app.get(
    "/v1/profiles/{id}/bundle",
    response_model=ProfileBundleV1,
    tags=["profiles"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_profile_bundle_v1(
    id: str,
    stage: Literal["draft", "published"] = Query(default="published"),
    profile_version: int | None = Query(default=None, ge=1),
) -> ProfileBundleV1:
    try:
        return profile_store.get_bundle(
            id,
            stage=stage,
            profile_version=profile_version,
        )
    except ProfileStoreError as exc:
        raise _profile_http_error(exc) from exc


@app.post(
    "/v1/profiles",
    response_model=ProfileRecordV1,
    status_code=201,
    tags=["profiles"],
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def create_profile_v1(request: ProfileCreateRequestV1) -> ProfileRecordV1:
    try:
        return profile_store.create_profile(request)
    except ProfileStoreError as exc:
        raise _profile_http_error(exc) from exc


@app.put(
    "/v1/profiles/{id}",
    response_model=ProfileRecordV1,
    tags=["profiles"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def update_profile_v1(
    id: str,
    request: ProfileUpdateRequestV1,
) -> ProfileRecordV1:
    try:
        return profile_store.update_profile(id, request)
    except ProfileStoreError as exc:
        raise _profile_http_error(exc) from exc


@app.post(
    "/v1/profiles/{id}/publish",
    response_model=ProfileBundleV1,
    tags=["profiles"],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def publish_profile_v1(id: str) -> ProfileBundleV1:
    try:
        return profile_store.publish_profile(id)
    except ProfileStoreError as exc:
        raise _profile_http_error(exc) from exc


@app.get(
    "/v1/profiles/{id}/iconset-resolution",
    response_model=ProfileIconsetResolutionResponseV1,
    tags=["profiles"],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_profile_iconset_resolution_v1(
    id: str,
    stage: Literal["draft", "published"] = Query(default="published"),
    profile_version: int | None = Query(default=None, ge=1),
) -> ProfileIconsetResolutionResponseV1:
    try:
        return profile_store.get_iconset_resolution(
            id,
            stage=stage,
            profile_version=profile_version,
        )
    except ProfileStoreError as exc:
        raise _profile_http_error(exc) from exc


@app.get(
    "/v1/autocomplete/catalog",
    response_model=AutocompleteCatalogResponseV1,
    tags=["profiles"],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_autocomplete_catalog_v1(
    profile_id: str,
    stage: Literal["draft", "published"] = Query(default="published"),
    profile_version: int | None = Query(default=None, ge=1),
) -> AutocompleteCatalogResponseV1:
    try:
        return profile_store.get_autocomplete_catalog(
            profile_id,
            stage=stage,
            profile_version=profile_version,
        )
    except ProfileStoreError as exc:
        raise _profile_http_error(exc) from exc


@app.get(
    "/v1/iconsets",
    response_model=IconsetListResponseV1,
    tags=["iconsets"],
)
def list_iconsets_v1() -> IconsetListResponseV1:
    try:
        return iconset_store.list_iconsets()
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.get(
    "/v1/iconsets/{id}",
    response_model=IconsetRecordV1,
    tags=["iconsets"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_iconset_v1(id: str) -> IconsetRecordV1:
    try:
        return iconset_store.get_iconset(id)
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.get(
    "/v1/iconsets/{id}/bundle",
    response_model=IconsetBundleV1,
    tags=["iconsets"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_iconset_bundle_v1(
    id: str,
    stage: Literal["draft", "published"] = Query(default="published"),
    iconset_version: int | None = Query(default=None, ge=1),
) -> IconsetBundleV1:
    try:
        return iconset_store.get_bundle(
            id,
            stage=stage,
            iconset_version=iconset_version,
        )
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.post(
    "/v1/iconsets",
    response_model=IconsetRecordV1,
    status_code=201,
    tags=["iconsets"],
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def create_iconset_v1(request: IconsetCreateRequestV1) -> IconsetRecordV1:
    try:
        return iconset_store.create_iconset(request)
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.put(
    "/v1/iconsets/{id}",
    response_model=IconsetRecordV1,
    tags=["iconsets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def update_iconset_v1(
    id: str,
    request: IconsetUpdateRequestV1,
) -> IconsetRecordV1:
    try:
        return iconset_store.update_iconset(id, request)
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.put(
    "/v1/iconsets/{id}/entries/{key}",
    response_model=IconsetRecordV1,
    tags=["iconsets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def upsert_iconset_entry_v1(
    id: str,
    key: str,
    request: IconsetEntryUpsertRequestV1,
) -> IconsetRecordV1:
    try:
        return iconset_store.upsert_iconset_entry(id, key, request)
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.delete(
    "/v1/iconsets/{id}/entries/{key}",
    response_model=IconsetRecordV1,
    tags=["iconsets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def delete_iconset_entry_v1(
    id: str,
    key: str,
) -> IconsetRecordV1:
    try:
        return iconset_store.delete_iconset_entry(id, key)
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.post(
    "/v1/iconsets/{id}/publish",
    response_model=IconsetBundleV1,
    tags=["iconsets"],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def publish_iconset_v1(id: str) -> IconsetBundleV1:
    try:
        return iconset_store.publish_iconset(id)
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.post(
    "/v1/iconsets/resolve",
    response_model=IconsetResolutionResultV1,
    tags=["iconsets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def resolve_iconsets_v1(request: IconsetResolveRequestV1) -> IconsetResolutionResultV1:
    resolved_entries: dict[str, str] = {}
    sources = []
    key_sources: dict[str, dict] = {}

    for ref in request.iconsetRefs:
        try:
            bundle = iconset_store.get_bundle(
                ref.iconsetId,
                stage=ref.stage,
                iconset_version=ref.iconsetVersion,
            )
        except IconsetStoreError as exc:
            raise _iconset_http_error(exc) from exc

        source = {
            "iconsetId": bundle.iconsetId,
            "iconsetVersion": bundle.iconsetVersion,
            "checksum": bundle.checksum,
        }
        sources.append(source)

        for key, icon in bundle.entries.items():
            existing = resolved_entries.get(key)
            key_sources.setdefault(
                key,
                {
                    "key": key,
                    "icon": icon,
                    "selectedFrom": source,
                    "candidates": [],
                },
            )
            key_sources[key]["candidates"].append(source)

            if existing is None:
                resolved_entries[key] = icon
                continue

            if existing == icon:
                continue

            if request.conflictPolicy == "reject":
                body = ErrorResponse(
                    error=ErrorBody(
                        code="ICONSET_KEY_CONFLICT",
                        message=(
                            f"Node type key '{key}' maps to multiple icons under reject policy."
                        ),
                        details={
                            "key": key,
                            "existingIcon": existing,
                            "incomingIcon": icon,
                            "conflictPolicy": request.conflictPolicy,
                        },
                    )
                )
                raise HTTPException(status_code=409, detail=body.model_dump()["error"])

            if request.conflictPolicy == "last-wins":
                resolved_entries[key] = icon
                key_sources[key]["icon"] = icon
                key_sources[key]["selectedFrom"] = source

    if not resolved_entries:
        body = ErrorResponse(
            error=ErrorBody(
                code="PROFILE_ICONSET_REF_INVALID",
                message="Resolved iconset map is empty.",
            )
        )
        raise HTTPException(status_code=400, detail=body.model_dump()["error"])

    sorted_entries = dict(sorted(resolved_entries.items(), key=lambda item: item[0]))
    checksum = compute_iconset_resolution_checksum(
        conflict_policy=request.conflictPolicy,
        sources=sources,
        resolved_entries=sorted_entries,
    )

    return IconsetResolutionResultV1.model_validate(
        {
            "conflictPolicy": request.conflictPolicy,
            "resolvedEntries": sorted_entries,
            "sources": sources,
            "keySources": key_sources,
            "checksum": checksum,
        }
    )


@app.get(
    "/v1/themes",
    response_model=ThemeListResponseV1,
    tags=["themes"],
)
def list_themes_v1() -> ThemeListResponseV1:
    try:
        return theme_store.list_themes()
    except ThemeStoreError as exc:
        raise _theme_http_error(exc) from exc


@app.get(
    "/v1/themes/{id}",
    response_model=ThemeRecordV1,
    tags=["themes"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_theme_v1(id: str) -> ThemeRecordV1:
    try:
        return theme_store.get_theme(id)
    except ThemeStoreError as exc:
        raise _theme_http_error(exc) from exc


@app.get(
    "/v1/themes/{id}/bundle",
    response_model=ThemeBundleV1,
    tags=["themes"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_theme_bundle_v1(
    id: str,
    stage: Literal["draft", "published"] = Query(default="published"),
    theme_version: int | None = Query(default=None, ge=1),
) -> ThemeBundleV1:
    try:
        return theme_store.get_bundle(
            id,
            stage=stage,
            theme_version=theme_version,
        )
    except ThemeStoreError as exc:
        raise _theme_http_error(exc) from exc


@app.post(
    "/v1/themes",
    response_model=ThemeRecordV1,
    status_code=201,
    tags=["themes"],
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def create_theme_v1(request: ThemeCreateRequestV1) -> ThemeRecordV1:
    try:
        return theme_store.create_theme(request)
    except ThemeStoreError as exc:
        raise _theme_http_error(exc) from exc


@app.put(
    "/v1/themes/{id}",
    response_model=ThemeRecordV1,
    tags=["themes"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def update_theme_v1(
    id: str,
    request: ThemeUpdateRequestV1,
) -> ThemeRecordV1:
    try:
        return theme_store.update_theme(id, request)
    except ThemeStoreError as exc:
        raise _theme_http_error(exc) from exc


@app.post(
    "/v1/themes/{id}/publish",
    response_model=ThemeBundleV1,
    tags=["themes"],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def publish_theme_v1(id: str) -> ThemeBundleV1:
    try:
        return theme_store.publish_theme(id)
    except ThemeStoreError as exc:
        raise _theme_http_error(exc) from exc


@app.post("/validate")
def validate_graph(graph: MinimalGraphIn) -> dict[str, object]:
    return {"valid": True, "normalized": graph.model_dump(by_alias=True, exclude_none=True)}


@app.post("/render/svg")
def render_svg(
    graph: MinimalGraphIn,
    profile_id: str | None = None,
    profile_stage: Literal["draft", "published"] = "published",
    profile_version: int | None = Query(default=None, ge=1),
    theme_id: str | None = None,
    theme_stage: Literal["draft", "published"] = "published",
    theme_version: int | None = Query(default=None, ge=1),
) -> Response:
    profile_bundle: ProfileBundleV1 | None = None
    theme_bundle: ThemeBundleV1 | None = None

    if profile_id:
        try:
            profile_bundle = profile_store.get_bundle(
                profile_id,
                stage=profile_stage,
                profile_version=profile_version,
            )
        except ProfileStoreError as exc:
            raise _profile_http_error(exc) from exc

    if theme_id:
        try:
            theme_bundle = theme_store.get_bundle(
                theme_id,
                stage=theme_stage,
                theme_version=theme_version,
            )
        except ThemeStoreError as exc:
            raise _theme_http_error(exc) from exc

    try:
        svg = render_svg_from_graph(
            graph,
            profile_bundle=profile_bundle,
            theme_bundle=theme_bundle,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    headers: dict[str, str] = {}
    if profile_bundle is not None:
        headers["X-GraphAPI-Profile-Id"] = profile_bundle.profileId
        headers["X-GraphAPI-Profile-Version"] = str(profile_bundle.profileVersion)
        headers["X-GraphAPI-Profile-Checksum"] = profile_bundle.checksum
        headers["X-GraphAPI-Iconset-Resolution-Checksum"] = profile_bundle.iconsetResolutionChecksum
        headers["X-GraphAPI-Iconset-Sources"] = ",".join(
            [
                f"{ref.iconsetId}@{ref.iconsetVersion}"
                for ref in profile_bundle.iconsetRefs
            ]
        )
    if theme_bundle is not None:
        headers["X-GraphAPI-Theme-Id"] = theme_bundle.themeId
        headers["X-GraphAPI-Theme-Version"] = str(theme_bundle.themeVersion)
        headers["X-GraphAPI-Theme-Checksum"] = theme_bundle.checksum

    if profile_bundle is not None or theme_bundle is not None:
        headers["X-GraphAPI-Runtime-Checksum"] = _runtime_checksum(
            profile_checksum=profile_bundle.checksum if profile_bundle is not None else "",
            theme_checksum=theme_bundle.checksum if theme_bundle is not None else "",
            iconset_resolution_checksum=(
                profile_bundle.iconsetResolutionChecksum
                if profile_bundle is not None
                else ""
            ),
        )

    return Response(content=svg, media_type="image/svg+xml", headers=headers)
