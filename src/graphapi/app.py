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

from .graph_type_contract import (
    AutocompleteCatalogResponseV1,
    GraphTypeBundleV1,
    GraphTypeCreateRequestV1,
    GraphTypeListResponseV1,
    GraphTypeRecordV1,
    GraphTypeRuntimeResponseV1,
    GraphTypeUpdateRequestV1,
    LayoutSetBundleV1,
    LayoutSetCreateRequestV1,
    LayoutSetEntryUpsertRequestV1,
    LayoutSetListResponseV1,
    LayoutSetRecordV1,
    LayoutSetUpdateRequestV1,
    LinkSetBundleV1,
    LinkSetCreateRequestV1,
    LinkSetEntryUpsertRequestV1,
    LinkSetListResponseV1,
    LinkSetRecordV1,
    LinkSetUpdateRequestV1,
    compute_icon_set_resolution_checksum,
)
from .graphtype_defaults import default_graph_type_create_request
from .graphtype_store import GraphTypeStore, GraphTypeStoreError
from .iconset_defaults import default_iconset_create_request
from .iconset_store import IconsetStore, IconsetStoreError
from .layoutset_defaults import default_layout_set_create_request
from .layoutset_store import LayoutSetStore, LayoutSetStoreError
from .linkset_defaults import default_link_set_create_request
from .linkset_store import LinkSetStore, LinkSetStoreError
from .profile_contract import (
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
    ThemeBundleV1,
    ThemeCreateRequestV1,
    ThemeListResponseV1,
    ThemeRecordV1,
    ThemeUpdateRequestV1,
)
from .theme_defaults import default_theme_create_request
from .theme_store import ThemeStore, ThemeStoreError

REQUEST_TIMEOUT_SECONDS = float(os.getenv("GRAPHAPI_REQUEST_TIMEOUT_SECONDS", "15"))
MAX_REQUEST_BYTES = int(os.getenv("GRAPHAPI_MAX_REQUEST_BYTES", "1048576"))

