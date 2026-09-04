import logging

import uvicorn

from app.auth import get_api_keys
from app.constants import PORT, setup_logging
from app.tls import get_scheme, get_tls_options, load_tls_options

logger = logging.getLogger(__name__)


def start_server(port: int) -> None:
    # load_tls_options reads the TLS configuration and proves the material loads,
    # so a broken certificate stops the start here rather than at the first
    # connection. An empty result leaves the server on plain HTTP.
    uvicorn.run("app.app:app", host="0.0.0.0", port=port, **load_tls_options())  # noqa: S104


def main() -> None:
    setup_logging()

    # Read the TLS configuration before the server starts, so a broken one is
    # reported here instead of deep inside uvicorn.
    logger.info("Bulk processing service listening port: %d", PORT)
    logger.info("Bulk processing service scheme: %s", get_scheme(get_tls_options()))

    api_keys = get_api_keys()
    if api_keys:
        logger.info("API key authentication enabled (%d key(s) configured)", len(api_keys))
    else:
        logger.info("API key authentication disabled")

    start_server(PORT)


if __name__ == "__main__":
    main()
