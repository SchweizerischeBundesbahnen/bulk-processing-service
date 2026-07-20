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
            now = datetime.now(UTC)
            for metadata in job_manager.list_jobs():
                age = now - metadata.created_at
                if metadata.status == JobStatus.COMPLETED and age > ttl:
                    job_manager.delete_job(metadata.job_id)
                    logger.info("Cleaned up expired completed job '%s' (age: %s)", metadata.job_id, age)
                elif metadata.status == JobStatus.ACTIVE and age > ttl * 2:
                    job_manager.delete_job(metadata.job_id)
                    logger.warning("Cleaned up stuck active job '%s' (age: %s)", metadata.job_id, age)
        except Exception:
            logger.exception("Error during job cleanup")
