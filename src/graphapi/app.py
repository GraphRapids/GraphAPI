from __future__ import annotations

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

from .profile_contract import (
    AutocompleteCatalogResponseV1,
    ErrorBody,
    ErrorResponse,
    ProfileBundleV1,
    ProfileCreateRequestV1,
    ProfileListResponseV1,
    ProfileRecordV1,
    ProfileUpdateRequestV1,
)
from .profile_defaults import default_profile_create_request
from .profile_store import ProfileStore, ProfileStoreError

REQUEST_TIMEOUT_SECONDS = float(os.getenv("GRAPHAPI_REQUEST_TIMEOUT_SECONDS", "15"))
MAX_REQUEST_BYTES = int(os.getenv("GRAPHAPI_MAX_REQUEST_BYTES", "1048576"))

app = FastAPI(
    title="GraphAPI",
    description=(
        "GraphRapids runtime API. Includes profile management (v1) and graph render "
        "orchestration over GraphLoom + GraphRender."
    ),
    version="1.0.0",
)

cors_origins = [origin.strip() for origin in os.getenv("GRAPHAPI_CORS_ORIGINS", "*").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["*"],
)


profile_store = ProfileStore.from_env()
profile_store.ensure_default_profile(default_profile_create_request())


def _profile_http_error(exc: ProfileStoreError) -> HTTPException:
    body = ErrorResponse(
        error=ErrorBody(code=exc.code, message=exc.message, details=exc.details)
    )
    return HTTPException(status_code=exc.status_code, detail=body.model_dump()["error"])


def _resolve_themed_settings(theme_id: str):
    settings = sample_settings()
    try:
        from graphloom.theme import resolve_theme_settings
    except Exception as exc:
        if theme_id != "default":
            raise RuntimeError(
                "Theme selection requires GraphLoom with GraphTheme integration."
            ) from exc
        return settings
    try:
        return resolve_theme_settings(settings, theme_id)
    except Exception as exc:
        raise RuntimeError(f"Theme resolution failed: {exc}") from exc


def _graphtheme_api():
    try:
        from graphtheme import get_theme_css
        from graphtheme import get_theme_metrics
        from graphtheme import get_theme_meta
        from graphtheme import list_themes
    except Exception as exc:
        raise RuntimeError(
            "GraphTheme package is required for theme API endpoints."
        ) from exc
    return list_themes, get_theme_meta, get_theme_css, get_theme_metrics


def render_svg_from_graph(
    graph: MinimalGraphIn,
    *,
    theme_id: str = "default",
    profile_bundle: ProfileBundleV1 | None = None,
) -> str:
    if profile_bundle is None:
        settings = _resolve_themed_settings(theme_id)
    else:
        settings = ElkSettings.model_validate(profile_bundle.elkSettings)

    canvas = build_canvas(graph, settings)
    payload = canvas.model_dump(by_alias=True, exclude_none=True)

    try:
        payload = layout_with_elkjs(payload, mode="node", node_cmd="node")
    except Exception as exc:
        raise RuntimeError(f"Graph layout failed: {exc}") from exc

    try:
        if profile_bundle is None:
            try:
                return GraphRender(payload, theme_id=theme_id).to_string()
            except TypeError:
                if theme_id != "default":
                    raise RuntimeError(
                        "Installed GraphRender does not support non-default theme ids."
                    )
                return GraphRender(payload).to_string()

        return GraphRender(
            payload,
            theme_css=profile_bundle.renderCss,
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


@app.get("/themes")
def list_themes() -> dict[str, object]:
    try:
        list_theme_meta, _, _, _ = _graphtheme_api()
        return {"themes": [theme.__dict__ for theme in list_theme_meta()]}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/themes/{theme_id}")
def get_theme(theme_id: str) -> dict[str, object]:
    try:
        _, get_theme_meta, _, _ = _graphtheme_api()
        return get_theme_meta(theme_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/themes/{theme_id}/css")
def get_theme_css(theme_id: str) -> Response:
    try:
        _, _, fetch_theme_css, _ = _graphtheme_api()
        return Response(content=fetch_theme_css(theme_id), media_type="text/css")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/themes/{theme_id}/metrics")
def get_theme_metrics(theme_id: str) -> dict[str, object]:
    try:
        _, _, _, fetch_theme_metrics = _graphtheme_api()
        return fetch_theme_metrics(theme_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
    "/v1/autocomplete/catalog",
    response_model=AutocompleteCatalogResponseV1,
    tags=["profiles"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_autocomplete_catalog(
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


@app.post("/validate")
def validate_graph(graph: MinimalGraphIn) -> dict[str, object]:
    return {"valid": True, "normalized": graph.model_dump(by_alias=True, exclude_none=True)}


@app.post("/render/svg")
def render_svg(
    graph: MinimalGraphIn,
    theme_id: str = "default",
    profile_id: str | None = None,
    profile_stage: Literal["draft", "published"] = "published",
    profile_version: int | None = Query(default=None, ge=1),
) -> Response:
    bundle: ProfileBundleV1 | None = None
    if profile_id:
        try:
            bundle = profile_store.get_bundle(
                profile_id,
                stage=profile_stage,
                profile_version=profile_version,
            )
        except ProfileStoreError as exc:
            raise _profile_http_error(exc) from exc

    try:
        svg = render_svg_from_graph(
            graph,
            theme_id=theme_id,
            profile_bundle=bundle,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    headers: dict[str, str] = {}
    if bundle is not None:
        headers["X-GraphAPI-Profile-Id"] = bundle.profileId
        headers["X-GraphAPI-Profile-Version"] = str(bundle.profileVersion)
        headers["X-GraphAPI-Profile-Checksum"] = bundle.checksum

    return Response(content=svg, media_type="image/svg+xml", headers=headers)
