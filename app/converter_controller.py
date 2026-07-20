from __future__ import annotations

import logging
import os
import pathlib
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response

from app.models import AddDocumentWithCoverRequest, MergeJobStartParams  # noqa: TC001
from app.pdf_merger import replace_first_page_with_cover
from app.weasyprint_client import WeasyPrintClient

if TYPE_CHECKING:
    from app.job_manager import JobManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/convert")

_job_manager: JobManager | None = None


def init_job_manager(job_manager: JobManager) -> None:
    global _job_manager  # noqa: PLW0603
    _job_manager = job_manager


def _get_job_manager() -> JobManager:
    if _job_manager is None:
        msg = "JobManager not initialized"
        raise RuntimeError(msg)
    return _job_manager


def get_weasyprint_client(job_url: str | None = None) -> WeasyPrintClient:
    base_url = os.environ.get("WEASYPRINT_SERVICE_URL") or job_url or "http://localhost:9080"
    timeout = float(os.environ.get("WEASYPRINT_TIMEOUT", "300"))
    return WeasyPrintClient(base_url=base_url, timeout=timeout)


def _save_debug_file(job_id: str, doc_index: int, suffix: str, data: str | bytes) -> None:
    debug_dir = os.environ.get("DEBUG_DIR")
    if not debug_dir:
        return
    pathlib.Path(debug_dir).mkdir(parents=True, exist_ok=True)
    path = pathlib.Path(f"{debug_dir}/{job_id}_{doc_index}{suffix}")
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)


@router.post("/start", status_code=201)
def start_merge_job(params: MergeJobStartParams) -> str:
    job_manager = _get_job_manager()
    job_id = job_manager.create_job(params)
    logger.info("Started merge job '%s' with fileName='%s'", job_id, params.file_name)
    return job_id


@router.post("/{job_id}/add", status_code=202)
async def add_document_to_job(job_id: str, request: Request) -> dict[str, str]:
    job_manager = _get_job_manager()
    metadata = job_manager.get_job_metadata(job_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    html_content = (await request.body()).decode("utf-8")
    doc_index = metadata.pdf_count

    _save_debug_file(job_id, doc_index, ".html", html_content)

    client = get_weasyprint_client(metadata.params.weasy_print_service_url)
    try:
        pdf_data = client.convert_html_to_pdf(html_content, metadata.params)
    except Exception as e:
        logger.exception("Failed to convert HTML to PDF for job '%s'", job_id)
        raise HTTPException(status_code=502, detail=f"WeasyPrint conversion failed: {e}") from e

    _save_debug_file(job_id, doc_index, ".pdf", pdf_data)

    job_manager.add_pdf(job_id, pdf_data)
    logger.info("Added document to job '%s' (total: %d)", job_id, doc_index + 1)
    return {"status": "accepted"}


@router.post("/{job_id}/add-with-cover", status_code=202)
async def add_document_with_cover_to_job(job_id: str, body: AddDocumentWithCoverRequest) -> dict[str, str]:
    job_manager = _get_job_manager()
    metadata = job_manager.get_job_metadata(job_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    doc_index = metadata.pdf_count

    _save_debug_file(job_id, doc_index, ".html", body.html)
    _save_debug_file(job_id, doc_index, "_cover.html", body.cover_page_html)

    client = get_weasyprint_client(metadata.params.weasy_print_service_url)
    try:
        content_pdf = client.convert_html_to_pdf(body.html, metadata.params)
        cover_pdf = client.convert_html_to_pdf(body.cover_page_html, metadata.params)
        pdf_data = replace_first_page_with_cover(content_pdf, cover_pdf)
    except Exception as e:
        logger.exception("Failed to convert HTML to PDF with cover page for job '%s'", job_id)
        raise HTTPException(status_code=502, detail=f"WeasyPrint conversion failed: {e}") from e

    _save_debug_file(job_id, doc_index, ".pdf", pdf_data)

    job_manager.add_pdf(job_id, pdf_data)
    logger.info("Added document with cover page to job '%s' (total: %d)", job_id, doc_index + 1)
    return {"status": "accepted"}


@router.post("/{job_id}/stop")
def finish_merge_job(job_id: str) -> Response:
    job_manager = _get_job_manager()
    try:
        result_path = job_manager.complete_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")  # noqa: B904
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    metadata = job_manager.get_job_metadata(job_id)
    file_name = metadata.params.file_name if metadata else "merged-document.pdf"

    return Response(
        content=result_path.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
