from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pathlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import defusedxml.ElementTree as DET  # noqa: F401
import uvicorn
from fastapi import FastAPI

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from app.cleanup import cleanup_expired_jobs, parse_ttl
from app.converter_controller import init_job_manager
from app.converter_controller import router as converter_router
from app.job_manager import JobManager

logger = logging.getLogger(__name__)


def create_job_manager() -> JobManager:
    storage_dir = os.environ.get("JOB_STORAGE_DIR") or str(pathlib.Path.home() / ".bulk-processing-service" / "jobs")
    return JobManager(storage_dir)


job_manager = create_job_manager()
init_job_manager(job_manager)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    ttl = parse_ttl(os.environ.get("JOB_TTL", "3h"))
    task = asyncio.create_task(cleanup_expired_jobs(job_manager, ttl))
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Bulk Processing Service", description="Bulk PDF export service: accepts HTML, converts via WeasyPrint, merges PDFs", lifespan=lifespan)
app.include_router(converter_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9070"))
    uvicorn.run("app.app:app", host="0.0.0.0", port=port)  # noqa: S104
