[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)
[![Bugs](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=bugs)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)
[![Code Smells](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=code_smells)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=coverage)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)

# Bulk Processing Service

Bulk PDF export service for Polarion PDF Exporter. Accepts HTML documents, converts them to PDF via WeasyPrint, and merges into a single output file with optional cover pages.

## Running the Service

```bash
docker run --detach \
  --publish 9070:9070 \
  --name bulk-processing-service \
  ghcr.io/schweizerischebundesbahnen/bulk-processing-service:latest
```

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `WEASYPRINT_SERVICE_URL` | — | Fallback URL of the WeasyPrint service (e.g. `http://weasyprint-service:9080`). The URL from the job start request (`weasyPrintServiceUrl`) takes precedence if provided, otherwise this env var is used, falling back to `http://localhost:9080` |
| `WEASYPRINT_TIMEOUT` | `300` | Timeout in seconds for WeasyPrint HTTP requests |
| `JOB_STORAGE_DIR` | `/data/jobs` | Directory for storing job data (metadata, PDFs, results) |
| `JOB_TTL` | `3h` | Time-to-live for completed jobs before cleanup. Supports `h` (hours), `m` (minutes), `s` (seconds) |
| `REQUEST_BODY_LIMIT_MB` | `500` | Maximum request body size in MB. Returns 413 if exceeded |
| `LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `LOG_DIR` | `/opt/bulk-processing-service/logs` | Directory for log files |
| `DEBUG_DIR` | — | If set, saves incoming HTML and converted PDFs to this directory for debugging |
| `PORT` | `9070` | HTTP port |

## Deployment Topology and Scaling

### Single replica (default)

The service is designed to run as a **single replica**. Job state lives on the local filesystem, and a background in-process task handles TTL cleanup. This is the simplest and recommended deployment:

```bash
docker run --detach \
  --publish 9070:9070 \
  --name bulk-processing-service \
  --env WEASYPRINT_SERVICE_URL=http://weasyprint-service:9080 \
  ghcr.io/schweizerischebundesbahnen/bulk-processing-service:latest
```

All requests for a given job must reach the same instance. The PDF Exporter sends `start → add → ... → finish` sequentially from a single thread, so this is naturally satisfied with a single replica.

### Persistent storage

By default, job data is stored inside the container at `/data/jobs` and is lost when the container is removed. To persist across restarts, mount an external volume:

```bash
docker run --detach \
  --publish 9070:9070 \
  --name bulk-processing-service \
  --volume /path/on/host:/data/jobs \
  --env WEASYPRINT_SERVICE_URL=http://weasyprint-service:9080 \
  ghcr.io/schweizerischebundesbahnen/bulk-processing-service:latest
```

### Multiple replicas (shared volume)

Running multiple replicas is possible but requires:

1. **Shared RWX volume** — all replicas must mount the same `JOB_STORAGE_DIR` (e.g. an NFS v4 or Kubernetes ReadWriteMany PVC). The filesystem must support POSIX `flock(2)`.
2. **Sticky sessions** — all requests for a given job ID must reach the same replica, or any replica via the shared volume. Without sticky sessions, a load balancer may route `/add` to a different replica than `/start`, which works correctly through the shared filesystem but adds latency.
3. **Cleanup coordination** — each replica runs its own cleanup loop independently. File-level locking (`fcntl.flock` with `LOCK_NB`) prevents a cleanup from deleting a job while another replica is writing to it. Multiple replicas performing cleanup on the same storage is safe but redundant — consider setting `JOB_TTL` to a high value or disabling cleanup on all but one replica.

**Limitations of multi-replica deployment:**
- `flock` may not work on all network filesystems (NFS v3, some FUSE mounts)
- No distributed job registry — each replica discovers jobs by scanning the storage directory
- No request routing awareness — the service does not know about other replicas

## API

### Call Sequence

```
start → add (1..N times) → finish
```

### Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/convert/start` | Create a new merge job. Accepts `MergeJobStartParams` JSON, returns job ID |
| POST | `/api/convert/{jobId}/add` | Add a document (`{"html": "...", "coverPageHtml": "..."}`, cover page is optional) |
| POST | `/api/convert/{jobId}/finish` | Merge all documents and return the resulting PDF |
| DELETE | `/api/convert/{jobId}` | Delete a job |
| GET | `/health` | Liveness check (process and local storage only; returns 503 if storage is not writable) |
| GET | `/ready` | Readiness check (also verifies the downstream WeasyPrint service; returns 503 if unavailable) |
| GET | `/version` | Service version and API version |

### Job Lifecycle

- **start** creates a job, returns a 32-character hex job ID
- **add** sends HTML to WeasyPrint for conversion, stores the resulting PDF on disk. If `coverPageHtml` is provided, it replaces the placeholder first page with a rendered cover page. Call order defines page order in the final PDF
- **finish** merges all PDFs into one, stores the result, marks the job as completed, and returns the merged PDF. The job data remains on disk until TTL-based cleanup removes it

### TTL Cleanup

A background task periodically scans job storage and removes:
- Completed jobs older than `JOB_TTL`
- Stuck active jobs older than `2 × JOB_TTL` (safety net)

## Development

### Building

```bash
docker build \
  --build-arg APP_IMAGE_VERSION=0.0.0 \
  --tag bulk-processing-service:0.0.0 .
```

### Testing

```bash
uv run tox
```

### REST API Documentation

Interactive API docs available at `/docs` when the service is running.
