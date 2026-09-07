"""Tests for configuration helpers in app.constants."""

import logging
import pathlib

import pytest

from app import constants


@pytest.fixture(autouse=True)
def restore_root_logger():
    """setup_logging mutates the root logger; snapshot and restore it."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


class TestRequestBodyLimit:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("REQUEST_BODY_LIMIT_MB", raising=False)
        assert constants.get_request_body_limit_bytes() == 500 * 1024 * 1024

    def test_custom_value(self, monkeypatch):
        monkeypatch.setenv("REQUEST_BODY_LIMIT_MB", "10")
        assert constants.get_request_body_limit_bytes() == 10 * 1024 * 1024

    def test_non_numeric_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("REQUEST_BODY_LIMIT_MB", "not-a-number")
        assert constants.get_request_body_limit_bytes() == 500 * 1024 * 1024

    def test_non_positive_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("REQUEST_BODY_LIMIT_MB", "0")
        assert constants.get_request_body_limit_bytes() == 500 * 1024 * 1024


class TestSanitizeForLog:
    def test_strips_control_characters(self):
        assert constants.sanitize_for_log("a\nb\tc\r\x00d") == "abcd"

    def test_keeps_normal_text(self):
        assert constants.sanitize_for_log("job-123 ok") == "job-123 ok"


class TestReadBuildTimestamp:
    def test_reads_file_when_present(self):
        ts_file = pathlib.Path(constants.__file__).parent.parent / ".build_timestamp"
        existed = ts_file.exists()
        original = ts_file.read_text(encoding="utf-8") if existed else None
        try:
            ts_file.write_text("2026-01-02T03:04:05Z\n", encoding="utf-8")
            assert constants._read_build_timestamp() == "2026-01-02T03:04:05Z"
        finally:
            if existed:
                ts_file.write_text(original, encoding="utf-8")
            else:
                ts_file.unlink()

    def test_empty_when_absent(self, monkeypatch, tmp_path):
        # Point the parent at an empty dir so the file is absent.
        monkeypatch.setattr(constants, "__file__", str(tmp_path / "app" / "constants.py"))
        assert constants._read_build_timestamp() == ""


class TestSetupLogging:
    def test_adds_file_handler(self, monkeypatch, tmp_path):
        log_dir = tmp_path / "logs"
        monkeypatch.setattr(constants, "LOG_DIR", str(log_dir))

        constants.setup_logging()

        root = logging.getLogger()
        assert any(isinstance(h, logging.FileHandler) for h in root.handlers)
        assert list(log_dir.glob("*.log"))

    def test_survives_unwritable_log_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(constants, "LOG_DIR", str(tmp_path / "logs"))

        def boom(*_args, **_kwargs):
            raise OSError("read-only file system")

        monkeypatch.setattr(pathlib.Path, "mkdir", boom)

        constants.setup_logging()  # must not raise

        root = logging.getLogger()
        assert not any(isinstance(h, logging.FileHandler) for h in root.handlers)
