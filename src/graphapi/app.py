from __future__ import annotations

import json
import os
from importlib import resources

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from graphloom import MinimalGraphIn, build_canvas, layout_with_elkjs, sample_settings
from graphrender import GraphRender

REQUEST_TIMEOUT_SECONDS = float(os.getenv("GRAPHAPI_REQUEST_TIMEOUT_SECONDS", "15"))
MAX_REQUEST_BYTES = int(os.getenv("GRAPHAPI_MAX_REQUEST_BYTES", "1048576"))

app = FastAPI(
    title="GraphAPI",
    description="Render GraphLoom minimal JSON into SVG using GraphRender.",
    version="0.1.0",
)

cors_origins = [origin.strip() for origin in os.getenv("GRAPHAPI_CORS_ORIGINS", "*").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


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


def render_svg_from_graph(graph: MinimalGraphIn, *, theme_id: str = "default") -> str:
    canvas = build_canvas(graph, _resolve_themed_settings(theme_id))
    payload = canvas.model_dump(by_alias=True, exclude_none=True)

    try:
        payload = layout_with_elkjs(payload, mode="node", node_cmd="node")
    except Exception as exc:
        raise RuntimeError(f"Graph layout failed: {exc}") from exc

    try:
        try:
            return GraphRender(payload, theme_id=theme_id).to_string()
        except TypeError:
            if theme_id != "default":
                raise RuntimeError(
                    "Installed GraphRender does not support non-default theme ids."
                )
            return GraphRender(payload).to_string()
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


@app.post("/validate")
def validate_graph(graph: MinimalGraphIn) -> dict[str, object]:
    return {"valid": True, "normalized": graph.model_dump(by_alias=True, exclude_none=True)}


@app.post("/render/svg")
def render_svg(graph: MinimalGraphIn, theme_id: str = "default") -> Response:
    try:
        svg = render_svg_from_graph(graph, theme_id=theme_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(content=svg, media_type="image/svg+xml")
