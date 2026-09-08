from __future__ import annotations

import fcntl
import logging
import os
import pathlib
import re
import shutil
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.constants import DEBUG_DIR, sanitize_for_log
from app.models import JobMetadata, JobStatus
from app.pdf_merger import merge_pdf_files

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
        # fcntl locks are owned by the process, so they do not exclude the threads
        # of a single replica (FastAPI runs the sync handlers in a threadpool), and
        # closing any fd for the file drops the process's locks. A per-job in-process
        # lock guards the threads of this replica; the file lock guards other
        # replicas on the shared storage.
        self._thread_locks_guard = threading.Lock()
        self._thread_locks: dict[str, threading.Lock] = {}

    def _thread_lock(self, job_id: str) -> threading.Lock:
        with self._thread_locks_guard:
            lock = self._thread_locks.get(job_id)
            if lock is None:
                lock = threading.Lock()
                self._thread_locks[job_id] = lock
            return lock

    def _job_dir(self, job_id: str) -> pathlib.Path:
        if not _VALID_JOB_ID.fullmatch(job_id):
            msg = f"Invalid job ID: '{job_id}'"
            raise KeyError(msg)
        # Defence in depth on top of the id pattern: the real path must stay inside
        # the storage directory, so a job id can never escape it even if the pattern
        # above were ever loosened. Every job path is built from here.
        base = os.path.realpath(self.storage_dir)
        job_dir = os.path.realpath(base + os.sep + job_id)
        # The real path must sit strictly under the storage root. Requiring the
        # `base + os.sep` prefix rejects both an escape and the root itself (an id
        # like "", "." or "a/.." resolving to base, which would otherwise let
        # delete_job wipe the whole storage directory).
        if not job_dir.startswith(base + os.sep):
            msg = f"Invalid job ID: '{job_id}'"
            raise KeyError(msg)
        return pathlib.Path(job_dir)

    def _metadata_path(self, job_id: str) -> pathlib.Path:
        return self._job_dir(job_id) / METADATA_FILE

    @contextmanager
    def _job_lock(self, job_id: str) -> Iterator[None]:
        thread_lock = self._thread_lock(job_id)
        thread_lock.acquire()
        try:
            lock_path = self._job_dir(job_id) / LOCK_FILE
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
            try:
                fcntl.lockf(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.lockf(fd, fcntl.LOCK_UN)
                os.close(fd)
        finally:
            thread_lock.release()

    @contextmanager
    def _try_job_lock(self, job_id: str) -> Iterator[bool]:
        thread_lock = self._thread_lock(job_id)
        if not thread_lock.acquire(blocking=False):
            # Another thread of this replica holds the job; treat it as busy.
            yield False
            return
        try:
            lock_path = self._job_dir(job_id) / LOCK_FILE
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
            try:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                acquired = False
            try:
                yield acquired
            finally:
                if acquired:
                    fcntl.lockf(fd, fcntl.LOCK_UN)
                os.close(fd)
        finally:
            thread_lock.release()

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

    def add_pdf(self, job_id: str, pdf_data: bytes) -> int:
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
            doc_index = metadata.pdf_count
            pdf_path = self._job_dir(job_id) / f"{doc_index:03d}.pdf"
            pdf_path.write_bytes(pdf_data)
            metadata.pdf_count = doc_index + 1
            metadata.updated_at = datetime.now(UTC)
            self._write_metadata(job_id, metadata)
            return doc_index

    def record_failure(self, job_id: str) -> None:
        if not self._job_dir(job_id).exists():
            msg = f"Job '{job_id}' not found"
            raise KeyError(msg)
        with self._job_lock(job_id):
            metadata = self._read_metadata(job_id)
            if metadata is None:
                msg = f"Job '{job_id}' not found"
                raise KeyError(msg)
            metadata.failed_count += 1
            metadata.updated_at = datetime.now(UTC)
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
            pdf_paths = [self._job_dir(job_id) / f"{i:03d}.pdf" for i in range(metadata.pdf_count)]
            result_path = self._job_dir(job_id) / RESULT_FILE
            merge_pdf_files(pdf_paths, result_path)
            metadata.status = JobStatus.COMPLETED
            metadata.completed_at = datetime.now(UTC)
            self._write_metadata(job_id, metadata)
            logger.info("Completed job '%s': merged %d documents, result %d bytes", sanitize_for_log(job_id), metadata.pdf_count, result_path.stat().st_size)
            return result_path

    def get_result_path(self, job_id: str) -> pathlib.Path | None:
        result = self._job_dir(job_id) / RESULT_FILE
        if result.exists():
            return result
        return None

    def get_debug_dir(self, job_id: str) -> pathlib.Path | None:
        if not DEBUG_DIR:
            return None
        debug_path = self._job_dir(job_id) / "debug"
        debug_path.mkdir(exist_ok=True)
        return debug_path

    def delete_job(self, job_id: str) -> None:
        job_dir = self._job_dir(job_id)
        if job_dir.exists():
            shutil.rmtree(job_dir)
        # Job IDs are never reused, so the per-job lock is dead weight once the job
        # is gone. Drop it to keep the map bounded; a holder mid-delete keeps its own
        # reference to the Lock object, so this does not disturb it.
        with self._thread_locks_guard:
            self._thread_locks.pop(job_id, None)

    def list_jobs(self) -> list[JobMetadata]:
        jobs: list[JobMetadata] = []
        if not self.storage_dir.exists():
            return jobs
        for entry in self.storage_dir.iterdir():
            if entry.is_dir():
                # fullmatch to agree with _job_dir: a name like "<32 hex>\n" that
                # match() would accept (its $ allows a trailing newline) must be
                # skipped here, or _job_dir would reject it and break the sweep.
                if not _VALID_JOB_ID.fullmatch(entry.name):
                    logger.debug("Skipping non-job directory '%s' in storage dir", entry.name)
                    continue
                metadata = self._read_metadata(entry.name)
                if metadata is not None:
                    jobs.append(metadata)
        return jobs
