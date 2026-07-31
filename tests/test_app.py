"""Tests for bulk processing service API endpoints."""

import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.app import app
from app.job_manager import JobManager
from app.models import JobStatus


@pytest.fixture
def client(tmp_path):
    app.state.job_manager = JobManager(tmp_path / "jobs")
    return TestClient(app, raise_server_exceptions=True)


def _job_manager() -> JobManager:
    return app.state.job_manager


def _start_job(client, **kwargs):
    response = client.post("/api/convert/start", json=kwargs)
    assert response.status_code == 201
    return response.json()["jobId"]


def _make_sample_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


SAMPLE_PDF = _make_sample_pdf()


class TestHealth:
    def test_health_healthy(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["storage"] == "writable"
        assert "weasyprint" not in data

    @patch("app.app._check_storage_writable", return_value="unwritable")
    def test_health_unhealthy_storage(self, _mock_storage, client):
        response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["storage"] == "unwritable"


class TestReady:
    @patch("app.app._check_weasyprint_reachable", return_value="available")
    def test_ready_when_dependencies_available(self, _mock_wp, client):
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["storage"] == "writable"
        assert data["weasyprint"] == "available"

    @patch("app.app._check_weasyprint_reachable", return_value="unavailable")
    def test_not_ready_when_weasyprint_unavailable(self, _mock_wp, client):
        response = client.get("/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not ready"
        assert data["weasyprint"] == "unavailable"


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
        data = response.json()
        assert "jobId" in data
        assert isinstance(data["jobId"], str)
        assert len(data["jobId"]) == 32

    def test_start_job_with_defaults(self, client):
        job_id = _start_job(client)
        metadata = _job_manager().get_job_metadata(job_id)
        assert metadata is not None
        assert metadata.params.file_name == "merged-document.pdf"
        assert metadata.status == JobStatus.ACTIVE

    def test_start_job_with_params(self, client):
        job_id = _start_job(client, fileName="output.pdf", pdfVariant="pdf/a-2b")
        metadata = _job_manager().get_job_metadata(job_id)
        assert metadata.params.file_name == "output.pdf"
        assert metadata.params.pdf_variant == "pdf/a-2b"


class TestAddDocumentToJob:
    @patch("app.converter_controller.get_weasyprint_client")
    def test_add_document_without_cover(self, mock_get_client, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.return_value = SAMPLE_PDF

        job_id = _start_job(client)

        response = client.post(f"/api/convert/{job_id}/add", json={"html": "<html><body>Hello</body></html>"})
        assert response.status_code == 202
        assert response.json() == {"status": "accepted"}

        metadata = _job_manager().get_job_metadata(job_id)
        assert metadata.pdf_count == 1
        mock_client.convert_html_to_pdf.assert_called_once()

    @patch("app.converter_controller.replace_first_page_with_cover")
    @patch("app.converter_controller.count_pdf_pages", return_value=5)
    @patch("app.converter_controller.get_weasyprint_client")
    def test_add_document_with_cover(self, mock_get_client, mock_count_pages, mock_replace, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.side_effect = [b"content_pdf", b"cover_pdf"]
        mock_replace.return_value = b"merged_with_cover"

        job_id = _start_job(client)

        response = client.post(
            f"/api/convert/{job_id}/add",
            json={"html": "<html>content</html>", "coverPageHtml": "<html>{{ PAGE_NUMBER }} of {{ PAGES_TOTAL_COUNT }}</html>"},
        )
        assert response.status_code == 202
        assert mock_client.convert_html_to_pdf.call_count == 2
        mock_count_pages.assert_called_once_with(b"content_pdf")
        mock_replace.assert_called_once_with(b"content_pdf", b"cover_pdf")

        metadata = _job_manager().get_job_metadata(job_id)
        assert metadata.pdf_count == 1

    @patch("app.converter_controller.get_weasyprint_client")
    def test_add_document_uses_per_doc_params(self, mock_get_client, client):
        """T1: per-document params (scaleFactor, customMetadata) are forwarded to WeasyPrint, not job-level params."""
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.return_value = SAMPLE_PDF

        job_id = _start_job(client)

        response = client.post(f"/api/convert/{job_id}/add", json={
            "html": "<html></html>",
            "params": {"scaleFactor": "3", "customMetadata": True, "pdfVariant": "pdf/a-1b"},
        })
        assert response.status_code == 202

        # Verify WeasyPrint was called with per-doc params, not defaults
        call_args = mock_client.convert_html_to_pdf.call_args
        doc_params = call_args.args[1]
        assert doc_params.scale_factor == "3"
        assert doc_params.custom_metadata is True
        assert doc_params.pdf_variant == "pdf/a-1b"

    def test_add_document_job_not_found(self, client):
        response = client.post("/api/convert/00000000000000000000000000000000/add", json={"html": "<html></html>"})
        assert response.status_code == 404

    def test_add_document_malformed_job_id(self, client):
        response = client.post("/api/convert/../../etc/passwd/add", json={"html": "<html></html>"})
        assert response.status_code == 404

    @patch("app.converter_controller.get_weasyprint_client")
    def test_add_document_weasyprint_failure_records_failure(self, mock_get_client, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.side_effect = RuntimeError("Connection refused")

        job_id = _start_job(client)

        response = client.post(f"/api/convert/{job_id}/add", json={"html": "<html></html>"})
        assert response.status_code == 502

        metadata = _job_manager().get_job_metadata(job_id)
        assert metadata.failed_count == 1
        assert metadata.pdf_count == 0

    @patch("app.converter_controller.get_weasyprint_client")
    def test_add_document_to_completed_job_returns_409(self, mock_get_client, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.return_value = SAMPLE_PDF

        job_id = _start_job(client)
        client.post(f"/api/convert/{job_id}/add", json={"html": "<html></html>"})
        client.post(f"/api/convert/{job_id}/finish")

        response = client.post(f"/api/convert/{job_id}/add", json={"html": "<html>more</html>"})
        assert response.status_code == 409
        assert "not active" in response.json()["detail"]


class TestFinishMergeJob:
    @patch("app.converter_controller.get_weasyprint_client")
    def test_finish_job_returns_merged_pdf(self, mock_get_client, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.return_value = SAMPLE_PDF

        job_id = _start_job(client, fileName="result.pdf")

        for _ in range(2):
            client.post(f"/api/convert/{job_id}/add", json={"html": "<html></html>"})

        response = client.post(f"/api/convert/{job_id}/finish")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "filename=\"result.pdf\"" in response.headers["content-disposition"]
        assert "filename*=UTF-8''result.pdf" in response.headers["content-disposition"]
        assert response.headers["x-documents-merged"] == "2"
        assert "x-documents-failed" not in response.headers

    def test_finish_job_not_found(self, client):
        response = client.post("/api/convert/00000000000000000000000000000000/finish")
        assert response.status_code == 404

    def test_finish_job_no_documents(self, client):
        job_id = _start_job(client)
        response = client.post(f"/api/convert/{job_id}/finish")
        assert response.status_code == 400

    @patch("app.converter_controller.get_weasyprint_client")
    def test_finish_job_all_failed(self, mock_get_client, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.side_effect = RuntimeError("fail")

        job_id = _start_job(client)
        client.post(f"/api/convert/{job_id}/add", json={"html": "<html></html>"})
        client.post(f"/api/convert/{job_id}/add", json={"html": "<html></html>"})

        response = client.post(f"/api/convert/{job_id}/finish")
        assert response.status_code == 400
        assert "2 documents failed" in response.json()["detail"]

    @patch("app.converter_controller.get_weasyprint_client")
    def test_finish_job_partial_merge_reports_failures(self, mock_get_client, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.side_effect = [SAMPLE_PDF, RuntimeError("fail"), SAMPLE_PDF]

        job_id = _start_job(client)
        client.post(f"/api/convert/{job_id}/add", json={"html": "<html>ok1</html>"})
        client.post(f"/api/convert/{job_id}/add", json={"html": "<html>fail</html>"})
        client.post(f"/api/convert/{job_id}/add", json={"html": "<html>ok2</html>"})

        response = client.post(f"/api/convert/{job_id}/finish")
        assert response.status_code == 200
        assert response.headers["x-documents-merged"] == "2"
        assert response.headers["x-documents-failed"] == "1"

    @patch("app.converter_controller.get_weasyprint_client")
    def test_finish_job_keeps_data(self, mock_get_client, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.return_value = SAMPLE_PDF

        job_id = _start_job(client)
        client.post(f"/api/convert/{job_id}/add", json={"html": "<html></html>"})
        client.post(f"/api/convert/{job_id}/finish")

        metadata = _job_manager().get_job_metadata(job_id)
        assert metadata is not None
        assert metadata.status == JobStatus.COMPLETED
        assert _job_manager().get_result_path(job_id) is not None


class TestDeleteMergeJob:
    def test_delete_active_job(self, client):
        job_id = _start_job(client)

        response = client.delete(f"/api/convert/{job_id}")
        assert response.status_code == 204

        assert _job_manager().get_job_metadata(job_id) is None

    def test_delete_not_found(self, client):
        response = client.delete("/api/convert/00000000000000000000000000000000")
        assert response.status_code == 404

    def test_delete_malformed_job_id(self, client):
        response = client.delete("/api/convert/../../../etc")
        assert response.status_code == 404


class TestRaceConditions:
    """T3: race between /finish and /add on same job."""

    @patch("app.converter_controller.get_weasyprint_client")
    def test_add_after_finish_returns_409(self, mock_get_client, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.return_value = SAMPLE_PDF

        job_id = _start_job(client)
        client.post(f"/api/convert/{job_id}/add", json={"html": "<html>doc1</html>"})
        response = client.post(f"/api/convert/{job_id}/finish")
        assert response.status_code == 200

        # Add after finish — job is COMPLETED, should reject
        response = client.post(f"/api/convert/{job_id}/add", json={"html": "<html>late</html>"})
        assert response.status_code == 409

    @patch("app.converter_controller.get_weasyprint_client")
    def test_double_finish_returns_same_pdf(self, mock_get_client, client):
        mock_client = mock_get_client.return_value
        mock_client.convert_html_to_pdf.return_value = SAMPLE_PDF

        job_id = _start_job(client)
        client.post(f"/api/convert/{job_id}/add", json={"html": "<html></html>"})

        response1 = client.post(f"/api/convert/{job_id}/finish")
        response2 = client.post(f"/api/convert/{job_id}/finish")
        assert response1.status_code == 200
        assert response2.status_code == 200
        assert response1.content == response2.content


class TestSSRFAndVersionContract:
    """T4: SSRF rejection + version API contract."""

    def test_unknown_fields_in_start_ignored(self, client):
        """weasyPrintServiceUrl or any extra field must not be accepted."""
        response = client.post("/api/convert/start", json={
            "fileName": "test.pdf",
            "weasyPrintServiceUrl": "http://evil.attacker.com:9080",
            "extraField": "should be ignored",
        })
        assert response.status_code == 201
        job_id = response.json()["jobId"]
        metadata = _job_manager().get_job_metadata(job_id)
        assert not hasattr(metadata.params, "weasy_print_service_url")
        assert metadata.params.file_name == "test.pdf"

    def test_version_returns_api_version(self, client):
        """Version endpoint must include apiVersion for compatibility checks."""
        response = client.get("/version")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["apiVersion"], int)
        assert data["apiVersion"] >= 1
