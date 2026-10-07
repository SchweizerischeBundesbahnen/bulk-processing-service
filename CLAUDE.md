# CLAUDE.md

- Use `uv` for all Python tooling (`uv run tox`, `uv run pytest`, `uv sync`, `uv build`) — never `pip`, `python -m pip`, or bare tool invocations.
- Always use `uv sync --locked` (CI and Dockerfile) — verifies lockfile is consistent with `pyproject.toml`; never `--frozen`.
- Docker builds require `--build-arg APP_IMAGE_VERSION=<version>` for correct versioning; omitting it defaults to `0.0.0`.
- Signed commits are required (GPG enforced via git config conditional includes).
- This is the bulk processing backend of the Polarion PDF Exporter. Keep code minimal and generic: the one piece of Polarion-specific logic is the optional verification of Polarion-issued tokens in `app/polarion_auth.py` (off unless `POLARION_JWKS_URL` is set); do not add other Polarion integration or domain-specific functionality.
- Line length is 240 characters (configured in ruff) — do not reformat to shorter lengths.
