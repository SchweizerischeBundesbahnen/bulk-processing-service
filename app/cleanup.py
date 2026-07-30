from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.models import JobStatus

if TYPE_CHECKING:
    from app.job_manager import JobManager

logger = logging.getLogger(__name__)


def parse_ttl(value: str) -> timedelta:
    match = re.fullmatch(r"(\d+)\s*([hms])", value.strip().lower())
    if not match:
        msg = f"Invalid TTL format: '{value}'. Expected format like '3h', '30m', '3600s'"
        raise ValueError(msg)
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    return timedelta(seconds=amount)


async def cleanup_expired_jobs(job_manager: JobManager, ttl: timedelta, interval: float = 60.0) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(_run_cleanup, job_manager, ttl)
        except Exception:
            logger.exception("Error during job cleanup")


def _run_cleanup(job_manager: JobManager, ttl: timedelta) -> None:
    now = datetime.now(UTC)
    for metadata in job_manager.list_jobs():
        age = now - metadata.created_at
        completed_age = (now - metadata.completed_at).total_seconds() if metadata.completed_at else 0
        if metadata.status == JobStatus.COMPLETED and metadata.completed_at and completed_age > ttl.total_seconds():
            job_manager.delete_job(metadata.job_id)
            logger.info("Cleaned up expired completed job '%s' (age: %s)", metadata.job_id, age)
        elif metadata.status == JobStatus.ACTIVE and age > ttl * 2:
            with job_manager._try_job_lock(metadata.job_id) as acquired:
                if acquired:
                    job_manager.delete_job(metadata.job_id)
                    logger.warning("Cleaned up stuck active job '%s' (age: %s)", metadata.job_id, age)
                else:
                    logger.debug("Skipped stuck active job '%s' — locked by another operation", metadata.job_id)
