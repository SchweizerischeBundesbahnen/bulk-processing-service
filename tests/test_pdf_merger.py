"""Tests for PDF merger."""

import io
import pathlib

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, TextStringObject

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


# What WeasyPrint writes into the catalog of a PDF/A or PDF/UA file, which a merge must not lose
CATALOG_KEYS = ("/Metadata", "/OutputIntents", "/Lang", "/MarkInfo")


def create_pdf_with_catalog(text: str = "tagged content", placeholder: bool = False) -> bytes:
    """A document as WeasyPrint writes it for a PDF/A or PDF/UA variant: metadata, output intent, language, tagging and an identifier."""
    writer = PdfWriter()
    texts = ["page to be removed", text] if placeholder else [text]
    for index, page_text in enumerate(texts):
        writer.add_blank_page(width=200, height=200)
        _add_text_to_page(writer, index, page_text)
        writer.pages[index][NameObject("/StructParents")] = NumberObject(index)
    metadata = DecodedStreamObject()
    metadata.set_data(b"<x:xmpmeta xmlns:x='adobe:ns:meta/'/>")
    metadata[NameObject("/Type")] = NameObject("/Metadata")
    metadata[NameObject("/Subtype")] = NameObject("/XML")
    output_intent = DictionaryObject({NameObject("/Type"): NameObject("/OutputIntent"), NameObject("/S"): NameObject("/GTS_PDFA1")})
    writer._root_object[NameObject("/Metadata")] = writer._add_object(metadata)
    writer._root_object[NameObject("/OutputIntents")] = ArrayObject([writer._add_object(output_intent)])
    writer._root_object[NameObject("/Lang")] = TextStringObject("en")
    writer._root_object[NameObject("/MarkInfo")] = DictionaryObject({NameObject("/Marked"): BooleanObject(True)})
    writer.generate_file_identifiers()
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _assert_keeps_the_catalog(pdf: bytes, keys: tuple[str, ...] = CATALOG_KEYS) -> None:
    reader = PdfReader(io.BytesIO(pdf))
    for key in keys:
        assert key in reader.trailer["/Root"], f"The merge keeps {key} of the catalog"
    assert "/ID" in reader.trailer, "The merge keeps the file identifier"


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


class TestMergeKeepsTheCatalog:
    def test_keeps_the_catalog_of_the_first_document(self, tmp_path):
        paths = [_write_pdf_file(tmp_path, f"{index}.pdf", create_pdf_with_catalog(f"document {index}")) for index in range(3)]
        output = tmp_path / "merged.pdf"
        merge_pdf_files(paths, output)
        _assert_keeps_the_catalog(output.read_bytes())
        assert len(PdfReader(output).pages) == 3

    def test_keeps_the_catalog_where_the_first_document_has_a_placeholder(self, tmp_path):
        paths = [_write_pdf_file(tmp_path, "a.pdf", create_pdf_with_catalog("first", placeholder=True)), _write_pdf_file(tmp_path, "b.pdf", create_pdf_with_catalog("second"))]
        output = tmp_path / "merged.pdf"
        merge_pdf_files(paths, output)
        _assert_keeps_the_catalog(output.read_bytes())
        reader = PdfReader(output)
        assert [page.extract_text() for page in reader.pages] == ["first", "second"]

    def test_pages_of_the_other_documents_point_into_no_structure(self, tmp_path):
        paths = [_write_pdf_file(tmp_path, f"{index}.pdf", create_pdf_with_catalog(f"document {index}")) for index in range(2)]
        output = tmp_path / "merged.pdf"
        merge_pdf_files(paths, output)
        pages = PdfReader(output).pages
        assert "/StructParents" in pages[0], "The first document keeps its structure"
        assert "/StructParents" not in pages[1], "A page of another document does not point into the structure of the first"

    def test_drops_the_navigation_of_the_first_document(self, tmp_path):
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(create_pdf_with_catalog("first"))))
        writer.add_outline_item("A heading of the first document", 0)
        writer.add_named_destination("anchor", 0)
        writer.page_mode = "/UseOutlines"
        buf = io.BytesIO()
        writer.write(buf)
        paths = [_write_pdf_file(tmp_path, "a.pdf", buf.getvalue()), _write_pdf_file(tmp_path, "b.pdf", create_pdf_with_catalog("second"))]
        output = tmp_path / "merged.pdf"
        merge_pdf_files(paths, output)
        root = PdfReader(output).trailer["/Root"]
        assert "/Outlines" not in root, "The bookmarks of the first document do not stand for the whole merge"
        assert "/Names" not in root
        assert "/PageMode" not in root
        _assert_keeps_the_catalog(output.read_bytes())

    def test_cover_keeps_the_catalog_of_the_content(self):
        result = replace_first_page_with_cover(create_pdf_with_catalog("content", placeholder=True), create_test_pdf("cover"))
        _assert_keeps_the_catalog(result)
        pages = PdfReader(io.BytesIO(result)).pages
        assert [page.extract_text() for page in pages] == ["cover", "content"]
        assert "/StructParents" not in pages[0], "The cover, rendered on its own, does not point into the structure of the content"


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