app = FastAPI(
    title="GraphAPI",
    description=(
        "GraphRapids runtime API. Includes graph type + icon set + layout set + link set + render theme "
        "management (v1) and graph render orchestration over GraphLoom + GraphRender."
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

layout_set_store = LayoutSetStore.from_env()
layout_set_store.ensure_default_layout_set(default_layout_set_create_request())

link_set_store = LinkSetStore.from_env()
link_set_store.ensure_default_link_set(default_link_set_create_request())

graph_type_store = GraphTypeStore.from_env(iconset_store, layout_set_store, link_set_store)
graph_type_store.ensure_default_graph_type(default_graph_type_create_request())

theme_store = ThemeStore.from_env()
theme_store.ensure_default_theme(default_theme_create_request())


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


def _layout_set_http_error(exc: LayoutSetStoreError) -> HTTPException:
    body = ErrorResponse(
        error=ErrorBody(code=exc.code, message=exc.message, details=exc.details)
    )
    return HTTPException(status_code=exc.status_code, detail=body.model_dump()["error"])


def _link_set_http_error(exc: LinkSetStoreError) -> HTTPException:
    body = ErrorResponse(
        error=ErrorBody(code=exc.code, message=exc.message, details=exc.details)
    )
    return HTTPException(status_code=exc.status_code, detail=body.model_dump()["error"])


def _graph_type_http_error(exc: GraphTypeStoreError) -> HTTPException:
    body = ErrorResponse(
        error=ErrorBody(code=exc.code, message=exc.message, details=exc.details)
    )
    return HTTPException(status_code=exc.status_code, detail=body.model_dump()["error"])


def _runtime_checksum(
    *,
    graph_type_checksum: str = "",
    theme_checksum: str = "",
    graph_type_runtime_checksum: str = "",
) -> str:
    material = "|".join(
        [
            graph_type_checksum,
            theme_checksum,
            graph_type_runtime_checksum,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def render_svg_from_graph(
    graph: MinimalGraphIn,
    *,
    graph_type_bundle: GraphTypeBundleV1 | None = None,
    theme_bundle: ThemeBundleV1 | None = None,
) -> str:
    if graph_type_bundle is not None:
        settings_payload = dict(graph_type_bundle.elkSettings)
        settings_payload["type_icon_map"] = graph_type_bundle.typeIconMap
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


@app.get("/v1/icon-sets", response_model=IconsetListResponseV1, tags=["icon-sets"])
def list_icon_sets_v1() -> IconsetListResponseV1:
    try:
        return iconset_store.list_icon_sets()
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.get(
    "/v1/icon-sets/{id}",
    response_model=IconsetRecordV1,
    tags=["icon-sets"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_iconset_v1(id: str) -> IconsetRecordV1:
    try:
        return iconset_store.get_iconset(id)
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.get(
    "/v1/icon-sets/{id}/bundle",
    response_model=IconsetBundleV1,
    tags=["icon-sets"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_iconset_bundle_v1(
    id: str,
    stage: Literal["draft", "published"] = Query(default="published"),
    icon_set_version: int | None = Query(default=None, ge=1),
) -> IconsetBundleV1:
    try:
        return iconset_store.get_bundle(id, stage=stage, icon_set_version=icon_set_version)
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.post(
    "/v1/icon-sets",
    response_model=IconsetRecordV1,
    status_code=201,
    tags=["icon-sets"],
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def create_iconset_v1(request: IconsetCreateRequestV1) -> IconsetRecordV1:
    try:
        return iconset_store.create_iconset(request)
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.put(
    "/v1/icon-sets/{id}",
    response_model=IconsetRecordV1,
    tags=["icon-sets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def update_iconset_v1(id: str, request: IconsetUpdateRequestV1) -> IconsetRecordV1:
    try:
        return iconset_store.update_iconset(id, request)
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.put(
    "/v1/icon-sets/{id}/entries/{key}",
    response_model=IconsetRecordV1,
    tags=["icon-sets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def upsert_iconset_entry_v1(id: str, key: str, request: IconsetEntryUpsertRequestV1) -> IconsetRecordV1:
    try:
        return iconset_store.upsert_iconset_entry(id, key, request)
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.delete(
    "/v1/icon-sets/{id}/entries/{key}",
    response_model=IconsetRecordV1,
    tags=["icon-sets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def delete_iconset_entry_v1(id: str, key: str) -> IconsetRecordV1:
    try:
        return iconset_store.delete_iconset_entry(id, key)
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.post(
    "/v1/icon-sets/{id}/publish",
    response_model=IconsetBundleV1,
    tags=["icon-sets"],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def publish_iconset_v1(id: str) -> IconsetBundleV1:
    try:
        return iconset_store.publish_iconset(id)
    except IconsetStoreError as exc:
        raise _iconset_http_error(exc) from exc


@app.post(
    "/v1/icon-sets/resolve",
    response_model=IconsetResolutionResultV1,
    tags=["icon-sets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def resolve_icon_sets_v1(request: IconsetResolveRequestV1) -> IconsetResolutionResultV1:
    resolved_entries: dict[str, str] = {}
    sources = []
    key_sources: dict[str, dict] = {}

    for ref in request.iconSetRefs:
        try:
            bundle = iconset_store.get_bundle(ref.iconSetId, stage=ref.stage, icon_set_version=ref.iconSetVersion)
        except IconsetStoreError as exc:
            raise _iconset_http_error(exc) from exc

        source = {
            "iconSetId": bundle.iconSetId,
            "iconSetVersion": bundle.iconSetVersion,
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
                code="GRAPH_TYPE_ICONSET_REF_INVALID",
                message="Resolved iconset map is empty.",
            )
        )
        raise HTTPException(status_code=400, detail=body.model_dump()["error"])

    sorted_entries = dict(sorted(resolved_entries.items(), key=lambda item: item[0]))
    checksum = compute_icon_set_resolution_checksum(
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


@app.get("/v1/layout-sets", response_model=LayoutSetListResponseV1, tags=["layout-sets"])
def list_layout_sets_v1() -> LayoutSetListResponseV1:
    try:
        return layout_set_store.list_layout_sets()
    except LayoutSetStoreError as exc:
        raise _layout_set_http_error(exc) from exc


@app.get(
    "/v1/layout-sets/{id}",
    response_model=LayoutSetRecordV1,
    tags=["layout-sets"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_layout_set_v1(id: str) -> LayoutSetRecordV1:
    try:
        return layout_set_store.get_layout_set(id)
    except LayoutSetStoreError as exc:
        raise _layout_set_http_error(exc) from exc


@app.get(
    "/v1/layout-sets/{id}/bundle",
    response_model=LayoutSetBundleV1,
    tags=["layout-sets"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_layout_set_bundle_v1(
    id: str,
    stage: Literal["draft", "published"] = Query(default="published"),
    layout_set_version: int | None = Query(default=None, ge=1),
) -> LayoutSetBundleV1:
    try:
        return layout_set_store.get_bundle(id, stage=stage, layout_set_version=layout_set_version)
    except LayoutSetStoreError as exc:
        raise _layout_set_http_error(exc) from exc


@app.post(
    "/v1/layout-sets",
    response_model=LayoutSetRecordV1,
    status_code=201,
    tags=["layout-sets"],
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def create_layout_set_v1(request: LayoutSetCreateRequestV1) -> LayoutSetRecordV1:
    try:
        return layout_set_store.create_layout_set(request)
    except LayoutSetStoreError as exc:
        raise _layout_set_http_error(exc) from exc


@app.put(
    "/v1/layout-sets/{id}",
    response_model=LayoutSetRecordV1,
    tags=["layout-sets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def update_layout_set_v1(id: str, request: LayoutSetUpdateRequestV1) -> LayoutSetRecordV1:
    try:
        return layout_set_store.update_layout_set(id, request)
    except LayoutSetStoreError as exc:
        raise _layout_set_http_error(exc) from exc


@app.put(
    "/v1/layout-sets/{id}/entries/{key}",
    response_model=LayoutSetRecordV1,
    tags=["layout-sets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def upsert_layout_set_entry_v1(
    id: str,
    key: str,
    request: LayoutSetEntryUpsertRequestV1,
) -> LayoutSetRecordV1:
    try:
        return layout_set_store.upsert_layout_set_entry(id, key, request)
    except LayoutSetStoreError as exc:
        raise _layout_set_http_error(exc) from exc


@app.delete(
    "/v1/layout-sets/{id}/entries/{key}",
    response_model=LayoutSetRecordV1,
    tags=["layout-sets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def delete_layout_set_entry_v1(id: str, key: str) -> LayoutSetRecordV1:
    try:
        return layout_set_store.delete_layout_set_entry(id, key)
    except LayoutSetStoreError as exc:
        raise _layout_set_http_error(exc) from exc


@app.post(
    "/v1/layout-sets/{id}/publish",
    response_model=LayoutSetBundleV1,
    tags=["layout-sets"],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def publish_layout_set_v1(id: str) -> LayoutSetBundleV1:
    try:
        return layout_set_store.publish_layout_set(id)
    except LayoutSetStoreError as exc:
        raise _layout_set_http_error(exc) from exc


@app.get("/v1/link-sets", response_model=LinkSetListResponseV1, tags=["link-sets"])
def list_link_sets_v1() -> LinkSetListResponseV1:
    try:
        return link_set_store.list_link_sets()
    except LinkSetStoreError as exc:
        raise _link_set_http_error(exc) from exc


@app.get(
    "/v1/link-sets/{id}",
    response_model=LinkSetRecordV1,
    tags=["link-sets"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_link_set_v1(id: str) -> LinkSetRecordV1:
    try:
        return link_set_store.get_link_set(id)
    except LinkSetStoreError as exc:
        raise _link_set_http_error(exc) from exc


@app.get(
    "/v1/link-sets/{id}/bundle",
    response_model=LinkSetBundleV1,
    tags=["link-sets"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_link_set_bundle_v1(
    id: str,
    stage: Literal["draft", "published"] = Query(default="published"),
    link_set_version: int | None = Query(default=None, ge=1),
) -> LinkSetBundleV1:
    try:
        return link_set_store.get_bundle(id, stage=stage, link_set_version=link_set_version)
    except LinkSetStoreError as exc:
        raise _link_set_http_error(exc) from exc


@app.post(
    "/v1/link-sets",
    response_model=LinkSetRecordV1,
    status_code=201,
    tags=["link-sets"],
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def create_link_set_v1(request: LinkSetCreateRequestV1) -> LinkSetRecordV1:
    try:
        return link_set_store.create_link_set(request)
    except LinkSetStoreError as exc:
        raise _link_set_http_error(exc) from exc


@app.put(
    "/v1/link-sets/{id}",
    response_model=LinkSetRecordV1,
    tags=["link-sets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def update_link_set_v1(id: str, request: LinkSetUpdateRequestV1) -> LinkSetRecordV1:
    try:
        return link_set_store.update_link_set(id, request)
    except LinkSetStoreError as exc:
        raise _link_set_http_error(exc) from exc


@app.put(
    "/v1/link-sets/{id}/entries/{key}",
    response_model=LinkSetRecordV1,
    tags=["link-sets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def upsert_link_entry_v1(id: str, key: str, request: LinkSetEntryUpsertRequestV1) -> LinkSetRecordV1:
    try:
        return link_set_store.upsert_link_entry(id, key, request)
    except LinkSetStoreError as exc:
        raise _link_set_http_error(exc) from exc


@app.delete(
    "/v1/link-sets/{id}/entries/{key}",
    response_model=LinkSetRecordV1,
    tags=["link-sets"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def delete_link_entry_v1(id: str, key: str) -> LinkSetRecordV1:
    try:
        return link_set_store.delete_link_entry(id, key)
    except LinkSetStoreError as exc:
        raise _link_set_http_error(exc) from exc


@app.post(
    "/v1/link-sets/{id}/publish",
    response_model=LinkSetBundleV1,
    tags=["link-sets"],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def publish_link_set_v1(id: str) -> LinkSetBundleV1:
    try:
        return link_set_store.publish_link_set(id)
    except LinkSetStoreError as exc:
        raise _link_set_http_error(exc) from exc


@app.get("/v1/graph-types", response_model=GraphTypeListResponseV1, tags=["graph-types"])
def list_graph_types_v1() -> GraphTypeListResponseV1:
    try:
        return graph_type_store.list_graph_types()
    except GraphTypeStoreError as exc:
        raise _graph_type_http_error(exc) from exc


@app.get(
    "/v1/graph-types/{id}",
    response_model=GraphTypeRecordV1,
    tags=["graph-types"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_graph_type_v1(id: str) -> GraphTypeRecordV1:
    try:
        return graph_type_store.get_graph_type(id)
    except GraphTypeStoreError as exc:
        raise _graph_type_http_error(exc) from exc


@app.get(
    "/v1/graph-types/{id}/bundle",
    response_model=GraphTypeBundleV1,
    tags=["graph-types"],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_graph_type_bundle_v1(
    id: str,
    stage: Literal["draft", "published"] = Query(default="published"),
    graph_type_version: int | None = Query(default=None, ge=1),
) -> GraphTypeBundleV1:
    try:
        return graph_type_store.get_bundle(id, stage=stage, graph_type_version=graph_type_version)
    except GraphTypeStoreError as exc:
        raise _graph_type_http_error(exc) from exc


@app.post(
    "/v1/graph-types",
    response_model=GraphTypeRecordV1,
    status_code=201,
    tags=["graph-types"],
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def create_graph_type_v1(request: GraphTypeCreateRequestV1) -> GraphTypeRecordV1:
    try:
        return graph_type_store.create_graph_type(request)
    except GraphTypeStoreError as exc:
        raise _graph_type_http_error(exc) from exc


@app.put(
    "/v1/graph-types/{id}",
    response_model=GraphTypeRecordV1,
    tags=["graph-types"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def update_graph_type_v1(id: str, request: GraphTypeUpdateRequestV1) -> GraphTypeRecordV1:
    try:
        return graph_type_store.update_graph_type(id, request)
    except GraphTypeStoreError as exc:
        raise _graph_type_http_error(exc) from exc


@app.post(
    "/v1/graph-types/{id}/publish",
    response_model=GraphTypeBundleV1,
    tags=["graph-types"],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def publish_graph_type_v1(id: str) -> GraphTypeBundleV1:
    try:
        return graph_type_store.publish_graph_type(id)
    except GraphTypeStoreError as exc:
        raise _graph_type_http_error(exc) from exc


@app.get(
    "/v1/graph-types/{id}/runtime",
    response_model=GraphTypeRuntimeResponseV1,
    tags=["graph-types"],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_graph_type_runtime_v1(
    id: str,
    stage: Literal["draft", "published"] = Query(default="published"),
    graph_type_version: int | None = Query(default=None, ge=1),
) -> GraphTypeRuntimeResponseV1:
    try:
        return graph_type_store.get_runtime(id, stage=stage, graph_type_version=graph_type_version)
    except GraphTypeStoreError as exc:
        raise _graph_type_http_error(exc) from exc


@app.get(
    "/v1/autocomplete/catalog",
    response_model=AutocompleteCatalogResponseV1,
    tags=["graph-types"],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_autocomplete_catalog_v1(
    graph_type_id: str,
    stage: Literal["draft", "published"] = Query(default="published"),
    graph_type_version: int | None = Query(default=None, ge=1),
) -> AutocompleteCatalogResponseV1:
    try:
        return graph_type_store.get_autocomplete_catalog(
            graph_type_id,
            stage=stage,
            graph_type_version=graph_type_version,
        )
    except GraphTypeStoreError as exc:
        raise _graph_type_http_error(exc) from exc


@app.get("/v1/themes", response_model=ThemeListResponseV1, tags=["themes"])
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
        return theme_store.get_bundle(id, stage=stage, theme_version=theme_version)
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
def update_theme_v1(id: str, request: ThemeUpdateRequestV1) -> ThemeRecordV1:
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
    graph_type_id: str | None = None,
    graph_type_stage: Literal["draft", "published"] = "published",
    graph_type_version: int | None = Query(default=None, ge=1),
    theme_id: str | None = None,
    theme_stage: Literal["draft", "published"] = "published",
    theme_version: int | None = Query(default=None, ge=1),
) -> Response:
    graph_type_bundle: GraphTypeBundleV1 | None = None
    theme_bundle: ThemeBundleV1 | None = None

    if graph_type_id:
        try:
            graph_type_bundle = graph_type_store.get_bundle(
                graph_type_id,
                stage=graph_type_stage,
                graph_type_version=graph_type_version,
            )
        except GraphTypeStoreError as exc:
            raise _graph_type_http_error(exc) from exc

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
        svg = render_svg_from_graph(graph, graph_type_bundle=graph_type_bundle, theme_bundle=theme_bundle)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    headers: dict[str, str] = {}
    if graph_type_bundle is not None:
        headers["X-GraphAPI-Graph-Type-Id"] = graph_type_bundle.graphTypeId
        headers["X-GraphAPI-Graph-Type-Version"] = str(graph_type_bundle.graphTypeVersion)
        headers["X-GraphAPI-Graph-Type-Checksum"] = graph_type_bundle.checksum
        headers["X-GraphAPI-Graph-Type-Runtime-Checksum"] = graph_type_bundle.runtimeChecksum
        headers["X-GraphAPI-Icon-Set-Resolution-Checksum"] = graph_type_bundle.iconSetResolutionChecksum
        headers["X-GraphAPI-Icon-Set-Sources"] = ",".join(
            [f"{ref.iconSetId}@{ref.iconSetVersion}" for ref in graph_type_bundle.iconSetRefs]
        )
    if theme_bundle is not None:
        headers["X-GraphAPI-Theme-Id"] = theme_bundle.themeId
        headers["X-GraphAPI-Theme-Version"] = str(theme_bundle.themeVersion)
        headers["X-GraphAPI-Theme-Checksum"] = theme_bundle.checksum

    if graph_type_bundle is not None or theme_bundle is not None:
        headers["X-GraphAPI-Runtime-Checksum"] = _runtime_checksum(
            graph_type_checksum=graph_type_bundle.checksum if graph_type_bundle is not None else "",
            theme_checksum=theme_bundle.checksum if theme_bundle is not None else "",
            graph_type_runtime_checksum=(
                graph_type_bundle.runtimeChecksum if graph_type_bundle is not None else ""
            ),
        )

    return Response(content=svg, media_type="image/svg+xml", headers=headers)
