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
| `WEASYPRINT_SERVICE_URL` | — | URL of the WeasyPrint service (e.g. `http://weasyprint-service:9080`). If not set, uses the URL from the job start request, falling back to `http://localhost:9080` |
| `WEASYPRINT_TIMEOUT` | `300` | Timeout in seconds for WeasyPrint HTTP requests |
| `JOB_STORAGE_DIR` | `/data/jobs` | Directory for storing job data (metadata, PDFs, results) |
| `JOB_TTL` | `3h` | Time-to-live for completed jobs before cleanup. Supports `h` (hours), `m` (minutes), `s` (seconds) |
| `DEBUG_DIR` | — | If set, saves incoming HTML and converted PDFs to this directory for debugging |
| `PORT` | `9070` | HTTP port |

## Persistent Storage

By default, job data is stored inside the container at `/data/jobs` and is lost when the container is removed.

To persist job data across container restarts or share it between multiple service instances, mount an external volume:

```bash
docker run --detach \
  --publish 9070:9070 \
  --name bulk-processing-service \
  --volume /path/on/host:/data/jobs \
  --env WEASYPRINT_SERVICE_URL=http://weasyprint-service:9080 \
  ghcr.io/schweizerischebundesbahnen/bulk-processing-service:latest
```

When using a shared volume, file-level locking (`fcntl.flock`) ensures safe concurrent access from multiple service instances. The filesystem must be POSIX-compliant (local disk, NFS v4).

## API

### Call Sequence

```
start → add / add-with-cover (1..N times) → stop
```

### Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/convert/start` | Create a new merge job. Accepts `MergeJobStartParams` JSON, returns job ID |
| POST | `/api/convert/{jobId}/add` | Add a document (raw HTML body, `Content-Type: text/html`) |
| POST | `/api/convert/{jobId}/add-with-cover` | Add a document with cover page (`{"html": "...", "coverPageHtml": "..."}`) |
| POST | `/api/convert/{jobId}/stop` | Merge all documents and return the resulting PDF |

### Job Lifecycle

- **start** creates a job, returns a 32-character hex job ID
- **add / add-with-cover** sends HTML to WeasyPrint for conversion, stores the resulting PDF on disk. Call order defines page order in the final PDF
- **stop** merges all PDFs into one, stores the result, marks the job as completed, and returns the merged PDF. The job data remains on disk until TTL-based cleanup removes it

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
