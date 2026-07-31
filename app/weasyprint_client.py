from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.models import DocumentConversionParams


class WeasyPrintClient:
    def __init__(self, base_url: str, timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

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

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/convert/html",
                content=html_content.encode("utf-8"),
                headers={"Content-Type": "text/html", "Accept": "application/pdf"},
                params=query_params,
            )
            response.raise_for_status()
            return response.content
