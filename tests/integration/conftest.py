from __future__ import annotations

import os

import httpx
import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    """Base URL of the live GraphAPI service.

    Read from the ``GRAPHAPI_BASE_URL`` environment variable.
    All integration tests are **skipped** when the variable is unset,
    so a plain ``pytest`` invocation never attempts network calls.
    """
    url = os.getenv("GRAPHAPI_BASE_URL", "").strip()
    if not url:
        pytest.skip("GRAPHAPI_BASE_URL not set; skipping integration tests")
    return url.rstrip("/")


@pytest.fixture()
def http_client(base_url: str):
    """Per-test HTTP client pointed at the live service.

    A new ``httpx.Client`` is created for every test to avoid shared
    state (cookies, connection pool exhaustion) between tests.
    """
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        yield client
