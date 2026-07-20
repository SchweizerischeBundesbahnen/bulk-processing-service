import io
import re

from pypdf import PageObject, PdfReader, PdfWriter

PLACEHOLDER_MARKER = "page to be removed"


def count_pdf_pages(pdf_data: bytes) -> int:
    reader = PdfReader(io.BytesIO(pdf_data))
    return len(reader.pages)


def resolve_cover_page_placeholders(cover_html: str, page_count: int) -> str:
    result = re.sub(r"\{\{\s*PAGE_NUMBER\s*}}", "1", cover_html)
    return re.sub(r"\{\{\s*PAGES_TOTAL_COUNT\s*}}", str(page_count), result)


def _is_placeholder_page(page: PageObject) -> bool:
    text = page.extract_text() or ""
    return PLACEHOLDER_MARKER in text.lower()


def replace_first_page_with_cover(content_pdf: bytes, cover_pdf: bytes) -> bytes:
    content_reader = PdfReader(io.BytesIO(content_pdf))
    cover_reader = PdfReader(io.BytesIO(cover_pdf))
    writer = PdfWriter()
    # Add only the first page from cover page PDF
    writer.add_page(cover_reader.pages[0])
    # Add all content pages except the placeholder first page
    for page in content_reader.pages[1:]:
        writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def merge_pdfs(pdf_documents: list[bytes]) -> bytes:
    writer = PdfWriter()
    for pdf_data in pdf_documents:
        reader = PdfReader(io.BytesIO(pdf_data))
        pages = reader.pages
        if len(pages) > 1 and _is_placeholder_page(pages[0]):
            pages = pages[1:]
        for page in pages:
            writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
