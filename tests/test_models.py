"""Tests for Pydantic models."""

from app.models import MergeJobStartParams


class TestMergeJobStartParams:
    def test_defaults(self):
        params = MergeJobStartParams()
        assert params.encoding == "utf-8"
        assert params.media_type == "print"
        assert params.presentational_hints is False
        assert params.base_url is None
        assert params.scale_factor is None
        assert params.file_name == "merged-document.pdf"
        assert params.pdf_variant is None
        assert params.custom_metadata is False
        assert params.full_fonts is False

    def test_camel_case_aliases(self):
        """Verify Java-style camelCase JSON is accepted."""
        params = MergeJobStartParams.model_validate({
            "mediaType": "screen",
            "presentationalHints": True,
            "baseUrl": "http://example.com",
            "scaleFactor": "2",
            "fileName": "output.pdf",
            "pdfVariant": "pdf/a-2b",
            "customMetadata": True,
            "fullFonts": True,
        })
        assert params.media_type == "screen"
        assert params.presentational_hints is True
        assert params.base_url == "http://example.com"
        assert params.file_name == "output.pdf"
        assert params.pdf_variant == "pdf/a-2b"

    def test_snake_case_also_works(self):
        params = MergeJobStartParams.model_validate({
            "media_type": "screen",
            "file_name": "test.pdf",
        })
        assert params.media_type == "screen"
        assert params.file_name == "test.pdf"
