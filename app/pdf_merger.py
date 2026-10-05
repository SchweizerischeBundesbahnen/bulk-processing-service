from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

import pikepdf
from pypdf import PdfReader

if TYPE_CHECKING:
    import pathlib

# Marker text injected by pdf-exporter (PdfConverter.java) into the first page of documents
# that have a cover page. This page is a throwaway placeholder that gets replaced by the
# rendered cover page PDF. Detection is text-based: if the first page of a multi-page PDF
# contains this string (case-insensitive), it is treated as a placeholder.
PLACEHOLDER_MARKER = "page to be removed"

# The key of a page, and of a form, into the parent tree of the structure: the elements its marked content belongs to
STRUCT_PARENTS = "/StructParents"

# The key of an annotation, as a link, into the parent tree of the structure: the element it belongs to
STRUCT_PARENT = "/StructParent"

# The navigation of a document: its bookmarks and named destinations, and whether a viewer opens with the bookmarks shown
NAVIGATION = ("/Outlines", "/Names")

# The root of the structure of a document, which only a tagged document has
STRUCT_TREE_ROOT = "/StructTreeRoot"

# The language of a document, or of an element of its structure
LANG = "/Lang"


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


def _starts_with_placeholder(pdf_data: bytes) -> bool:
    """Whether the first of several pages is the placeholder; a document of one page keeps it."""
    reader = PdfReader(io.BytesIO(pdf_data))
    if len(reader.pages) <= 1:
        return False
    text = reader.pages[0].extract_text() or ""
    return PLACEHOLDER_MARKER in text.lower()


def _list(value: pikepdf.Object | None) -> list[pikepdf.Object]:
    """An array as a list, a single object as a list of one, nothing as an empty list."""
    if value is None:
        return []
    if isinstance(value, pikepdf.Array):
        return [value[index] for index in range(len(value))]
    return [value]


def _number_tree(tree: pikepdf.Object) -> dict[int, pikepdf.Object]:
    """The entries of a number tree, as the parent tree of a structure is, flat or split into kids."""
    entries: dict[int, pikepdf.Object] = {}
    if "/Nums" in tree:
        nums = _list(tree.Nums)
        for index in range(0, len(nums), 2):
            entries[int(nums[index])] = nums[index + 1]
    for kid in _list(tree.get("/Kids")):
        entries.update(_number_tree(kid))
    return entries


def _write_number_tree(pdf: pikepdf.Pdf, root: pikepdf.Object, entries: dict[int, pikepdf.Object]) -> None:
    nums = pikepdf.Array()
    for key in sorted(entries):
        nums.append(key)
        nums.append(entries[key])
    root.ParentTree = pdf.make_indirect(pikepdf.Dictionary(Nums=nums))
    root.ParentTreeNextKey = max(entries, default=-1) + 1


def _annotations(page: pikepdf.Object) -> list[pikepdf.Object]:
    return [annotation for annotation in _list(page.get("/Annots")) if STRUCT_PARENT in annotation]


def _keys_of(page: pikepdf.Object) -> list[int]:
    """The keys a page holds into the parent tree: its own, and those of its annotations."""
    keys = [int(page[STRUCT_PARENTS])] if STRUCT_PARENTS in page else []
    return keys + [int(annotation[STRUCT_PARENT]) for annotation in _annotations(page)]


def _shift_keys(page: pikepdf.Object, offset: int) -> None:
    if STRUCT_PARENTS in page:
        page[STRUCT_PARENTS] = int(page[STRUCT_PARENTS]) + offset
    for annotation in _annotations(page):
        annotation[STRUCT_PARENT] = int(annotation[STRUCT_PARENT]) + offset


def _detach(page: pikepdf.Object) -> None:
    """Remove the keys of a page into a parent tree, for a page added to a document without a structure."""
    if STRUCT_PARENTS in page:
        del page[STRUCT_PARENTS]
    for annotation in _annotations(page):
        del annotation[STRUCT_PARENT]


def _document_element(root: pikepdf.Object) -> pikepdf.Object:
    """The element under the root of the structure, the Document of a structure WeasyPrint writes."""
    return _list(root.K)[0]


def _prune(element: pikepdf.Object, pages: set[tuple[int, int]]) -> bool:
    """Keep only what of an element is on the given pages; whether anything of it is left."""
    page = element.get("/Pg")
    on_a_kept_page = page is None or page.objgen in pages
    kept = pikepdf.Array()
    for kid in _list(element.get("/K")):
        if isinstance(kid, pikepdf.Dictionary):
            if "/S" in kid and not _prune(kid, pages):
                continue
            if "/S" not in kid and "/Pg" in kid and kid.Pg.objgen not in pages:
                continue
        elif not on_a_kept_page:
            continue
        kept.append(kid)
    element.K = kept
    return len(kept) > 0


def _remove_first_page(pdf: pikepdf.Pdf) -> None:
    """Remove the placeholder page, with what the structure holds of it."""
    page = pdf.pages[0].obj
    root = pdf.Root.get(STRUCT_TREE_ROOT)
    if root is not None:
        entries = _number_tree(root.ParentTree)
        for key in _keys_of(page):
            entries.pop(key, None)
        _write_number_tree(pdf, root, entries)
        _prune(_document_element(root), {kept.obj.objgen for kept in pdf.pages[1:]})
    del pdf.pages[0]


def _copy_pages(target: pikepdf.Pdf, pages: list[pikepdf.Page], *, at_start: bool) -> list[pikepdf.Object]:
    """Copy pages into the target, at its start or end, and return the copies."""
    copies: list[pikepdf.Object] = []
    for index, page in enumerate(pages):
        if at_start:
            target.pages.insert(index, page)
            copies.append(target.pages[index].obj)
        else:
            target.pages.append(page)
            copies.append(target.pages[-1].obj)
    return copies