TAGGED = pathlib.Path(__file__).parent / "resources" / "tagged"


def _tagged(name: str) -> bytes:
    """A PDF/UA-1 document as WeasyPrint writes it: a Document element, a flat parent tree, links with structure parents."""
    return (TAGGED / f"{name}.pdf").read_bytes()


def _structure(pdf_data: bytes) -> dict:
    """What a check of a tagged document needs of it: the keys into its parent tree, and what its elements point at."""
    import pikepdf

    pdf = pikepdf.open(io.BytesIO(pdf_data))
    root = pdf.Root.StructTreeRoot
    page_ids = [page.obj.objgen for page in pdf.pages]
    page_keys = []
    for page in pdf.pages:
        keys = [int(page.obj.StructParents)] if "/StructParents" in page.obj else []
        keys += [int(annotation.StructParent) for annotation in page.obj.get("/Annots", []) if "/StructParent" in annotation]
        page_keys.append(keys)
    nums = root.ParentTree.Nums
    tree_keys = [int(nums[index]) for index in range(0, len(nums), 2)]
    pointed_at: set = set()
    elements: list = []
    pageless: list = []

    def walk(element: object) -> None:
        if not isinstance(element, pikepdf.Dictionary):
            return
        if "/Pg" in element:
            pointed_at.add(element.Pg.objgen)
        if "/S" in element:
            elements.append(element)
        kids = element.get("/K")
        marked = [kid for kid in (kids if isinstance(kids, pikepdf.Array) else [kids]) if isinstance(kid, int)]
        if marked and "/Pg" not in element:
            pageless.append(element)
        for kid in kids if isinstance(kids, pikepdf.Array) else [kids] if kids is not None else []:
            walk(kid)

    document = root.K[0]
    walk(document)
    return {
        "pdf": pdf,  # kept open, as the elements below belong to it
        "pages": len(page_ids),
        "page_keys": page_keys,
        "tree_keys": tree_keys,
        "next_key": int(root.ParentTreeNextKey) if "/ParentTreeNextKey" in root else None,
        "foreign_pages": pointed_at - set(page_ids),
        "pageless": pageless,
        "pages_reached": [page_id in pointed_at for page_id in page_ids],
        "document_kids": [kid for kid in document.K if isinstance(kid, pikepdf.Dictionary)],
        "text": [page.extract_text() for page in PdfReader(io.BytesIO(pdf_data)).pages],
    }


def _assert_whole(structure: dict) -> None:
    """Every key of a page is in the parent tree, every entry belongs to a page, and the elements point at pages of the document only."""
    keys = [key for keys in structure["page_keys"] for key in keys]
    assert len(keys) == len(set(keys)), "No two pages share a key into the parent tree"
    assert sorted(keys) == sorted(structure["tree_keys"]), "The parent tree holds the keys of the pages and of nothing else"
    assert structure["next_key"] == max(keys) + 1
    assert not structure["foreign_pages"], "No element points at a page which is not in the document"
    assert not structure["pageless"], "No element holds marked content without the page it is on"
    assert all(structure["pages_reached"]), "Every page is reached from the structure"


