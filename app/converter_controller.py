from __future__ import annotations

import logging
import pathlib
import re
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.constants import DEBUG_DIR, WEASYPRINT_SERVICE_URL, WEASYPRINT_SERVICE_URL_DEFAULT, WEASYPRINT_TIMEOUT, sanitize_for_log
from app.job_manager import JobManager
from app.models import AddDocumentRequest, MergeJobStartParams  # noqa: TC001
from app.pdf_merger import count_pdf_pages, replace_first_page_with_cover, resolve_cover_page_placeholders
from app.weasyprint_client import WeasyPrintClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/convert")


def _get_job_manager(request: Request) -> JobManager:
    return request.app.state.job_manager  # type: ignore[no-any-return]


JobManagerDep = Annotated[JobManager, Depends(_get_job_manager)]


def get_weasyprint_client(job_url: str | None = None) -> WeasyPrintClient:
    base_url = job_url or WEASYPRINT_SERVICE_URL or WEASYPRINT_SERVICE_URL_DEFAULT
    return WeasyPrintClient(base_url=base_url, timeout=WEASYPRINT_TIMEOUT)


def _save_debug_file(job_id: str, doc_index: int, suffix: str, data: str | bytes) -> None:
    if not DEBUG_DIR:
        return
    pathlib.Path(DEBUG_DIR).mkdir(parents=True, exist_ok=True)
    path = pathlib.Path(f"{DEBUG_DIR}/{job_id}_{doc_index}{suffix}")
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)


@router.post("/start", status_code=201)
def start_merge_job(params: MergeJobStartParams, job_manager: JobManagerDep) -> str:
    job_id = job_manager.create_job(params)
    logger.info("Started merge job '%s' with fileName='%s'", job_id, sanitize_for_log(params.file_name))
    return job_id


@router.post("/{job_id}/add", status_code=202)
def add_document_to_job(job_id: str, body: AddDocumentRequest, job_manager: JobManagerDep) -> dict[str, str]:
    metadata = job_manager.get_job_metadata(job_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    client = get_weasyprint_client(metadata.params.weasy_print_service_url)
    try:
        content_pdf = client.convert_html_to_pdf(body.html, metadata.params)
        if body.cover_page_html:
            page_count = count_pdf_pages(content_pdf)
            cover_html = resolve_cover_page_placeholders(body.cover_page_html, page_count)
            cover_pdf = client.convert_html_to_pdf(cover_html, metadata.params)
            pdf_data = replace_first_page_with_cover(content_pdf, cover_pdf)
        else:
            pdf_data = content_pdf
    except Exception as e:
        logger.exception("Failed to convert HTML to PDF for job '%s'", job_id)
        raise HTTPException(status_code=502, detail=f"WeasyPrint conversion failed: {e}") from e

    try:
        doc_index = job_manager.add_pdf(job_id, pdf_data)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")  # noqa: B904
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    _save_debug_file(job_id, doc_index, ".html", body.html)
    if body.cover_page_html:
        _save_debug_file(job_id, doc_index, "_cover.html", body.cover_page_html)
    _save_debug_file(job_id, doc_index, ".pdf", pdf_data)

    logger.info("Added document to job '%s' (total: %d)", job_id, doc_index + 1)
    return {"status": "accepted"}


@router.post("/{job_id}/finish")
def finish_merge_job(job_id: str, job_manager: JobManagerDep) -> FileResponse:
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

    return FileResponse(
        path=result_path,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=\"{safe_name}\"; filename*=UTF-8''{encoded_name}"},
    )


@router.delete("/{job_id}", status_code=204)
def delete_merge_job(job_id: str, job_manager: JobManagerDep) -> None:
    if job_manager.get_job_metadata(job_id) is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    job_manager.delete_job(job_id)
    logger.info("Deleted merge job '%s'", job_id)
