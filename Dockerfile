FROM ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff AS uv-source

FROM python:3.14.7-alpine@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc
LABEL maintainer="SBB Polarion Team <polarion-opensource@sbb.ch>"

ARG WORKING_DIR=/app
# DO NOT CHANGE APP_IMAGE_VERSION --> It is controlled by the pipeline
ARG APP_IMAGE_VERSION=0.0.0

WORKDIR ${WORKING_DIR}

# curl backs the healthcheck, which follows the configured http/https scheme.
# hadolint ignore=DL3018
RUN apk add --no-cache curl

# Copy uv binary from source stage
COPY --from=uv-source /uv /usr/local/bin/uv

COPY .tool-versions pyproject.toml uv.lock ${WORKING_DIR}/
COPY ./app/ ${WORKING_DIR}/app/
COPY healthcheck.sh ${WORKING_DIR}/healthcheck.sh
RUN chmod +x ${WORKING_DIR}/healthcheck.sh

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

RUN date -u +"%Y-%m-%dT%H:%M:%SZ" > .build_timestamp

ENV PATH="${WORKING_DIR}/.venv/bin:${PATH}"
ENV BULK_PROCESSING_SERVICE_VERSION=${APP_IMAGE_VERSION}
ENV JOB_STORAGE_DIR=/data/jobs
ENV JOB_TTL=24h

EXPOSE 9070

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/bin/sh", "-c", "./healthcheck.sh || exit 1"]

ENTRYPOINT [ "python", "-m", "app.application" ]
