"""Tests for PDF merger."""

import io

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.pdf_merger import count_pdf_pages, merge_pdfs, replace_first_page_with_cover, resolve_cover_page_placeholders


def _add_text_to_page(writer: PdfWriter, page_index: int, text: str) -> None:
    """Add extractable text to a page via raw content stream."""
    page = writer.pages[page_index]
    resources = page.get("/Resources")
    if resources is None:
        resources = DictionaryObject()
        page[NameObject("/Resources")] = resources

    font_dict = resources.get("/Font")
    if font_dict is None:
        font_dict = DictionaryObject()
        resources[NameObject("/Font")] = font_dict

    font_obj = DictionaryObject()
    font_obj[NameObject("/Type")] = NameObject("/Font")
    font_obj[NameObject("/Subtype")] = NameObject("/Type1")
    font_obj[NameObject("/BaseFont")] = NameObject("/Helvetica")
    font_dict[NameObject("/F1")] = font_obj

    stream_obj = DecodedStreamObject()
    stream_obj.set_data(f"BT /F1 12 Tf 100 100 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream_obj)


def create_test_pdf(text: str = "test content") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    _add_text_to_page(writer, 0, text)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def create_pdf_with_placeholder() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    _add_text_to_page(writer, 0, "page to be removed")
    writer.add_blank_page(width=200, height=200)
    _add_text_to_page(writer, 1, "real content")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestMergePdfs:
    def test_merge_single_pdf(self):
        pdf = create_test_pdf()
        result = merge_pdfs([pdf])
        assert result.startswith(b"%PDF")

    def test_merge_multiple_pdfs(self):
        pdfs = [create_test_pdf() for _ in range(3)]
        result = merge_pdfs(pdfs)
        reader = PdfReader(io.BytesIO(result))
        assert len(reader.pages) == 3

    def test_merge_preserves_page_count(self):
        pdfs = [create_test_pdf() for _ in range(4)]
        result = merge_pdfs(pdfs)
        reader = PdfReader(io.BytesIO(result))
        assert len(reader.pages) == 4

    def test_placeholder_page_removed(self):
        pdf = create_pdf_with_placeholder()
        result = merge_pdfs([pdf])
        reader = PdfReader(io.BytesIO(result))
        assert len(reader.pages) == 1

    def test_placeholder_removed_from_multiple_docs(self):
        pdfs = [create_pdf_with_placeholder() for _ in range(3)]
        result = merge_pdfs(pdfs)
        reader = PdfReader(io.BytesIO(result))
        assert len(reader.pages) == 3

    def test_single_page_pdf_not_stripped(self):
        """A single-page PDF should never be stripped even if text matches."""
        pdf = create_test_pdf("page to be removed")
        result = merge_pdfs([pdf])
        reader = PdfReader(io.BytesIO(result))
        assert len(reader.pages) == 1

    def test_mixed_docs_with_and_without_placeholder(self):
        pdf_with = create_pdf_with_placeholder()
        pdf_without = create_test_pdf("no placeholder here")
        result = merge_pdfs([pdf_with, pdf_without])
        reader = PdfReader(io.BytesIO(result))
        assert len(reader.pages) == 2

    def test_normal_multipage_not_stripped(self):
        """Multi-page PDF without placeholder should keep all pages."""
        writer = PdfWriter()
        for i in range(3):
            writer.add_blank_page(width=200, height=200)
            _add_text_to_page(writer, i, f"normal page {i}")
        buf = io.BytesIO()
        writer.write(buf)
        result = merge_pdfs([buf.getvalue()])
        reader = PdfReader(io.BytesIO(result))
        assert len(reader.pages) == 3


class TestReplaceFirstPageWithCover:
    def test_replaces_first_page(self):
        content_pdf = create_pdf_with_placeholder()  # 2 pages: placeholder + real
        cover_pdf = create_test_pdf("cover page")  # 1 page

        result = replace_first_page_with_cover(content_pdf, cover_pdf)
        reader = PdfReader(io.BytesIO(result))
        # cover page + real content = 2 pages
        assert len(reader.pages) == 2

    def test_cover_uses_only_first_page(self):
        """If cover PDF has multiple pages, only the first is used."""
        content_pdf = create_pdf_with_placeholder()
        # Create multi-page cover
        writer = PdfWriter()
        for i in range(3):
            writer.add_blank_page(width=200, height=200)
            _add_text_to_page(writer, i, f"cover page {i}")
        buf = io.BytesIO()
        writer.write(buf)
        cover_pdf = buf.getvalue()

        result = replace_first_page_with_cover(content_pdf, cover_pdf)
        reader = PdfReader(io.BytesIO(result))
        # 1 cover page + 1 content page = 2 pages
        assert len(reader.pages) == 2


class TestCountPdfPages:
    def test_single_page(self):
        pdf = create_test_pdf()
        assert count_pdf_pages(pdf) == 1

    def test_multi_page(self):
        pdf = create_pdf_with_placeholder()  # 2 pages
        assert count_pdf_pages(pdf) == 2


class TestResolveCoverPagePlaceholders:
    def test_replaces_both_placeholders(self):
        html = "<html>Page {{ PAGE_NUMBER }} of {{ PAGES_TOTAL_COUNT }}</html>"
        result = resolve_cover_page_placeholders(html, 42)
        assert result == "<html>Page 1 of 42</html>"

    def test_handles_flexible_whitespace(self):
        html = "{{PAGE_NUMBER}} / {{  PAGES_TOTAL_COUNT  }}"
        result = resolve_cover_page_placeholders(html, 10)
        assert result == "1 / 10"

    def test_no_placeholders(self):
        html = "<html>No placeholders here</html>"
        result = resolve_cover_page_placeholders(html, 5)
        assert result == html
