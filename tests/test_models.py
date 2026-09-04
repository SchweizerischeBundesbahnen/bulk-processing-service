"""Tests for Pydantic models."""

from app.models import AddDocumentRequest, DocumentConversionParams, MergeJobStartParams


class TestMergeJobStartParams:
    def test_defaults(self):
        params = MergeJobStartParams()
        assert params.file_name == "merged-document.pdf"
        assert params.pdf_variant is None

    def test_camel_case_aliases(self):
        params = MergeJobStartParams.model_validate({"fileName": "output.pdf", "pdfVariant": "pdf/a-2b"})
        assert params.file_name == "output.pdf"
        assert params.pdf_variant == "pdf/a-2b"


class TestDocumentConversionParams:
    def test_defaults(self):
        params = DocumentConversionParams()
        assert params.encoding == "utf-8"
        assert params.media_type == "print"
        assert params.presentational_hints is False
        assert params.base_url is None
        assert params.scale_factor is None
        assert params.pdf_variant is None
        assert params.custom_metadata is False
        assert params.full_fonts is False

    def test_camel_case_aliases(self):
        params = DocumentConversionParams.model_validate({
            "mediaType": "screen",
            "presentationalHints": True,
            "scaleFactor": "2",
            "pdfVariant": "pdf/a-2b",
            "customMetadata": True,
            "fullFonts": True,
        })
        assert params.media_type == "screen"
        assert params.presentational_hints is True
        assert params.scale_factor == "2"
        assert params.pdf_variant == "pdf/a-2b"
        assert params.custom_metadata is True
        assert params.full_fonts is True


class TestAddDocumentRequest:
    def test_defaults(self):
        req = AddDocumentRequest(html="<html></html>")
        assert req.html == "<html></html>"
        assert req.cover_page_html is None
        assert req.params.encoding == "utf-8"

    def test_with_params(self):
        req = AddDocumentRequest.model_validate({
            "html": "<html></html>",
            "params": {"presentationalHints": True, "scaleFactor": "3"},
        })
        assert req.params.presentational_hints is True
        assert req.params.scale_factor == "3"
