import logging
import os
import pathlib
import re
from datetime import UTC, datetime

API_VERSION = 1

LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_DIR: str = os.environ.get("LOG_DIR", "/opt/bulk-processing-service/logs")

WEASYPRINT_SERVICE_URL: str | None = os.environ.get("WEASYPRINT_SERVICE_URL")
WEASYPRINT_SERVICE_URL_DEFAULT = "http://localhost:9080"
WEASYPRINT_TIMEOUT: float = float(os.environ.get("WEASYPRINT_TIMEOUT", "300"))

# API key sent to WeasyPrint when it is started with authentication enabled. It is
# only ever put on an https transport; over plain http the request is refused so the
# credential is not exposed. Empty sends no key.
WEASYPRINT_API_KEY: str = os.environ.get("WEASYPRINT_API_KEY", "").strip()

JOB_STORAGE_DIR: str = os.environ.get("JOB_STORAGE_DIR") or str(pathlib.Path.home() / ".bulk-processing-service" / "jobs")
JOB_TTL: str = os.environ.get("JOB_TTL", "24h")

# Where Polarion publishes the keys its tokens are signed with, e.g. http://polarion/polarion/.well-known/jwks.json.
# When set, every call for a merge job has to carry a token Polarion issued for it (X-Polarion-Token), and a job only
# opens for the user it was started for. Off by default, so the service behaves as before.
POLARION_JWKS_URL: str | None = os.environ.get("POLARION_JWKS_URL", "").strip() or None
# The longest life, in seconds, a token may have been given: the tokens of the PDF Exporter live for minutes.
# The host name to ask Polarion for its keys under, where the address in POLARION_JWKS_URL is not the one Polarion
# knows itself by. Polarion answers a request only under the host name of its own base URL and refuses any other (400),
# so a service which reaches it by the name of a container, an IP address or a proxy has to say whose key set it wants.
# Not needed where POLARION_JWKS_URL is the base URL of Polarion.
POLARION_JWKS_HOST: str | None = os.environ.get("POLARION_JWKS_HOST", "").strip() or None
POLARION_TOKEN_MAX_AGE: int = int(os.environ.get("POLARION_TOKEN_MAX_AGE", "900"))

DEBUG_DIR: str | None = os.environ.get("DEBUG_DIR")

PORT: int = int(os.environ.get("PORT", "9070"))

SERVICE_VERSION: str = os.environ.get("BULK_PROCESSING_SERVICE_VERSION", "dev")


def _read_build_timestamp() -> str:
    timestamp_file = pathlib.Path(__file__).parent.parent / ".build_timestamp"
    if timestamp_file.exists():
        return timestamp_file.read_text(encoding="utf-8").strip()
    return ""


BUILD_TIMESTAMP: str = _read_build_timestamp()


def get_request_body_limit_bytes() -> int:
    default_mb = 500
    try:
        value = int(os.environ.get("REQUEST_BODY_LIMIT_MB", str(default_mb)))
        if value <= 0:
            value = default_mb
    except ValueError:
        value = default_mb
    return value * 1024 * 1024


REQUEST_BODY_LIMIT: int = get_request_body_limit_bytes()

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_for_log(value: str) -> str:
    # Replace line breaks explicitly (so a value cannot forge a log line) before
    # stripping the remaining control characters. The explicit newline replacement
    # is also the form static analysis recognises as a log-injection barrier.
    value = value.replace("\n", " ").replace("\r", " ")
    return _CONTROL_CHARS.sub("", value)


def setup_logging() -> None:
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    log_dir = pathlib.Path(LOG_DIR)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        current_time = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")
        file_handler = logging.FileHandler(log_dir / f"bulk-processing-service_{current_time}.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except OSError:
        pass

    root_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
