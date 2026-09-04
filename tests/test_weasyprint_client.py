"""Tests for WeasyPrint client."""

import ssl
from unittest.mock import patch, MagicMock

import httpx
import pytest

from app.models import DocumentConversionParams
from app.weasyprint_client import WeasyPrintClient


@pytest.fixture
def client():
    return WeasyPrintClient(base_url="http://weasyprint:9080")


@pytest.fixture
def default_params():
    return DocumentConversionParams()


class TestWeasyPrintClient:
    @patch("app.weasyprint_client.httpx.Client")
    def test_convert_html_to_pdf_success(self, mock_client_cls, client, default_params):
        mock_response = MagicMock()
        mock_response.content = b"%PDF-1.4 test"
        mock_response.raise_for_status = MagicMock()

        mock_http_client = MagicMock()
        mock_http_client.post.return_value = mock_response
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_http_client

        result = client.convert_html_to_pdf("<html></html>", default_params)
        assert result == b"%PDF-1.4 test"

        mock_http_client.post.assert_called_once()
        call_kwargs = mock_http_client.post.call_args
        assert "/convert/html" in call_kwargs.args[0]

    @patch("app.weasyprint_client.httpx.Client")
    def test_convert_passes_query_params(self, mock_client_cls, client):
        mock_response = MagicMock()
        mock_response.content = b"%PDF"
        mock_response.raise_for_status = MagicMock()

        mock_http_client = MagicMock()
        mock_http_client.post.return_value = mock_response
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_http_client

        params = DocumentConversionParams(pdf_variant="pdf/a-2b", scale_factor="2", presentational_hints=True)
        client.convert_html_to_pdf("<html></html>", params)

        call_kwargs = mock_http_client.post.call_args
        query_params = call_kwargs.kwargs["params"]
        assert query_params["pdf_variant"] == "pdf/a-2b"
        assert query_params["scale_factor"] == "2"
        assert query_params["presentational_hints"] is True

    @patch("app.weasyprint_client.httpx.Client")
    def test_convert_raises_on_http_error(self, mock_client_cls, client, default_params):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError("Server Error", request=MagicMock(), response=MagicMock())

        mock_http_client = MagicMock()
        mock_http_client.post.return_value = mock_response
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_http_client

        with pytest.raises(httpx.HTTPStatusError):
            client.convert_html_to_pdf("<html></html>", default_params)

    def test_trailing_slash_stripped(self):
        client = WeasyPrintClient(base_url="http://weasyprint:9080/")
        assert client.base_url == "http://weasyprint:9080"

    @patch("app.weasyprint_client.httpx.Client")
    def test_verifies_against_platform_trust_store(self, mock_client_cls, default_params):
        mock_response = MagicMock()
        mock_response.content = b"%PDF"
        mock_response.raise_for_status = MagicMock()

        mock_http_client = MagicMock()
        mock_http_client.post.return_value = mock_response
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_http_client

        client = WeasyPrintClient(base_url="https://weasyprint:9080")
        client.convert_html_to_pdf("<html></html>", default_params)

        assert isinstance(mock_client_cls.call_args.kwargs["verify"], ssl.SSLContext)

    def test_api_key_defaults_to_none(self):
        assert WeasyPrintClient(base_url="https://weasyprint:9080").api_key is None

    @patch("app.weasyprint_client.httpx.Client")
    def test_api_key_sent_over_https(self, mock_client_cls, default_params):
        mock_response = MagicMock()
        mock_response.content = b"%PDF"
        mock_response.raise_for_status = MagicMock()

        mock_http_client = MagicMock()
        mock_http_client.post.return_value = mock_response
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_http_client

        client = WeasyPrintClient(base_url="https://weasyprint:9080", api_key="secret")
        client.convert_html_to_pdf("<html></html>", default_params)

        assert mock_http_client.post.call_args.kwargs["headers"]["X-API-Key"] == "secret"

    def test_api_key_refused_over_plain_http(self, default_params):
        client = WeasyPrintClient(base_url="http://weasyprint:9080", api_key="secret")
        with pytest.raises(ValueError, match="not sent over plain http"):
            client.convert_html_to_pdf("<html></html>", default_params)

    @patch("app.weasyprint_client.httpx.Client")
    def test_no_api_key_header_when_unset(self, mock_client_cls, default_params):
        mock_response = MagicMock()
        mock_response.content = b"%PDF"
        mock_response.raise_for_status = MagicMock()

        mock_http_client = MagicMock()
        mock_http_client.post.return_value = mock_response
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_http_client

        client = WeasyPrintClient(base_url="http://weasyprint:9080")
        client.convert_html_to_pdf("<html></html>", default_params)

        assert "X-API-Key" not in mock_http_client.post.call_args.kwargs["headers"]
