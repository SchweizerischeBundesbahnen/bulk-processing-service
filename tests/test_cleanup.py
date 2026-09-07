"""Tests for TTL cleanup and parse_ttl."""

import io
from datetime import UTC, datetime, timedelta

import pytest
from pypdf import PdfWriter

from app.cleanup import _cleanup_active_job, _cleanup_completed_job, _run_cleanup, cleanup_expired_jobs, parse_ttl
from app.job_manager import JobManager
from app.models import JobStatus, MergeJobStartParams


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

    def test_keeps_long_active_job_with_recent_activity(self, tmp_path):
        # An old job that is still making progress (recent add) must not be deleted.
        manager = JobManager(tmp_path / "jobs")
        job_id = manager.create_job(MergeJobStartParams())
        metadata = manager.get_job_metadata(job_id)
        metadata.created_at = datetime(2020, 1, 1, tzinfo=UTC)
        manager._write_metadata(job_id, metadata)
        manager.add_pdf(job_id, _make_test_pdf())  # refreshes updated_at to now

        _run_cleanup(manager, timedelta(hours=1))

        assert manager.get_job_metadata(job_id) is not None

    def test_deletes_active_job_idle_beyond_double_ttl(self, tmp_path):
        # No activity for well over 2x TTL -> genuinely stuck -> deleted.
        manager = JobManager(tmp_path / "jobs")
        job_id = manager.create_job(MergeJobStartParams())
        manager.add_pdf(job_id, _make_test_pdf())
        metadata = manager.get_job_metadata(job_id)
        metadata.created_at = datetime(2020, 1, 1, tzinfo=UTC)
        metadata.updated_at = datetime(2020, 1, 1, tzinfo=UTC)
        manager._write_metadata(job_id, metadata)

        _run_cleanup(manager, timedelta(hours=1))

        assert manager.get_job_metadata(job_id) is None

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


class TestCleanupBranches:
    def test_completed_job_without_completed_at_is_skipped(self, tmp_path):
        manager = JobManager(tmp_path / "jobs")
        job_id = manager.create_job(MergeJobStartParams())
        manager.add_pdf(job_id, _make_test_pdf())
        manager.complete_job(job_id)
        metadata = manager.get_job_metadata(job_id)
        metadata.completed_at = None  # missing timestamp -> cannot age it
        manager._write_metadata(job_id, metadata)

        _cleanup_completed_job(manager, manager.get_job_metadata(job_id), datetime.now(UTC), timedelta(hours=1))

        assert manager.get_job_metadata(job_id) is not None

    def test_active_cleanup_skips_when_locked(self, tmp_path):
        manager = JobManager(tmp_path / "jobs")
        job_id = manager.create_job(MergeJobStartParams())
        stale = manager.get_job_metadata(job_id)
        stale.created_at = datetime(2020, 1, 1, tzinfo=UTC)
        manager._write_metadata(job_id, stale)

        thread_lock = manager._thread_lock(job_id)
        thread_lock.acquire()
        try:
            _cleanup_active_job(manager, stale, datetime.now(UTC), timedelta(hours=1))
        finally:
            thread_lock.release()

        assert manager.get_job_metadata(job_id) is not None  # skipped, not deleted

    def test_active_cleanup_reread_sees_status_change(self, tmp_path, monkeypatch):
        manager = JobManager(tmp_path / "jobs")
        job_id = manager.create_job(MergeJobStartParams())
        stale = manager.get_job_metadata(job_id)
        stale.created_at = datetime(2020, 1, 1, tzinfo=UTC)

        # Under the lock the job is re-read as COMPLETED, so it must not be deleted.
        completed = stale.model_copy(update={"status": JobStatus.COMPLETED})
        monkeypatch.setattr(manager, "get_job_metadata", lambda _job_id: completed)

        _cleanup_active_job(manager, stale, datetime.now(UTC), timedelta(hours=1))

        assert (manager.storage_dir / job_id).exists()  # not deleted

    def test_active_cleanup_reread_sees_recent_activity(self, tmp_path):
        manager = JobManager(tmp_path / "jobs")
        job_id = manager.create_job(MergeJobStartParams())
        manager.add_pdf(job_id, _make_test_pdf())  # on-disk updated_at = now (fresh)

        stale = manager.get_job_metadata(job_id).model_copy(
            update={"created_at": datetime(2020, 1, 1, tzinfo=UTC), "updated_at": datetime(2020, 1, 1, tzinfo=UTC)}
        )

        _cleanup_active_job(manager, stale, datetime.now(UTC), timedelta(hours=1))

        assert manager.get_job_metadata(job_id) is not None  # re-read is fresh, kept

    @pytest.mark.asyncio
    async def test_loop_logs_error_and_continues(self, tmp_path, monkeypatch):
        import asyncio

        manager = JobManager(tmp_path / "jobs")
        calls = {"n": 0}

        async def fake_sleep(_seconds):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise asyncio.CancelledError

        def boom(*_args):
            raise RuntimeError("cleanup boom")

        monkeypatch.setattr("app.cleanup.asyncio.sleep", fake_sleep)
        monkeypatch.setattr("app.cleanup._run_cleanup", boom)

        with pytest.raises(asyncio.CancelledError):
            await cleanup_expired_jobs(manager, timedelta(hours=1))

        assert calls["n"] == 2  # first iteration's error was caught, then cancelled
