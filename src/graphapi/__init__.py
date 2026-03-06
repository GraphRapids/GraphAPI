from .app import app, render_svg_from_graph
from .health import router as _health_router

# Register the /health router on the main FastAPI app so it is available
# at runtime.  This endpoint is a cross-repo contract — see health.py.
app.include_router(_health_router)

__all__ = ["app", "render_svg_from_graph"]
