"""Tests for the optional TLS configuration."""

import shutil
import subprocess
from pathlib import Path

import pytest

from app import application
from app.tls import (
    TLS_PREFIX,
    TlsConfigurationError,
    get_scheme,
    get_tls_options,
    load_tls_options,
    verify_tls_material,
)

TLS_VARIABLES = [TLS_PREFIX + suffix for suffix in ("CERT_FILE", "KEY_FILE", "KEY_PASSWORD")]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from an unconfigured environment."""
    for name in TLS_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def pem_files(tmp_path: Path) -> tuple[str, str]:
    """Two readable files standing in for a certificate and a key."""
    paths = []
    for name in ("cert.pem", "key.pem"):
        path = tmp_path / name
        path.write_text(f"-----BEGIN {name}-----\n", encoding="utf-8")
        paths.append(str(path))
    return paths[0], paths[1]


def test_unconfigured_environment_serves_plain_http() -> None:
    assert get_tls_options() == {}
    assert get_scheme({}) == "http"


def test_whitespace_only_value_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TLS_CERT_FILE", "   ")
    monkeypatch.setenv("TLS_KEY_FILE", "   ")

    assert get_tls_options() == {}


def test_certificate_and_key_enable_tls(monkeypatch: pytest.MonkeyPatch, pem_files: tuple[str, str]) -> None:
    cert, key = pem_files
    monkeypatch.setenv("TLS_CERT_FILE", cert)
    monkeypatch.setenv("TLS_KEY_FILE", key)

    options = get_tls_options()

    assert options == {"ssl_certfile": cert, "ssl_keyfile": key}
    assert get_scheme(options) == "https"


def test_key_password_is_passed_unchanged(monkeypatch: pytest.MonkeyPatch, pem_files: tuple[str, str]) -> None:
    cert, key = pem_files
    monkeypatch.setenv("TLS_CERT_FILE", cert)
    monkeypatch.setenv("TLS_KEY_FILE", key)
    monkeypatch.setenv("TLS_KEY_PASSWORD", "  pass phrase  ")

    assert get_tls_options()["ssl_keyfile_password"] == "  pass phrase  "


def test_certificate_without_key_is_rejected(monkeypatch: pytest.MonkeyPatch, pem_files: tuple[str, str]) -> None:
    cert, _ = pem_files
    monkeypatch.setenv("TLS_CERT_FILE", cert)

    with pytest.raises(TlsConfigurationError, match="TLS_KEY_FILE is missing"):
        get_tls_options()


def test_key_without_certificate_is_rejected(monkeypatch: pytest.MonkeyPatch, pem_files: tuple[str, str]) -> None:
    _, key = pem_files
    monkeypatch.setenv("TLS_KEY_FILE", key)

    with pytest.raises(TlsConfigurationError, match="TLS_CERT_FILE is missing"):
        get_tls_options()


def test_unreadable_certificate_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pem_files: tuple[str, str]) -> None:
    _, key = pem_files
    monkeypatch.setenv("TLS_CERT_FILE", str(tmp_path / "absent.pem"))
    monkeypatch.setenv("TLS_KEY_FILE", key)

    with pytest.raises(TlsConfigurationError, match="which is not a file"):
        get_tls_options()


def test_file_present_but_not_readable_is_rejected(monkeypatch: pytest.MonkeyPatch, pem_files: tuple[str, str]) -> None:
    cert, key = pem_files
    monkeypatch.setenv("TLS_CERT_FILE", cert)
    monkeypatch.setenv("TLS_KEY_FILE", key)
    monkeypatch.setattr("os.access", lambda *_args, **_kwargs: False)

    with pytest.raises(TlsConfigurationError, match="cannot be read"):
        get_tls_options()


def test_material_which_does_not_load_is_reported(monkeypatch: pytest.MonkeyPatch, pem_files: tuple[str, str]) -> None:
    """A readable file is not usable material: the key has to match its certificate."""
    cert, key = pem_files
    monkeypatch.setenv("TLS_CERT_FILE", cert)
    monkeypatch.setenv("TLS_KEY_FILE", key)

    with pytest.raises(TlsConfigurationError, match="do not load together"):
        load_tls_options()


def test_nothing_is_loaded_without_a_configuration() -> None:
    assert load_tls_options() == {}
    verify_tls_material({})


def test_real_material_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl is not available to produce a certificate")

    cert = tmp_path / "server.pem"
    key = tmp_path / "server.key"
    subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost"],
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("TLS_CERT_FILE", str(cert))
    monkeypatch.setenv("TLS_KEY_FILE", str(key))

    assert load_tls_options() == {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}


def test_server_is_started_with_the_configured_options(monkeypatch: pytest.MonkeyPatch, pem_files: tuple[str, str]) -> None:
    cert, key = pem_files
    monkeypatch.setattr(application, "load_tls_options", lambda: {"ssl_certfile": cert, "ssl_keyfile": key})
    captured: dict[str, object] = {}

    def fake_run(_app: object, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(application.uvicorn, "run", fake_run)
    application.start_server(9070)

    assert captured["ssl_certfile"] == cert
    assert captured["ssl_keyfile"] == key
