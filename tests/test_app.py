"""Tests for bulk processing service API endpoints."""

import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

import app.app as app_module
from app.app import app
from app.converter_controller import init_job_manager
from app.job_manager import JobManager
from app.models import JobStatus


@pytest.fixture(autouse=True)
def _use_tmp_storage(tmp_path):
    """Point job_manager at a temp directory for each test."""
    original = app_module.job_manager
    tmp_manager = JobManager(tmp_path / "jobs")
    app_module.job_manager = tmp_manager
    init_job_manager(tmp_manager)
    yield
    app_module.job_manager = original
    init_job_manager(original)


@pytest.fixture
def client():
    return TestClient(app)


def _make_sample_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


SAMPLE_PDF = _make_sample_pdf()


class TestHealth:
    def test_health_endpoint(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestVersion:
    def test_version_endpoint(self, client):
        response = client.get("/version")
        assert response.status_code == 200
        data = response.json()
        assert data["apiVersion"] == 1
        assert "python" in data
        assert "bulkProcessingService" in data
        assert "timestamp" in data


class TestStartMergeJob:
    def test_start_job_returns_job_id(self, client):
        response = client.post("/api/convert/start", json={"fileName": "test.pdf"})
        assert response.status_code == 201
        job_id = response.json()
        assert isinstance(job_id, str)
        assert len(job_id) == 32

    def test_start_job_with_defaults(self, client):
        response = client.post("/api/convert/start", json={})
        assert response.status_code == 201
        metadata = app_module.job_manager.get_job_metadata(response.json())
        assert metadata is not None
        assert metadata.params.encoding == "utf-8"
        assert metadata.params.media_type == "print"
        assert metadata.params.file_name == "merged-document.pdf"
        assert metadata.status == JobStatus.ACTIVE

    def test_start_job_with_all_params(self, client):
        params = {
            "encoding": "utf-16",
            "mediaType": "screen",
            "presentationalHints": True,
            "baseUrl": "http://example.com",
            "scaleFactor": "2",
            "fileName": "output.pdf",
            "pdfVariant": "pdf/a-2b",
            "customMetadata": True,
            "fullFonts": True,
        }
        response = client.post("/api/convert/start", json=params)
        assert response.status_code == 201
        metadata = app_module.job_manager.get_job_metadata(response.json())
        assert metadata.params.encoding == "utf-16"
        assert metadata.params.media_type == "screen"
        assert metadata.params.presentational_hints is True
        assert metadata.params.base_url == "http://example.com"
        assert metadata.params.scale_factor == "2"
        assert metadata.params.file_name == "output.pdf"
        assert metadata.params.pdf_variant == "pdf/a-2b"
        assert metadata.params.custom_metadata is True
        assert metadata.params.full_fonts is True


class TestAddDocumentToJob:
    @patch("app.converter_controller.get_weasyprint_client")
    def test_add_document_without_cover(self, mock_get_client, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.return_value = SAMPLE_PDF

        response = client.post("/api/convert/start", json={})
        job_id = response.json()

        response = client.post(f"/api/convert/{job_id}/add", json={"html": "<html><body>Hello</body></html>"})
        assert response.status_code == 202
        assert response.json() == {"status": "accepted"}

        metadata = app_module.job_manager.get_job_metadata(job_id)
        assert metadata.pdf_count == 1
        mock_client.convert_html_to_pdf.assert_called_once()

    @patch("app.converter_controller.replace_first_page_with_cover")
    @patch("app.converter_controller.count_pdf_pages", return_value=5)
    @patch("app.converter_controller.get_weasyprint_client")
    def test_add_document_with_cover(self, mock_get_client, mock_count_pages, mock_replace, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.side_effect = [b"content_pdf", b"cover_pdf"]
        mock_replace.return_value = b"merged_with_cover"

        response = client.post("/api/convert/start", json={})
        job_id = response.json()

        response = client.post(
            f"/api/convert/{job_id}/add",
            json={"html": "<html>content</html>", "coverPageHtml": "<html>{{ PAGE_NUMBER }} of {{ PAGES_TOTAL_COUNT }}</html>"},
        )
        assert response.status_code == 202
        assert mock_client.convert_html_to_pdf.call_count == 2
        mock_count_pages.assert_called_once_with(b"content_pdf")
        mock_replace.assert_called_once_with(b"content_pdf", b"cover_pdf")

        metadata = app_module.job_manager.get_job_metadata(job_id)
        assert metadata.pdf_count == 1

    def test_add_document_job_not_found(self, client):
        response = client.post("/api/convert/00000000000000000000000000000000/add", json={"html": "<html></html>"})
        assert response.status_code == 404

    @patch("app.converter_controller.get_weasyprint_client")
    def test_add_document_weasyprint_failure(self, mock_get_client, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.side_effect = RuntimeError("Connection refused")

        response = client.post("/api/convert/start", json={})
        job_id = response.json()

        response = client.post(f"/api/convert/{job_id}/add", json={"html": "<html></html>"})
        assert response.status_code == 502


class TestFinishMergeJob:
    @patch("app.converter_controller.get_weasyprint_client")
    def test_finish_job_returns_merged_pdf(self, mock_get_client, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.return_value = SAMPLE_PDF

        response = client.post("/api/convert/start", json={"fileName": "result.pdf"})
        job_id = response.json()

        for _ in range(2):
            client.post(f"/api/convert/{job_id}/add", json={"html": "<html></html>"})

        response = client.post(f"/api/convert/{job_id}/finish")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"] == 'attachment; filename="result.pdf"'

    def test_finish_job_not_found(self, client):
        response = client.post("/api/convert/00000000000000000000000000000000/finish")
        assert response.status_code == 404

    def test_finish_job_no_documents(self, client):
        response = client.post("/api/convert/start", json={})
        job_id = response.json()

        response = client.post(f"/api/convert/{job_id}/finish")
        assert response.status_code == 400

    @patch("app.converter_controller.get_weasyprint_client")
    def test_finish_job_keeps_data(self, mock_get_client, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.return_value = SAMPLE_PDF

        response = client.post("/api/convert/start", json={})
        job_id = response.json()
        client.post(f"/api/convert/{job_id}/add", json={"html": "<html></html>"})
        client.post(f"/api/convert/{job_id}/finish")

        metadata = app_module.job_manager.get_job_metadata(job_id)
        assert metadata is not None
        assert metadata.status == JobStatus.COMPLETED
        assert app_module.job_manager.get_result_path(job_id) is not None


class TestDeleteMergeJob:
    def test_delete_active_job(self, client):
        response = client.post("/api/convert/start", json={})
        job_id = response.json()

        response = client.delete(f"/api/convert/{job_id}")
        assert response.status_code == 204

        assert app_module.job_manager.get_job_metadata(job_id) is None

    def test_delete_not_found(self, client):
        response = client.delete("/api/convert/00000000000000000000000000000000")
        assert response.status_code == 404
