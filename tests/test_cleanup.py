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

    def test_one_failure_does_not_break_entire_pass(self, tmp_path):
        manager = JobManager(tmp_path / "jobs")
        job1 = manager.create_job(MergeJobStartParams())
        job2 = manager.create_job(MergeJobStartParams())
        manager.add_pdf(job1, _make_test_pdf())
        manager.add_pdf(job2, _make_test_pdf())
        manager.complete_job(job1)
        manager.complete_job(job2)

        for jid in [job1, job2]:
            metadata = manager.get_job_metadata(jid)
            metadata.completed_at = datetime(2020, 1, 1, tzinfo=UTC)
            manager._write_metadata(jid, metadata)

        # Corrupt job1 dir so delete fails
        import shutil
        job1_dir = manager.storage_dir / job1
        lock_file = job1_dir / "lock"
        lock_file.chmod(0o000)

        _run_cleanup(manager, timedelta(hours=1))

        # job1 may or may not be deleted depending on OS, but job2 must be cleaned
        assert manager.get_job_metadata(job2) is None
        lock_file.chmod(0o644)  # restore for tmp_path cleanup

    def test_completed_job_skipped_when_locked(self, tmp_path):
        """fcntl.lockf is per-process — use subprocess to hold the lock."""
        import subprocess
        import sys

        manager = JobManager(tmp_path / "jobs")
        job_id = manager.create_job(MergeJobStartParams())
        manager.add_pdf(job_id, _make_test_pdf())
        manager.complete_job(job_id)

        metadata = manager.get_job_metadata(job_id)
        metadata.completed_at = datetime(2020, 1, 1, tzinfo=UTC)
        manager._write_metadata(job_id, metadata)

        holder_script = f"""
import sys, os, fcntl
lock_path = '{manager.storage_dir}/{job_id}/lock'
fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
fcntl.lockf(fd, fcntl.LOCK_EX)
print('locked', flush=True)
sys.stdin.readline()
fcntl.lockf(fd, fcntl.LOCK_UN)
os.close(fd)
"""
        holder = subprocess.Popen([sys.executable, "-c", holder_script], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        line = holder.stdout.readline().decode().strip()
        assert line == "locked"

        _run_cleanup(manager, timedelta(hours=1))

        # Job should NOT be deleted because lock is held by another process
        assert manager.get_job_metadata(job_id) is not None

        holder.stdin.close()
        holder.wait(timeout=5)