def _copy_entries(target: pikepdf.Pdf, entries: dict[int, pikepdf.Object], source_entries: dict[int, pikepdf.Object], keys: list[int], offset: int) -> None:
    """Take over the entries of the parent tree of the source for the given keys, shifted by the offset."""
    for key in keys:
        value = source_entries.get(key)
        if value is None:
            continue
        if isinstance(value, pikepdf.Array) and not value.is_indirect:
            entries[key + offset] = pikepdf.Array([target.copy_foreign(element) for element in _list(value)])
        else:
            entries[key + offset] = target.copy_foreign(value)


def _copy_elements(target: pikepdf.Pdf, source: pikepdf.Pdf, *, at_start: bool) -> None:
    """Add the elements of the source, pruned to the copied pages, to the Document of the target, at its start or end."""
    target_root = target.Root.StructTreeRoot
    source_root = source.Root.StructTreeRoot
    document = _document_element(target_root)
    copied = target.copy_foreign(_document_element(source_root))
    language = str(source.Root.Lang) if LANG in source.Root else None
    target_language = str(target.Root.Lang) if LANG in target.Root else None
    moved = _list(copied.get("/K"))
    for kid in moved:
        if isinstance(kid, pikepdf.Dictionary) and "/S" in kid:
            kid.P = document
            if language is not None and language != target_language and LANG not in kid:
                kid.Lang = pikepdf.String(language)
    own = _list(document.get("/K"))
    document.K = pikepdf.Array(moved + own if at_start else own + moved)
    _merge_maps(target_root, source_root)


def _add(target: pikepdf.Pdf, source: pikepdf.Pdf, pages: list[pikepdf.Page], *, at_start: bool = False) -> None:
    """Add pages of another document to the target, with their part of its structure.

    The keys of the pages into the parent tree are shifted past those of the target, the parent tree takes their entries,
    and the elements of their content are added to the Document of the target, at its start or end. A page added to a
    target without a structure loses its keys, which would point into nothing.
    """
    target_root = target.Root.get(STRUCT_TREE_ROOT)
    source_root = source.Root.get(STRUCT_TREE_ROOT)
    copies = _copy_pages(target, pages, at_start=at_start)
    if target_root is None or source_root is None:
        for copy in copies:
            _detach(copy)
        return
    # Pruned before any of it is copied: qpdf copies a reference to a page it did not copy as null, which reads as no page
    _prune(_document_element(source_root), {page.obj.objgen for page in pages})
    entries = _number_tree(target_root.ParentTree)
    offset = max(entries, default=-1) + 1
    for copy in copies:
        _shift_keys(copy, offset)
    # The pages are copied first, so that the structure finds them already copied and points at the copies
    _copy_entries(target, entries, _number_tree(source_root.ParentTree), [key for page in pages for key in _keys_of(page.obj)], offset)
    _write_number_tree(target, target_root, entries)
    _copy_elements(target, source, at_start=at_start)


def _merge_maps(target_root: pikepdf.Object, source_root: pikepdf.Object) -> None:
    """Take over the role and class maps of the source, where the target has none of the same name."""
    for key in ("/RoleMap", "/ClassMap"):
        if key not in source_root:
            continue
        if key not in target_root:
            target_root[key] = pikepdf.Dictionary()
        merged = target_root[key]
        for name, value in source_root[key].items():
            if name not in merged:
                merged[name] = value


def _drop_navigation(pdf: pikepdf.Pdf) -> None:
    """Remove the bookmarks and named destinations a merge took over from its first document, which describe that document alone."""
    for key in NAVIGATION:
        if key in pdf.Root:
            del pdf.Root[key]
    if pdf.Root.get("/PageMode") == pikepdf.Name.UseOutlines:
        del pdf.Root["/PageMode"]


def _save(pdf: pikepdf.Pdf) -> bytes:
    output = io.BytesIO()
    pdf.save(output)
    return output.getvalue()


def replace_first_page_with_cover(content_pdf: bytes, cover_pdf: bytes) -> bytes:
    """Put the first page of the cover in place of the placeholder page of the content.

    The result is built on the content, so it keeps what WeasyPrint wrote into its catalog for the PDF variant asked for:
    the metadata, the output intent, the language, and the file identifier. A tagged variant keeps the structure of the
    content and takes that of the cover before it, as the cover is the first page.
    """
    cover = pikepdf.open(io.BytesIO(cover_pdf))
    if len(cover.pages) == 0:
        msg = "Cover page PDF has no pages"
        raise ValueError(msg)
    content = pikepdf.open(io.BytesIO(content_pdf))
    if _starts_with_placeholder(content_pdf):
        _remove_first_page(content)
    _add(content, cover, [cover.pages[0]], at_start=True)
    return _save(content)


def merge_pdf_files(pdf_paths: list[pathlib.Path], output_path: pathlib.Path) -> None:
    """Merge the documents into one, in their order.

    The result is built on the first document, so it keeps what WeasyPrint wrote into its catalog for the PDF variant
    asked for: the metadata, the output intent, the language and the file identifier, which a PDF/A or PDF/UA file needs.
    The other documents are added with their structure, which a tagged variant needs. The navigation of the first document
    is dropped, as its bookmarks would stand for the whole merge.
    """
    merged: pikepdf.Pdf | None = None
    for pdf_path in pdf_paths:
        data = pdf_path.read_bytes()
        pdf = pikepdf.open(io.BytesIO(data))
        if _starts_with_placeholder(data):
            _remove_first_page(pdf)
        if merged is None:
            merged = pdf
            _drop_navigation(merged)
        else:
            _add(merged, pdf, list(pdf.pages))
    output_path.write_bytes(_save(merged if merged is not None else pikepdf.new()))
