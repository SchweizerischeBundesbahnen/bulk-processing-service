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
        assert metadata.params.encoding == "utf-8"

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
