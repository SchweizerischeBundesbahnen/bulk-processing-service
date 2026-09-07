[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)
[![Bugs](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=bugs)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)
[![Code Smells](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=code_smells)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=coverage)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)
[![Duplicated Lines (%)](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=duplicated_lines_density)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)
[![Lines of Code](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=ncloc)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)
[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)
[![Vulnerabilities](https://sonarcloud.io/api/project_badges/measure?project=SchweizerischeBundesbahnen_bulk-processing-service&metric=vulnerabilities)](https://sonarcloud.io/summary/new_code?id=SchweizerischeBundesbahnen_bulk-processing-service)


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
| `WEASYPRINT_SERVICE_URL` | `http://localhost:9080` | URL of the WeasyPrint service (e.g. `http://weasyprint-service:9080`) |
| `WEASYPRINT_TIMEOUT` | `300` | Timeout in seconds for WeasyPrint HTTP requests |
| `WEASYPRINT_API_KEY` | — | API key sent to WeasyPrint (as `X-API-Key`) when it requires authentication. Only sent over `https`; a plain-http `WEASYPRINT_SERVICE_URL` refuses the request |
| `JOB_STORAGE_DIR` | `/data/jobs` | Directory for storing job data (metadata, PDFs, results) |
| `JOB_TTL` | `24h` | Time-to-live for completed jobs before cleanup. Supports `h` (hours), `m` (minutes), `s` (seconds) |
| `REQUEST_BODY_LIMIT_MB` | `500` | Maximum request body size in MB. Returns 413 if exceeded |
| `LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `LOG_DIR` | `/opt/bulk-processing-service/logs` | Directory for log files |
| `DEBUG_DIR` | — | If set, saves incoming HTML and converted PDFs into a `debug/` subdirectory inside each job's storage directory. Debug files are cleaned up automatically with the job by TTL cleanup |
| `PORT` | `9070` | HTTP port |
| `API_KEY` | — | Enables [API key authentication](#api-key) when set. Comma-separated list allows several keys for rotation |

TLS is configured through the `TLS_*` variables described under [HTTPS](#https).

## Security

### HTTPS

The server speaks plain HTTP by default, which is what a deployment behind a reverse proxy or an ingress expects: TLS terminates there and nothing has to be configured here. That remains the recommended setup where such a component is already in place.

Where the service is reached directly across a network, it can serve TLS itself. Set at least a certificate and its key:

| Environment Variable | Description |
|---|---|
| `TLS_CERT_FILE` | Certificate chain in PEM format |
| `TLS_KEY_FILE` | Private key in PEM format |
| `TLS_KEY_PASSWORD` | Password of the key, where it has one |

```bash
docker run --detach \
  --publish 9070:9070 \
  --volume /path/to/tls:/opt/bulk-processing-service/tls:ro \
  --env TLS_CERT_FILE=/opt/bulk-processing-service/tls/server.pem \
  --env TLS_KEY_FILE=/opt/bulk-processing-service/tls/server.key \
  --name bulk-processing-service \
  ghcr.io/schweizerischebundesbahnen/bulk-processing-service:latest
```

An incomplete configuration stops the start rather than falling back to plain HTTP. The material is loaded before the server listens, so a key which does not match its certificate or a wrong `TLS_KEY_PASSWORD` also stops the start, rather than surfacing at the first connection. Callers are authenticated by the API key below, not by client certificates.

**The container healthcheck** follows the configured scheme. It talks to its own process over loopback, so it does not verify the certificate.

**Certificate renewal.** The certificate is read once, at startup. A renewed certificate takes effect when the container restarts.

**Reaching WeasyPrint over https.** The paragraphs above are the inbound side. To have the service itself call WeasyPrint over `https`, set `WEASYPRINT_SERVICE_URL` to an `https` address. The server certificate is verified against the container's trust store, the same model the PDF Exporter uses with the JVM truststore: where WeasyPrint presents a privately signed certificate, install that CA into the container (for example with `update-ca-certificates`, or point the standard `SSL_CERT_FILE` at it) — the application carries no CA of its own. Where WeasyPrint requires an API key, send it with `WEASYPRINT_API_KEY` (only over https).

### API key

Authentication is disabled by default. It activates when `API_KEY` holds at least one non-empty key. Several keys can be configured as a comma-separated list, which allows key rotation without downtime. Clients send the key in one of two headers:

- `X-API-Key: <key>`
- `Authorization: Bearer <key>`

Only the merge endpoints under `/api/convert` are guarded. The `/health`, `/ready` and `/version` endpoints stay open, so probes keep working without a key. A missing or invalid key is answered with `401`.

Since the key is a reusable credential, name the service with an `https` address where a key is configured, so it is not put on the wire in the clear.

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

1. **Shared RWX volume** — all replicas must mount the same `JOB_STORAGE_DIR` (e.g. an NFS v4 or Kubernetes ReadWriteMany PVC). The filesystem must support POSIX record locks (`fcntl.lockf`).
2. **Sticky sessions** — all requests for a given job ID must reach the same replica, or any replica via the shared volume. Without sticky sessions, a load balancer may route `/add` to a different replica than `/start`, which works correctly through the shared filesystem but adds latency.
3. **Cleanup coordination** — each replica runs its own cleanup loop independently. File-level locking (`fcntl.lockf` with `LOCK_NB`) prevents a cleanup from deleting a job while another replica is writing to it. Multiple replicas performing cleanup on the same storage is safe but redundant — consider setting `JOB_TTL` to a high value or disabling cleanup on all but one replica.

**Limitations of multi-replica deployment:**
- `lockf` requires POSIX record lock support (NFS v4+; NFS v3 and some FUSE mounts may not support it)
- No distributed job registry — each replica discovers jobs by scanning the storage directory
- No request routing awareness — the service does not know about other replicas

## API

### Call Sequence

```
POST /api/convert/start          → 201, job ID
POST /api/convert/{id}/add       → 202 (repeat for each document)
POST /api/convert/{id}/finish    → 200, merged PDF
```

### Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/convert/start` | Create a merge job |
| POST | `/api/convert/{jobId}/add` | Convert and add a document |
| POST | `/api/convert/{jobId}/finish` | Merge all documents, return PDF |
| DELETE | `/api/convert/{jobId}` | Delete a job |
| GET | `/health` | Liveness (storage writable; 503 if not) |
| GET | `/ready` | Readiness (storage + WeasyPrint; 503 if either fails) |
| GET | `/version` | API version, service version, Python version |

### Wire Format

**POST /api/convert/start**

Request:
```json
{"fileName": "merged.pdf", "pdfVariant": "pdf/a-2b"}
```

Response: `201 Created`
```json
{"jobId": "a1b2c3d4e5f6..."}
```

**POST /api/convert/{jobId}/add**

Request:
```json
{
  "html": "<html>...</html>",
  "coverPageHtml": "<html>{{ PAGE_NUMBER }} of {{ PAGES_TOTAL_COUNT }}</html>",
  "params": {
    "presentationalHints": true,
    "pdfVariant": "pdf/a-2b",
    "scaleFactor": "2",
    "customMetadata": false,
    "fullFonts": false
  }
}
```

- `html` (required) — document HTML to convert
- `coverPageHtml` (optional) — cover page HTML; placeholders `{{ PAGE_NUMBER }}` and `{{ PAGES_TOTAL_COUNT }}` are resolved after conversion
- `params` (optional) — per-document WeasyPrint conversion parameters; defaults applied if omitted

Response: `202 Accepted`
```json
{"status": "accepted"}
```

On conversion failure: `202 Accepted` with `{"status": "failed"}` — the failure is recorded in job metadata and reported once in `X-Documents-Failed` at `/finish`. The job stays active for the remaining documents.

**POST /api/convert/{jobId}/finish**

Response: `200 OK` with `application/pdf` body and headers:

| Header | Description |
|---|---|
| `Content-Disposition` | `attachment; filename="merged.pdf"; filename*=UTF-8''merged.pdf` |
| `X-Documents-Merged` | Number of successfully converted documents |
| `X-Documents-Failed` | Number of failed documents (only present if > 0) |

Error responses:
- `400` — no documents added, or all documents failed
- `404` — job not found

### Job Lifecycle

1. **start** — creates a job directory on disk, returns a 32-character hex job ID
2. **add** (1..N times) — converts HTML to PDF via WeasyPrint using per-document `params`, stores the PDF on disk. If `coverPageHtml` is provided, the placeholder first page is replaced with the rendered cover page. Call order defines page order in the merged result. On conversion failure, the error is recorded but the job remains active for remaining documents
3. **finish** — merges all successfully converted PDFs into one file, marks the job as COMPLETED, and streams the result. Response headers report how many documents were merged and how many failed
4. **delete** (optional) — explicitly removes a job and all its data. Used by the caller for error cleanup (e.g. when the merge flow fails mid-way)

**Completed jobs are not deleted automatically after `/finish`.** The job data (metadata, individual PDFs, merged result) remains on disk and is removed only by TTL-based background cleanup. This allows re-downloading the result or debugging after completion. The caller may also explicitly `DELETE` a job if immediate cleanup is desired.

### TTL Cleanup

A background task periodically scans job storage and removes:
- Completed jobs older than `JOB_TTL` (based on `completed_at` timestamp)
- Stuck active jobs older than `2 × JOB_TTL` (safety net, based on `created_at`)

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
