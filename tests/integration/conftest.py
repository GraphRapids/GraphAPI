from __future__ import annotations

import os

import httpx
import pytest


@pytest.fixture(scope="session")
def service_url() -> str:
    """Base URL of the live GraphAPI service, read from SERVICE_URL env var."""
    return os.getenv("SERVICE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def http_client(service_url: str):
    """Shared HTTP client for integration tests, scoped to the test session."""
    with httpx.Client(base_url=service_url, timeout=30.0) as client:
        yield client
