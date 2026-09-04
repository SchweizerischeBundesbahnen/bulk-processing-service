from __future__ import annotations

import ssl
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.models import DocumentConversionParams

# Server certificates are verified against the platform trust store, the same
# model the pdf-exporter uses with the JVM truststore: an operator installs the
# WeasyPrint CA into the container's CA store (or points the standard SSL_CERT_FILE
# at it), and the application carries no CA of its own. ssl.create_default_context()
# reads that store, unlike httpx's default which is pinned to the certifi bundle.
TLS_CONTEXT = ssl.create_default_context()


class WeasyPrintClient:
    def __init__(self, base_url: str, timeout: float = 300.0, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # API key carried in the X-API-Key header, or None to send none.
        self.api_key = api_key

    def _request_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "text/html", "Accept": "application/pdf"}
        if self.api_key:
            # A key is a reusable credential, so it is only handed to a transport
            # which protects it; over plain http the request is refused instead.
            if not self.base_url.lower().startswith("https://"):
                msg = "The WeasyPrint API key is not sent over plain http. Set WEASYPRINT_SERVICE_URL to an https address, or clear WEASYPRINT_API_KEY where the service needs no key."
                raise ValueError(msg)
            headers["X-API-Key"] = self.api_key
        return headers

    def convert_html_to_pdf(self, html_content: str, params: DocumentConversionParams) -> bytes:
        query_params: dict[str, str | bool] = {
            "presentational_hints": params.presentational_hints,
            "custom_metadata": params.custom_metadata,
            "full_fonts": params.full_fonts,
        }
        if params.pdf_variant:
            query_params["pdf_variant"] = params.pdf_variant
        if params.scale_factor:
            query_params["scale_factor"] = params.scale_factor

        with httpx.Client(timeout=self.timeout, verify=TLS_CONTEXT) as client:
            response = client.post(
                f"{self.base_url}/convert/html",
                content=html_content.encode("utf-8"),
                headers=self._request_headers(),
                params=query_params,
            )
            response.raise_for_status()
            return response.content
