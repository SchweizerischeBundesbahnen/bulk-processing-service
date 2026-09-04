from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.models import JobMetadata, JobStatus

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
        try:
            _cleanup_job(job_manager, metadata, now, ttl)
        except Exception:
            logger.exception("Failed to clean up job '%s'", metadata.job_id)


def _cleanup_job(job_manager: JobManager, metadata: JobMetadata, now: datetime, ttl: timedelta) -> None:
    if metadata.status == JobStatus.COMPLETED and metadata.completed_at:
        completed_age = now - metadata.completed_at
        if completed_age > ttl:
            with job_manager._try_job_lock(metadata.job_id) as acquired:
                if acquired:
                    job_manager.delete_job(metadata.job_id)
                    logger.info("Cleaned up expired completed job '%s' (age: %s)", metadata.job_id, completed_age)
                else:
                    logger.debug("Skipped completed job '%s' — locked by another operation", metadata.job_id)
    elif metadata.status == JobStatus.ACTIVE:
        # Base "stuck" on the last activity, not creation: a long batch that keeps
        # adding documents refreshes updated_at and must not be deleted mid-flight.
        last_activity = metadata.updated_at or metadata.created_at
        if now - last_activity > ttl * 2:
            with job_manager._try_job_lock(metadata.job_id) as acquired:
                if not acquired:
                    logger.debug("Skipped stuck active job '%s' — locked by another operation", metadata.job_id)
                    return
                # Re-read under the lock: the snapshot from list_jobs may be stale, so
                # a job that finished or made progress since then must not be deleted.
                current = job_manager.get_job_metadata(metadata.job_id)
                if current is None or current.status != JobStatus.ACTIVE:
                    return
                current_last_activity = current.updated_at or current.created_at
                if now - current_last_activity <= ttl * 2:
                    return
                job_manager.delete_job(metadata.job_id)
                logger.warning("Cleaned up stuck active job '%s' (idle: %s)", metadata.job_id, now - current_last_activity)
