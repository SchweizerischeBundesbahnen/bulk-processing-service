from __future__ import annotations

import contextlib
import logging
import re
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.auth import require_api_key
from app.constants import WEASYPRINT_API_KEY, WEASYPRINT_SERVICE_URL, WEASYPRINT_SERVICE_URL_DEFAULT, WEASYPRINT_TIMEOUT, sanitize_for_log
from app.job_manager import JobManager
from app.models import AddDocumentRequest, MergeJobStartParams  # noqa: TC001
from app.pdf_merger import count_pdf_pages, replace_first_page_with_cover, resolve_cover_page_placeholders
from app.weasyprint_client import WeasyPrintClient

logger = logging.getLogger(__name__)

# The merge endpoints carry document content, so they are guarded by the optional
# API key. The dependency is a no-op until API_KEY is configured. Probes and the
# version endpoint stay open, as they carry nothing worth protecting.
router = APIRouter(prefix="/api/convert", dependencies=[Depends(require_api_key)])


def _get_job_manager(request: Request) -> JobManager:
    return request.app.state.job_manager  # type: ignore[no-any-return]


JobManagerDep = Annotated[JobManager, Depends(_get_job_manager)]


def get_weasyprint_client() -> WeasyPrintClient:
    base_url = WEASYPRINT_SERVICE_URL or WEASYPRINT_SERVICE_URL_DEFAULT
    return WeasyPrintClient(base_url=base_url, timeout=WEASYPRINT_TIMEOUT, api_key=WEASYPRINT_API_KEY or None)


def _save_debug_file(job_manager: JobManager, job_id: str, doc_index: int, suffix: str, data: str | bytes) -> None:
    debug_dir = job_manager.get_debug_dir(job_id)
    if debug_dir is None:
        return
    path = debug_dir / f"{doc_index:03d}{suffix}"
    # Debug output is best-effort: the document is already stored, so a failed
    # debug write must not turn a successful add into an error.
    try:
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8")
        else:
            path.write_bytes(data)
    except OSError:
        logger.warning("Could not write debug file '%s' for job '%s'", path.name, job_id)


@router.post("/start", status_code=201)
def start_merge_job(params: MergeJobStartParams, job_manager: JobManagerDep) -> dict[str, str]:
    job_id = job_manager.create_job(params)
    logger.info("Started merge job '%s' with fileName='%s'", job_id, sanitize_for_log(params.file_name))
    return {"jobId": job_id}


@router.post("/{job_id}/add", status_code=202)
def add_document_to_job(job_id: str, body: AddDocumentRequest, job_manager: JobManagerDep) -> dict[str, str]:
    try:
        metadata = job_manager.get_job_metadata(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")  # noqa: B904
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    client = get_weasyprint_client()
    try:
        content_pdf = client.convert_html_to_pdf(body.html, body.params)
        if body.cover_page_html:
            page_count = count_pdf_pages(content_pdf)
            cover_html = resolve_cover_page_placeholders(body.cover_page_html, page_count)
            cover_pdf = client.convert_html_to_pdf(cover_html, body.params)
            pdf_data = replace_first_page_with_cover(content_pdf, cover_pdf)
        else:
            pdf_data = content_pdf
    except Exception:
        # A per-document render failure does not abort the batch: it is recorded here
        # (surfaced in X-Documents-Failed at finish) and reported back as an accepted
        # 202 so the caller does not also count it. Returning an error would make the
        # server and the caller each count the same failure, inflating the total.
        logger.exception("Failed to convert HTML to PDF for job '%s'", job_id)
        with contextlib.suppress(Exception):
            job_manager.record_failure(job_id)
        return {"status": "failed"}

    try:
        doc_index = job_manager.add_pdf(job_id, pdf_data)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")  # noqa: B904
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    _save_debug_file(job_manager, job_id, doc_index, ".html", body.html)
    if body.cover_page_html:
        _save_debug_file(job_manager, job_id, doc_index, "_cover.html", body.cover_page_html)
    _save_debug_file(job_manager, job_id, doc_index, ".pdf", pdf_data)

    logger.info("Added document to job '%s' (total: %d)", job_id, doc_index + 1)
    return {"status": "accepted"}


@router.post("/{job_id}/finish")
def finish_merge_job(job_id: str, job_manager: JobManagerDep) -> FileResponse:
    try:
        metadata_before = job_manager.get_job_metadata(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")  # noqa: B904
    if metadata_before is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if metadata_before.pdf_count == 0 and metadata_before.failed_count > 0:
        raise HTTPException(status_code=400, detail=f"All {metadata_before.failed_count} documents failed to convert")

    try:
        result_path = job_manager.complete_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")  # noqa: B904
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    metadata = job_manager.get_job_metadata(job_id)
    file_name = metadata.params.file_name if metadata else "merged-document.pdf"
    safe_name = re.sub(r'[\x00-\x1f"\\/]', "_", file_name)
    encoded_name = quote(file_name, safe="")

    headers: dict[str, str] = {
        "Content-Disposition": f"attachment; filename=\"{safe_name}\"; filename*=UTF-8''{encoded_name}",
        "X-Documents-Merged": str(metadata.pdf_count) if metadata else "0",
    }
    if metadata and metadata.failed_count > 0:
        headers["X-Documents-Failed"] = str(metadata.failed_count)

    return FileResponse(path=result_path, media_type="application/pdf", headers=headers)


@router.delete("/{job_id}", status_code=204)
def delete_merge_job(job_id: str, job_manager: JobManagerDep) -> None:
    try:
        metadata = job_manager.get_job_metadata(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")  # noqa: B904
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    job_manager.delete_job(job_id)
    logger.info("Deleted merge job '%s'", job_id)
