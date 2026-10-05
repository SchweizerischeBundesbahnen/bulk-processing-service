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

# The link of a page into the structure tree of its document, which a tagged PDF variant has
STRUCT_PARENTS = "/StructParents"

# The navigation of a document: its bookmarks and named destinations, and whether a viewer opens with the bookmarks shown
NAVIGATION = ("/Outlines", "/Names")


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


def _detach_from_structure(page: PageObject) -> None:
    """Remove the link of a page into a structure tree.

    The structure tree of a result is the one of the document it is built on. The structure parents of a page of
    another document would point into that tree, at elements which are not the page's own.
    """
    if STRUCT_PARENTS in page:
        del page[STRUCT_PARENTS]


def _drop_navigation(writer: PdfWriter) -> None:
    """Remove the bookmarks and named destinations a merge took over from its first document, which describe that document alone."""
    root = writer._root_object
    for key in NAVIGATION:
        if key in root:
            del root[key]
    if root.get("/PageMode") == "/UseOutlines":
        del root["/PageMode"]


def replace_first_page_with_cover(content_pdf: bytes, cover_pdf: bytes) -> bytes:
    """Put the first page of the cover in place of the placeholder page of the content.

    The result is built on the content, so it keeps what WeasyPrint wrote into its catalog for the PDF variant asked for:
    the metadata, the output intent, the structure tree, the language, and the file identifier.
    """
    content_reader = PdfReader(io.BytesIO(content_pdf))
    cover_reader = PdfReader(io.BytesIO(cover_pdf))
    if not cover_reader.pages:
        msg = "Cover page PDF has no pages"
        raise ValueError(msg)
    has_placeholder = len(content_reader.pages) > 1 and _is_placeholder_page(content_reader.pages[0])
    writer = PdfWriter(clone_from=content_reader)
    if has_placeholder:
        writer.remove_page(0)
    _detach_from_structure(writer.insert_page(cover_reader.pages[0], 0))
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def merge_pdf_files(pdf_paths: list[pathlib.Path], output_path: pathlib.Path) -> None:
    """Merge the documents into one, in their order.

    The result is built on the first document, so it keeps what WeasyPrint wrote into its catalog for the PDF variant
    asked for: the metadata, the output intent, the language and the file identifier, which a PDF/A or PDF/UA file needs.
    The pages of the other documents are added to it. Their structure trees are not merged, which a tagged variant needs.
    The navigation of the first document is dropped, as before: its bookmarks would stand for the whole merge.
    """
    writer: PdfWriter | None = None
    for pdf_path in pdf_paths:
        reader = PdfReader(pdf_path)
        skip_placeholder = len(reader.pages) > 1 and _is_placeholder_page(reader.pages[0])
        if writer is None:
            writer = PdfWriter(clone_from=reader)
            _drop_navigation(writer)
            if skip_placeholder:
                writer.remove_page(0)
            continue
        pages = reader.pages[1:] if skip_placeholder else reader.pages
        for page in pages:
            _detach_from_structure(writer.add_page(page))
    with output_path.open("wb") as f:
        (writer or PdfWriter()).write(f)
