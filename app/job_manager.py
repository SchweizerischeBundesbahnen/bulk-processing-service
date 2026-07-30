from __future__ import annotations

import fcntl
import logging
import os
import pathlib
import re
import shutil
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.models import JobMetadata, JobStatus
from app.pdf_merger import merge_pdfs

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import MergeJobStartParams

logger = logging.getLogger(__name__)

METADATA_FILE = "metadata.json"
RESULT_FILE = "result.pdf"
LOCK_FILE = "lock"
_VALID_JOB_ID = re.compile(r"^[a-f0-9]{32}$")


class JobManager:
    def __init__(self, storage_dir: str | pathlib.Path) -> None:
        self.storage_dir = pathlib.Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _job_dir(self, job_id: str) -> pathlib.Path:
        if not _VALID_JOB_ID.match(job_id):
            msg = f"Invalid job ID: '{job_id}'"
            raise KeyError(msg)
        return self.storage_dir / job_id

    def _metadata_path(self, job_id: str) -> pathlib.Path:
        return self._job_dir(job_id) / METADATA_FILE

    @contextmanager
    def _job_lock(self, job_id: str) -> Iterator[None]:
        lock_path = self._job_dir(job_id) / LOCK_FILE
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read_metadata(self, job_id: str) -> JobMetadata | None:
        path = self._metadata_path(job_id)
        if not path.exists():
            return None
        return JobMetadata.model_validate_json(path.read_text(encoding="utf-8"))

    def _write_metadata(self, job_id: str, metadata: JobMetadata) -> None:
        path = self._metadata_path(job_id)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(metadata.model_dump_json(), encoding="utf-8")
        tmp_path.replace(path)

    def create_job(self, params: MergeJobStartParams) -> str:
        job_id = uuid.uuid4().hex
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True)
        metadata = JobMetadata(job_id=job_id, status=JobStatus.ACTIVE, created_at=datetime.now(UTC), params=params)
        self._write_metadata(job_id, metadata)
        return job_id

    def get_job_metadata(self, job_id: str) -> JobMetadata | None:
        return self._read_metadata(job_id)

    def add_pdf(self, job_id: str, pdf_data: bytes) -> None:
        if not self._job_dir(job_id).exists():
            msg = f"Job '{job_id}' not found"
            raise KeyError(msg)
        with self._job_lock(job_id):
            metadata = self._read_metadata(job_id)
            if metadata is None:
                msg = f"Job '{job_id}' not found"
                raise KeyError(msg)
            if metadata.status != JobStatus.ACTIVE:
                msg = f"Job '{job_id}' is not active"
                raise ValueError(msg)
            pdf_path = self._job_dir(job_id) / f"{metadata.pdf_count:03d}.pdf"
            pdf_path.write_bytes(pdf_data)
            metadata.pdf_count += 1
            self._write_metadata(job_id, metadata)

    def complete_job(self, job_id: str) -> pathlib.Path:
        if not self._job_dir(job_id).exists():
            msg = f"Job '{job_id}' not found"
            raise KeyError(msg)
        with self._job_lock(job_id):
            metadata = self._read_metadata(job_id)
            if metadata is None:
                msg = f"Job '{job_id}' not found"
                raise KeyError(msg)
            if metadata.status == JobStatus.COMPLETED:
                result = self._job_dir(job_id) / RESULT_FILE
                if result.exists():
                    return result
            if metadata.pdf_count == 0:
                msg = "No documents were added to the job"
                raise ValueError(msg)
            pdf_documents = []
            for i in range(metadata.pdf_count):
                pdf_path = self._job_dir(job_id) / f"{i:03d}.pdf"
                pdf_documents.append(pdf_path.read_bytes())
            merged_pdf = merge_pdfs(pdf_documents)
            result_path = self._job_dir(job_id) / RESULT_FILE
            result_path.write_bytes(merged_pdf)
            metadata.status = JobStatus.COMPLETED
            metadata.completed_at = datetime.now(UTC)
            self._write_metadata(job_id, metadata)
            logger.info("Completed job '%s': merged %d documents, %d bytes", job_id, metadata.pdf_count, len(merged_pdf))
            return result_path

    def get_result_path(self, job_id: str) -> pathlib.Path | None:
        result = self._job_dir(job_id) / RESULT_FILE
        if result.exists():
            return result
        return None

    def delete_job(self, job_id: str) -> None:
        job_dir = self._job_dir(job_id)
        if job_dir.exists():
            shutil.rmtree(job_dir)

    def list_jobs(self) -> list[JobMetadata]:
        jobs: list[JobMetadata] = []
        if not self.storage_dir.exists():
            return jobs
        for entry in self.storage_dir.iterdir():
            if entry.is_dir():
                metadata = self._read_metadata(entry.name)
                if metadata is not None:
                    jobs.append(metadata)
        return jobs
