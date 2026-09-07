"""Tests for PDF merger."""

import io
import pathlib

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.pdf_merger import count_pdf_pages, merge_pdf_files, replace_first_page_with_cover, resolve_cover_page_placeholders


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


def _write_pdf_file(tmp_path: pathlib.Path, name: str, data: bytes) -> pathlib.Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


class TestMergePdfFiles:
    def test_merge_single_pdf(self, tmp_path):
        p = _write_pdf_file(tmp_path, "0.pdf", create_test_pdf())
        out = tmp_path / "result.pdf"
        merge_pdf_files([p], out)
        assert out.read_bytes().startswith(b"%PDF")

    def test_merge_multiple_pdfs(self, tmp_path):
        paths = [_write_pdf_file(tmp_path, f"{i}.pdf", create_test_pdf()) for i in range(3)]
        out = tmp_path / "result.pdf"
        merge_pdf_files(paths, out)
        reader = PdfReader(out)
        assert len(reader.pages) == 3

    def test_merge_preserves_page_count(self, tmp_path):
        paths = [_write_pdf_file(tmp_path, f"{i}.pdf", create_test_pdf()) for i in range(4)]
        out = tmp_path / "result.pdf"
        merge_pdf_files(paths, out)
        reader = PdfReader(out)
        assert len(reader.pages) == 4

    def test_placeholder_page_removed(self, tmp_path):
        p = _write_pdf_file(tmp_path, "0.pdf", create_pdf_with_placeholder())
        out = tmp_path / "result.pdf"
        merge_pdf_files([p], out)
        reader = PdfReader(out)
        assert len(reader.pages) == 1

    def test_placeholder_removed_from_multiple_docs(self, tmp_path):
        paths = [_write_pdf_file(tmp_path, f"{i}.pdf", create_pdf_with_placeholder()) for i in range(3)]
        out = tmp_path / "result.pdf"
        merge_pdf_files(paths, out)
        reader = PdfReader(out)
        assert len(reader.pages) == 3

    def test_single_page_pdf_not_stripped(self, tmp_path):
        p = _write_pdf_file(tmp_path, "0.pdf", create_test_pdf("page to be removed"))
        out = tmp_path / "result.pdf"
        merge_pdf_files([p], out)
        reader = PdfReader(out)
        assert len(reader.pages) == 1

    def test_mixed_docs_with_and_without_placeholder(self, tmp_path):
        p1 = _write_pdf_file(tmp_path, "0.pdf", create_pdf_with_placeholder())
        p2 = _write_pdf_file(tmp_path, "1.pdf", create_test_pdf("no placeholder here"))
        out = tmp_path / "result.pdf"
        merge_pdf_files([p1, p2], out)
        reader = PdfReader(out)
        assert len(reader.pages) == 2

    def test_normal_multipage_not_stripped(self, tmp_path):
        writer = PdfWriter()
        for i in range(3):
            writer.add_blank_page(width=200, height=200)
            _add_text_to_page(writer, i, f"normal page {i}")
        buf = io.BytesIO()
        writer.write(buf)
        p = _write_pdf_file(tmp_path, "0.pdf", buf.getvalue())
        out = tmp_path / "result.pdf"
        merge_pdf_files([p], out)
        reader = PdfReader(out)
        assert len(reader.pages) == 3


class TestReplaceFirstPageWithCover:
    def test_replaces_first_page(self):
        content_pdf = create_pdf_with_placeholder()
        cover_pdf = create_test_pdf("cover page")

        result = replace_first_page_with_cover(content_pdf, cover_pdf)
        reader = PdfReader(io.BytesIO(result))
        assert len(reader.pages) == 2

    def test_cover_uses_only_first_page(self):
        content_pdf = create_pdf_with_placeholder()
        writer = PdfWriter()
        for i in range(3):
            writer.add_blank_page(width=200, height=200)
            _add_text_to_page(writer, i, f"cover page {i}")
        buf = io.BytesIO()
        writer.write(buf)
        cover_pdf = buf.getvalue()

        result = replace_first_page_with_cover(content_pdf, cover_pdf)
        reader = PdfReader(io.BytesIO(result))
        assert len(reader.pages) == 2

    def test_single_page_content_without_placeholder_not_lost(self):
        content_pdf = create_test_pdf("real content")
        cover_pdf = create_test_pdf("cover page")

        result = replace_first_page_with_cover(content_pdf, cover_pdf)
        reader = PdfReader(io.BytesIO(result))
        assert len(reader.pages) == 2

    def test_multipage_content_without_placeholder_prepends_cover(self):
        writer = PdfWriter()
        for i in range(3):
            writer.add_blank_page(width=200, height=200)
            _add_text_to_page(writer, i, f"content page {i}")
        buf = io.BytesIO()
        writer.write(buf)
        content_pdf = buf.getvalue()
        cover_pdf = create_test_pdf("cover")

        result = replace_first_page_with_cover(content_pdf, cover_pdf)
        reader = PdfReader(io.BytesIO(result))
        assert len(reader.pages) == 4

    def test_empty_cover_pdf_is_rejected_clearly(self):
        content_pdf = create_test_pdf("real content")
        empty_buf = io.BytesIO()
        PdfWriter().write(empty_buf)  # a valid PDF with zero pages
        empty_cover = empty_buf.getvalue()

        with pytest.raises(ValueError, match="Cover page PDF has no pages"):
            replace_first_page_with_cover(content_pdf, empty_cover)


class TestCountPdfPages:
    def test_single_page(self):
        pdf = create_test_pdf()
        assert count_pdf_pages(pdf) == 1

    def test_multi_page(self):
        pdf = create_pdf_with_placeholder()
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

    def test_page_number_always_one(self):
        """PAGE_NUMBER is always 1 — cover page is the first page of its document."""
        html = "Page {{ PAGE_NUMBER }}"
        assert resolve_cover_page_placeholders(html, 100) == "Page 1"

    def test_pages_total_count_is_per_document(self):
        """PAGES_TOTAL_COUNT reflects the individual document page count, not the merged total."""
        html = "{{ PAGES_TOTAL_COUNT }} pages"
        # Document has 5 pages — cover page should say "5 pages" regardless of merge batch size
        assert resolve_cover_page_placeholders(html, 5) == "5 pages"
        # Different document with 12 pages
        assert resolve_cover_page_placeholders(html, 12) == "12 pages"
