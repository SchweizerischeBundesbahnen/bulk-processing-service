"""Tests for TTL cleanup and parse_ttl."""

import io
from datetime import UTC, datetime, timedelta

import pytest
from pypdf import PdfWriter

from app.cleanup import _run_cleanup, parse_ttl
from app.job_manager import JobManager
from app.models import MergeJobStartParams


def _make_test_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestParseTtl:
    def test_hours(self):
        assert parse_ttl("3h") == timedelta(hours=3)

    def test_minutes(self):
        assert parse_ttl("30m") == timedelta(minutes=30)

    def test_seconds(self):
        assert parse_ttl("3600s") == timedelta(seconds=3600)

    def test_with_spaces(self):
        assert parse_ttl("  12 h  ") == timedelta(hours=12)

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid TTL format"):
            parse_ttl("3days")

    def test_empty_string(self):
        with pytest.raises(ValueError, match="Invalid TTL format"):
            parse_ttl("")

    def test_no_unit(self):
        with pytest.raises(ValueError, match="Invalid TTL format"):
            parse_ttl("100")


class TestRunCleanup:
    def test_deletes_completed_job_older_than_ttl(self, tmp_path):
        manager = JobManager(tmp_path / "jobs")
        job_id = manager.create_job(MergeJobStartParams())
        manager.add_pdf(job_id, _make_test_pdf())
        manager.complete_job(job_id)

        # Backdate completed_at
        metadata = manager.get_job_metadata(job_id)
        metadata.completed_at = datetime(2020, 1, 1, tzinfo=UTC)
        manager._write_metadata(job_id, metadata)

        _run_cleanup(manager, timedelta(hours=1))

        assert manager.get_job_metadata(job_id) is None

    def test_keeps_recently_completed_job(self, tmp_path):
        manager = JobManager(tmp_path / "jobs")
        job_id = manager.create_job(MergeJobStartParams())
        manager.add_pdf(job_id, _make_test_pdf())
        manager.complete_job(job_id)

        _run_cleanup(manager, timedelta(hours=3))

        assert manager.get_job_metadata(job_id) is not None

    def test_deletes_stuck_active_job_after_double_ttl(self, tmp_path):
        manager = JobManager(tmp_path / "jobs")
        job_id = manager.create_job(MergeJobStartParams())

        # Backdate created_at
        metadata = manager.get_job_metadata(job_id)
        metadata.created_at = datetime(2020, 1, 1, tzinfo=UTC)
        manager._write_metadata(job_id, metadata)

        _run_cleanup(manager, timedelta(hours=1))

        assert manager.get_job_metadata(job_id) is None

    def test_keeps_recent_active_job(self, tmp_path):
        manager = JobManager(tmp_path / "jobs")
        job_id = manager.create_job(MergeJobStartParams())

        _run_cleanup(manager, timedelta(hours=1))

        assert manager.get_job_metadata(job_id) is not None
