"""Tests for filesystem-based job manager."""

import io
from unittest.mock import patch

import pytest
from pypdf import PdfReader, PdfWriter

from app.job_manager import JobManager
from app.models import JobStatus, MergeJobStartParams

NONEXISTENT_JOB_ID = "00000000000000000000000000000000"


def _make_test_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def manager(tmp_path):
    return JobManager(tmp_path / "jobs")


@pytest.fixture
def default_params():
    return MergeJobStartParams()


class TestCreateJob:
    def test_creates_directory_and_metadata(self, manager, default_params):
        job_id = manager.create_job(default_params)
        assert (manager.storage_dir / job_id / "metadata.json").exists()
        metadata = manager.get_job_metadata(job_id)
        assert metadata is not None
        assert metadata.status == JobStatus.ACTIVE
        assert metadata.pdf_count == 0
        assert metadata.params.file_name == "merged-document.pdf"

    def test_returns_unique_ids(self, manager, default_params):
        ids = {manager.create_job(default_params) for _ in range(10)}
        assert len(ids) == 10


class TestGetJobMetadata:
    def test_returns_none_for_nonexistent(self, manager):
        assert manager.get_job_metadata(NONEXISTENT_JOB_ID) is None

    def test_returns_metadata(self, manager, default_params):
        job_id = manager.create_job(default_params)
        metadata = manager.get_job_metadata(job_id)
        assert metadata.job_id == job_id
        assert metadata.completed_at is None

    def test_rejects_invalid_job_id(self, manager):
        with pytest.raises(KeyError, match="Invalid job ID"):
            manager.get_job_metadata("../../../etc/passwd")


class TestAddPdf:
    def test_writes_pdf_files(self, manager, default_params):
        job_id = manager.create_job(default_params)
        manager.add_pdf(job_id, b"pdf_content_0")
        manager.add_pdf(job_id, b"pdf_content_1")

        assert (manager.storage_dir / job_id / "000.pdf").read_bytes() == b"pdf_content_0"
        assert (manager.storage_dir / job_id / "001.pdf").read_bytes() == b"pdf_content_1"

        metadata = manager.get_job_metadata(job_id)
        assert metadata.pdf_count == 2

    def test_raises_for_nonexistent_job(self, manager):
        with pytest.raises(KeyError, match="not found"):
            manager.add_pdf(NONEXISTENT_JOB_ID, b"pdf")

    def test_raises_for_completed_job(self, manager, default_params):
        job_id = manager.create_job(default_params)
        pdf = _make_test_pdf()
        manager.add_pdf(job_id, pdf)
        manager.complete_job(job_id)

        with pytest.raises(ValueError, match="not active"):
            manager.add_pdf(job_id, b"more_pdf")


class TestCompleteJob:
    def test_merges_and_writes_result(self, manager, default_params):
        job_id = manager.create_job(default_params)
        pdf = _make_test_pdf()
        manager.add_pdf(job_id, pdf)
        manager.add_pdf(job_id, pdf)

        result_path = manager.complete_job(job_id)
        assert result_path.exists()
        assert result_path.name == "result.pdf"

        reader = PdfReader(io.BytesIO(result_path.read_bytes()))
        assert len(reader.pages) == 2

        metadata = manager.get_job_metadata(job_id)
        assert metadata.status == JobStatus.COMPLETED
        assert metadata.completed_at is not None

    def test_returns_existing_result_if_already_completed(self, manager, default_params):
        job_id = manager.create_job(default_params)
        manager.add_pdf(job_id, _make_test_pdf())
        path1 = manager.complete_job(job_id)
        path2 = manager.complete_job(job_id)
        assert path1 == path2

    def test_raises_for_nonexistent_job(self, manager):
        with pytest.raises(KeyError, match="not found"):
            manager.complete_job(NONEXISTENT_JOB_ID)

    def test_raises_for_empty_job(self, manager, default_params):
        job_id = manager.create_job(default_params)
        with pytest.raises(ValueError, match="No documents"):
            manager.complete_job(job_id)


class TestGetResultPath:
    def test_returns_none_before_completion(self, manager, default_params):
        job_id = manager.create_job(default_params)
        assert manager.get_result_path(job_id) is None

    def test_returns_path_after_completion(self, manager, default_params):
        job_id = manager.create_job(default_params)
        manager.add_pdf(job_id, _make_test_pdf())
        manager.complete_job(job_id)
        assert manager.get_result_path(job_id) is not None


class TestDeleteJob:
    def test_removes_directory(self, manager, default_params):
        job_id = manager.create_job(default_params)
        manager.delete_job(job_id)
        assert not (manager.storage_dir / job_id).exists()

    def test_noop_for_nonexistent(self, manager):
        manager.delete_job(NONEXISTENT_JOB_ID)  # should not raise


class TestListJobs:
    def test_lists_all_jobs(self, manager, default_params):
        ids = {manager.create_job(default_params) for _ in range(3)}
        jobs = manager.list_jobs()
        assert {j.job_id for j in jobs} == ids

    def test_empty_storage(self, manager):
        assert manager.list_jobs() == []


class TestConcurrentAdd:
    def test_concurrent_adds_from_subprocesses_produce_unique_indices(self, manager, default_params):
        """Verify that file-level locking serialises concurrent writes from separate processes."""
        import subprocess
        import sys

        job_id = manager.create_job(default_params)
        pdf = _make_test_pdf()
        # Pre-write a PDF so the subprocess script can reference a real file
        test_pdf_path = manager.storage_dir / "test_payload.pdf"
        test_pdf_path.write_bytes(pdf)

        script = f"""
import sys
sys.path.insert(0, '.')
from app.job_manager import JobManager
manager = JobManager('{manager.storage_dir}')
pdf_data = open('{test_pdf_path}', 'rb').read()
idx = manager.add_pdf('{job_id}', pdf_data)
print(idx)
"""
        processes = [subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(5)]
        indices = []
        for p in processes:
            stdout, stderr = p.communicate(timeout=30)
            assert p.returncode == 0, f"Subprocess failed: {stderr.decode()}"
            indices.append(int(stdout.decode().strip()))

        assert sorted(indices) == list(range(5))
        metadata = manager.get_job_metadata(job_id)
        assert metadata.pdf_count == 5

    def test_try_lock_returns_false_when_held_by_another_process(self, manager, default_params):
        """fcntl.lockf is per-process — verify cross-process lock contention."""
        import subprocess
        import sys

        job_id = manager.create_job(default_params)

        # Script that holds the lock and waits for signal
        holder_script = f"""
import sys, os, fcntl, time
sys.path.insert(0, '.')
lock_path = '{manager.storage_dir}/{job_id}/lock'
fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
fcntl.lockf(fd, fcntl.LOCK_EX)
print('locked', flush=True)
# Wait until parent closes stdin
sys.stdin.readline()
fcntl.lockf(fd, fcntl.LOCK_UN)
os.close(fd)
"""
        holder = subprocess.Popen([sys.executable, "-c", holder_script], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        # Wait for lock to be acquired
        line = holder.stdout.readline().decode().strip()
        assert line == "locked"

        # Now try_lock from this process should fail
        with manager._try_job_lock(job_id) as acquired:
            assert acquired is False

        # Release holder
        holder.stdin.close()
        holder.wait(timeout=5)
