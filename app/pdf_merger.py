from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

from pypdf import PageObject, PdfReader, PdfWriter

if TYPE_CHECKING:
    import pathlib

# Marker text injected by pdf-exporter (PdfConverter.java) into the first page of documents
# that have a cover page. This page is a throwaway placeholder that gets replaced by the
# rendered cover page PDF. Detection is text-based: if the first page of a multi-page PDF
# contains this string (case-insensitive), it is treated as a placeholder.
PLACEHOLDER_MARKER = "page to be removed"


def count_pdf_pages(pdf_data: bytes) -> int:
    reader = PdfReader(io.BytesIO(pdf_data))
    return len(reader.pages)


def resolve_cover_page_placeholders(cover_html: str, page_count: int) -> str:
    """Replace page-number placeholders in cover page HTML.

    PAGE_NUMBER is always "1" — the cover page is the first page of its document.
    PAGES_TOTAL_COUNT is the page count of the individual document (including placeholder),
    not the total page count of the final merged PDF. This is intentional: cover pages
    describe their own document, not the entire merge batch.
    """
    result = re.sub(r"\{\{\s*PAGE_NUMBER\s*}}", "1", cover_html)
    return re.sub(r"\{\{\s*PAGES_TOTAL_COUNT\s*}}", str(page_count), result)


def _is_placeholder_page(page: PageObject) -> bool:
    text = page.extract_text() or ""
    return PLACEHOLDER_MARKER in text.lower()


def replace_first_page_with_cover(content_pdf: bytes, cover_pdf: bytes) -> bytes:
    content_reader = PdfReader(io.BytesIO(content_pdf))
    cover_reader = PdfReader(io.BytesIO(cover_pdf))
    writer = PdfWriter()
    writer.add_page(cover_reader.pages[0])
    has_placeholder = len(content_reader.pages) > 1 and _is_placeholder_page(content_reader.pages[0])
    content_pages = content_reader.pages[1:] if has_placeholder else content_reader.pages
    for page in content_pages:
        writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def merge_pdf_files(pdf_paths: list[pathlib.Path], output_path: pathlib.Path) -> None:
    writer = PdfWriter()
    for pdf_path in pdf_paths:
        reader = PdfReader(pdf_path)
        pages = reader.pages
        if len(pages) > 1 and _is_placeholder_page(pages[0]):
            pages = pages[1:]
        for page in pages:
            writer.add_page(page)
    with output_path.open("wb") as f:
        writer.write(f)
