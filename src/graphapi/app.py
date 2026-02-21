from __future__ import annotations

from typing import Literal

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from graphloom import MinimalGraphIn, build_canvas, layout_with_elkjs, sample_settings
from graphrender import GraphRender
from pydantic import BaseModel, ValidationError

app = FastAPI(
    title="GraphAPI",
    description="Render GraphLoom YAML into SVG using GraphRender.",
    version="0.1.0",
)


class RenderRequest(BaseModel):
    yaml: str
    layout: bool = False
    elkjs_mode: Literal["node", "npm", "npx"] = "node"
    node_cmd: str = "node"


def render_svg_from_yaml(
    yaml_text: str,
    *,
    layout: bool = False,
    elkjs_mode: str = "node",
    node_cmd: str = "node",
) -> str:
    try:
        raw = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc

    try:
        minimal = MinimalGraphIn.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Graph validation failed: {exc}") from exc

    canvas = build_canvas(minimal, sample_settings())
    payload = canvas.model_dump(by_alias=True, exclude_none=True)

    if layout:
        try:
            payload = layout_with_elkjs(payload, mode=elkjs_mode, node_cmd=node_cmd)
        except Exception as exc:
            raise RuntimeError(f"Graph layout failed: {exc}") from exc

    try:
        return GraphRender(payload).to_string()
    except Exception as exc:
        raise RuntimeError(f"Graph render failed: {exc}") from exc


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/render/svg")
def render_svg(request: RenderRequest) -> Response:
    try:
        svg = render_svg_from_yaml(
            request.yaml,
            layout=request.layout,
            elkjs_mode=request.elkjs_mode,
            node_cmd=request.node_cmd,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(content=svg, media_type="image/svg+xml")
