from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint.

    Returns HTTP 200 with ``{"status": "ok"}``.
    This is a cross-repo contract used by Graphras and other
    orchestration tooling to determine service readiness.
    """
    return {"status": "ok"}