class TestMergeTheStructure:
    def test_merges_the_structure_of_every_document(self, tmp_path):
        paths = [_write_pdf_file(tmp_path, "rich.pdf", _tagged("rich")), _write_pdf_file(tmp_path, "second.pdf", _tagged("second"))]
        output = tmp_path / "merged.pdf"
        merge_pdf_files(paths, output)
        structure = _structure(output.read_bytes())
        _assert_whole(structure)
        assert structure["pages"] == 3
        rich = _structure(_tagged("rich"))
        assert len(structure["document_kids"]) == len(rich["document_kids"]) + len(_structure(_tagged("second"))["document_kids"])
        # PDF/UA asks for no output intent, which PDF/A does
        _assert_keeps_the_catalog(output.read_bytes(), ("/Metadata", "/Lang", "/MarkInfo", "/StructTreeRoot", "/ViewerPreferences"))

    def test_marks_the_language_of_a_document_which_differs(self, tmp_path):
        paths = [_write_pdf_file(tmp_path, "rich.pdf", _tagged("rich")), _write_pdf_file(tmp_path, "second.pdf", _tagged("second"))]
        output = tmp_path / "merged.pdf"
        merge_pdf_files(paths, output)
        merged = _structure(output.read_bytes())
        kids = merged["document_kids"]
        own = len(_structure(_tagged("rich"))["document_kids"])
        assert all("/Lang" not in kid for kid in kids[:own]), "The elements of the first document keep the language of the merge"
        assert all(str(kid.Lang) == "en" for kid in kids[own:]), "The elements of the English document state their language in a German merge"

    def test_cover_takes_the_place_of_the_placeholder_in_the_structure(self):
        result = replace_first_page_with_cover(_tagged("content"), _tagged("cover"))
        structure = _structure(result)
        _assert_whole(structure)
        assert structure["pages"] == 3
        assert "Cover of the document" in structure["text"][0]
        assert all("page to be removed" not in text for text in structure["text"]), "The placeholder is gone"
        assert structure["document_kids"][0].Pg.objgen == structure["pdf"].pages[0].obj.objgen, "The structure of the cover comes first, as its page does"

    def test_takes_only_the_structure_of_the_first_page_of_a_longer_cover(self):
        result = replace_first_page_with_cover(_tagged("content"), _tagged("long-cover"))
        structure = _structure(result)
        _assert_whole(structure)
        assert structure["pages"] == 3
        one_page_cover = _structure(replace_first_page_with_cover(_tagged("content"), _tagged("cover")))
        assert [str(kid.S) for kid in structure["document_kids"]] == [str(kid.S) for kid in one_page_cover["document_kids"]], "The heading and paragraph of the first page are taken, the paragraph of the second page is left out"

    def test_merges_documents_with_their_covers(self, tmp_path):
        paths = [
            _write_pdf_file(tmp_path, "a.pdf", replace_first_page_with_cover(_tagged("content"), _tagged("cover"))),
            _write_pdf_file(tmp_path, "b.pdf", replace_first_page_with_cover(_tagged("content"), _tagged("cover"))),
        ]
        output = tmp_path / "merged.pdf"
        merge_pdf_files(paths, output)
        structure = _structure(output.read_bytes())
        _assert_whole(structure)
        assert structure["pages"] == 6

    def test_a_tagged_document_added_to_an_untagged_one_loses_its_keys(self, tmp_path):
        paths = [_write_pdf_file(tmp_path, "plain.pdf", create_test_pdf("plain")), _write_pdf_file(tmp_path, "rich.pdf", _tagged("rich"))]
        output = tmp_path / "merged.pdf"
        merge_pdf_files(paths, output)
        reader = PdfReader(output)
        assert "/StructTreeRoot" not in reader.trailer["/Root"]
        for page in reader.pages:
            assert "/StructParents" not in page
            assert all("/StructParent" not in annotation.get_object() for annotation in page.get("/Annots", []))
