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


def render_svg_from_graph(graph: MinimalGraphIn) -> str:
    canvas = build_canvas(graph, sample_settings())
    payload = canvas.model_dump(by_alias=True, exclude_none=True)

    try:
        payload = layout_with_elkjs(payload, mode="node", node_cmd="node")
    except Exception as exc:
        raise RuntimeError(f"Graph layout failed: {exc}") from exc

    try:
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


@app.post("/validate")
def validate_graph(graph: MinimalGraphIn) -> dict[str, object]:
    return {"valid": True, "normalized": graph.model_dump(by_alias=True, exclude_none=True)}


@app.post("/render/svg")
def render_svg(graph: MinimalGraphIn) -> Response:
    try:
        svg = render_svg_from_graph(graph)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(content=svg, media_type="image/svg+xml")
