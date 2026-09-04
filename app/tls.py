"""Optional TLS for the API server of this service.

The server speaks plain HTTP by default, which is what a deployment behind a
reverse proxy or an ingress expects. Where the service is reached directly, the
server can serve TLS instead, configured through the ``TLS_*`` variables.

Authentication of callers is handled by the API key, not by client certificates,
so only server-side TLS is configured here. An incomplete configuration stops the
start, it never falls back to plain HTTP: silently serving in the clear while an
operator believes otherwise is the one outcome this must not produce.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path
from typing import Any

TLS_PREFIX = "TLS_"

CERT_FILE = "CERT_FILE"
KEY_FILE = "KEY_FILE"


class TlsConfigurationError(ValueError):
    """Raised when the TLS configuration is incomplete or unusable."""


def _read(name: str) -> str:
    """Read an environment value, treating whitespace as unset."""
    return os.environ.get(name, "").strip()


def _readable_file(value: str, name: str) -> str:
    """Return the path, or explain which variable points at an unusable file."""
    path = Path(value)
    if not path.is_file():
        raise TlsConfigurationError(f"{name} points to '{value}', which is not a file")
    if not os.access(path, os.R_OK):
        raise TlsConfigurationError(f"{name} points to '{value}', which cannot be read")
    return value


def get_tls_options(prefix: str = TLS_PREFIX) -> dict[str, Any]:
    """
    Build the TLS keyword arguments for uvicorn from the environment.

    Args:
        prefix: Variable prefix, TLS_PREFIX by default.

    Returns:
        Keyword arguments for uvicorn.run. Empty when TLS is not configured,
        which leaves the server on plain HTTP.

    Raises:
        TlsConfigurationError: When the configuration is incomplete or unusable.
    """
    cert_file = _read(prefix + CERT_FILE)
    key_file = _read(prefix + KEY_FILE)

    if not cert_file and not key_file:
        return {}

    if not cert_file or not key_file:
        missing = prefix + (CERT_FILE if not cert_file else KEY_FILE)
        raise TlsConfigurationError(f"{missing} is missing, a certificate and its key are both required")

    options: dict[str, Any] = {
        "ssl_certfile": _readable_file(cert_file, prefix + CERT_FILE),
        "ssl_keyfile": _readable_file(key_file, prefix + KEY_FILE),
    }

    # The password is taken as it stands, whitespace included, it is a secret.
    key_password = os.environ.get(prefix + "KEY_PASSWORD")
    if key_password:
        options["ssl_keyfile_password"] = key_password

    return options


def verify_tls_material(options: dict[str, Any], prefix: str = TLS_PREFIX) -> None:
    """
    Load the configured certificate and key once, before the server needs them.

    Naming a readable file is not the same as holding usable material: a key
    which does not match its certificate, or a wrong password, would otherwise
    surface at the first connection, or be swallowed by a caller which tolerates
    a server failing to start.

    Args:
        options: Result of get_tls_options, empty for plain HTTP.
        prefix: Variable prefix the options were read with, for the message.

    Raises:
        TlsConfigurationError: When the material does not load.
    """
    if not options:
        return

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(options["ssl_certfile"], options["ssl_keyfile"], options.get("ssl_keyfile_password"))
    except OSError as e:  # ssl.SSLError derives from it
        raise TlsConfigurationError(f"{prefix + CERT_FILE} and {prefix + KEY_FILE} do not load together: {e}") from e


def load_tls_options(prefix: str = TLS_PREFIX) -> dict[str, Any]:
    """
    Read the configuration and prove the material loads.

    This is what the server start uses. get_tls_options alone reads the rules,
    which is what the configuration is checked against in tests.
    """
    options = get_tls_options(prefix)
    verify_tls_material(options, prefix)
    return options


def get_scheme(tls_options: dict[str, Any]) -> str:
    """Return the URL scheme the given options serve."""
    return "https" if tls_options else "http"
