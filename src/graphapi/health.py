from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint.

    Returns HTTP 200 with ``{"status": "ok"}``.

    **Cross-repo contract** — Graphras (and potentially other orchestration
    services) poll this path to determine readiness.  Any change to the
    route path or response schema must be coordinated across repositories:

    * GraphAPI  – this file
    * Graphras  – readiness probe configuration
    """
    return {"status": "ok"}
