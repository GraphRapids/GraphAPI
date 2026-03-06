from .app import app, render_svg_from_graph
from fastapi.responses import JSONResponse


@app.get("/health", tags=["infrastructure"])
async def health() -> JSONResponse:
    """Liveness health check endpoint."""
    return JSONResponse(content={"status": "ok"})


__all__ = ["app", "render_svg_from_graph"]
