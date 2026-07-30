from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pathlib
import platform
import time
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.responses import PlainTextResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

from app.cleanup import cleanup_expired_jobs, parse_ttl
from app.constants import API_VERSION, BUILD_TIMESTAMP, JOB_STORAGE_DIR, JOB_TTL, REQUEST_BODY_LIMIT, SERVICE_VERSION, WEASYPRINT_SERVICE_URL, WEASYPRINT_SERVICE_URL_DEFAULT
from app.converter_controller import router as converter_router
from app.job_manager import JobManager
from app.models import VersionInfo

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.job_manager = JobManager(JOB_STORAGE_DIR)
    ttl = parse_ttl(JOB_TTL)
    task = asyncio.create_task(cleanup_expired_jobs(app.state.job_manager, ttl))
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Bulk Processing Service", description="Bulk PDF export service: accepts HTML, converts via WeasyPrint, merges PDFs", lifespan=lifespan)
app.include_router(converter_router)


_HTTP_OK = 200
_WEASYPRINT_HEALTH_TIMEOUT = 2.0
_WEASYPRINT_HEALTH_CACHE_TTL = 5.0

# (checked_at_monotonic, result); cached briefly so frequent probes don't each block on WeasyPrint.
_weasyprint_status: tuple[float, str] = (0.0, "unavailable")


@app.middleware("http")
async def check_request_size(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared = int(content_length)
        except ValueError:
            return PlainTextResponse("Invalid Content-Length header", status_code=400)
        if declared > REQUEST_BODY_LIMIT:
            return PlainTextResponse(f"Request body too large (limit: {REQUEST_BODY_LIMIT} bytes)", status_code=413)
    return await call_next(request)


def _check_storage_writable() -> str:
    storage_dir = pathlib.Path(JOB_STORAGE_DIR)
    # Unique name so concurrent probes don't race on unlink of a shared file.
    test_file = storage_dir / f".health_check_{os.getpid()}_{uuid.uuid4().hex}"
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        test_file.write_text("ok", encoding="utf-8")
        return "writable"
    except OSError:
        return "unwritable"
    finally:
        with contextlib.suppress(OSError):
            test_file.unlink()


def _check_weasyprint_reachable() -> str:
    global _weasyprint_status  # noqa: PLW0603
    checked_at, cached = _weasyprint_status
    now = time.monotonic()
    if checked_at and now - checked_at < _WEASYPRINT_HEALTH_CACHE_TTL:
        return cached
    base_url = WEASYPRINT_SERVICE_URL or WEASYPRINT_SERVICE_URL_DEFAULT
    try:
        with httpx.Client(timeout=_WEASYPRINT_HEALTH_TIMEOUT) as client:
            response = client.get(f"{base_url}/version")
            result = "available" if response.status_code == _HTTP_OK else "unavailable"
    except Exception:  # noqa: BLE001
        result = "unavailable"
    _weasyprint_status = (now, result)
    return result


@app.get("/health")
def health() -> JSONResponse:
    """Liveness: process and local storage only. Independent of downstream services."""
    storage = _check_storage_writable()
    body = {"status": "healthy" if storage == "writable" else "unhealthy", "storage": storage}
    return JSONResponse(body, status_code=_HTTP_OK if storage == "writable" else 503)


@app.get("/ready")
def ready() -> JSONResponse:
    """Readiness: also verifies the downstream WeasyPrint dependency."""
    storage = _check_storage_writable()
    weasyprint = _check_weasyprint_reachable()
    body = {"status": "ready", "storage": storage, "weasyprint": weasyprint}
    if storage != "writable" or weasyprint != "available":
        body["status"] = "not ready"
        return JSONResponse(body, status_code=503)
    return JSONResponse(body)


@app.get("/version")
def version() -> VersionInfo:
    return VersionInfo(
        api_version=API_VERSION,
        python=platform.python_version(),
        bulk_processing_service=SERVICE_VERSION,
        timestamp=BUILD_TIMESTAMP,
    )
