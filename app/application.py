import uvicorn

from app.constants import PORT, setup_logging


def main() -> None:
    setup_logging()
    uvicorn.run("app.app:app", host="0.0.0.0", port=PORT)  # noqa: S104


if __name__ == "__main__":
    main()
