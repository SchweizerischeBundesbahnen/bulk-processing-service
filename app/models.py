from datetime import datetime  # noqa: TC003
from enum import StrEnum

from pydantic import BaseModel, Field


def _to_camel_case(field_name: str) -> str:
    return "".join(word if i == 0 else word.capitalize() for i, word in enumerate(field_name.split("_")))


class MergeJobStartParams(BaseModel):
    file_name: str = "merged-document.pdf"
    pdf_variant: str | None = None

    model_config = {"alias_generator": _to_camel_case, "populate_by_name": True}


class DocumentConversionParams(BaseModel):
    encoding: str = "utf-8"
    media_type: str = "print"
    presentational_hints: bool = False
    base_url: str | None = None
    scale_factor: str | None = None
    pdf_variant: str | None = None
    custom_metadata: bool = False
    full_fonts: bool = False

    model_config = {"alias_generator": _to_camel_case, "populate_by_name": True}


class AddDocumentRequest(BaseModel):
    html: str
    cover_page_html: str | None = None
    params: DocumentConversionParams = Field(default_factory=DocumentConversionParams)

    model_config = {"alias_generator": _to_camel_case, "populate_by_name": True}


class VersionInfo(BaseModel):
    api_version: int = Field(title="API Version", description="API version for compatibility checks")
    python: str = Field(title="Python Version", description="Python runtime version")
    bulk_processing_service: str = Field(title="Service Version", description="Bulk Processing Service version")
    timestamp: str = Field(title="Build Timestamp", description="Docker image build timestamp (UTC)")

    model_config = {"alias_generator": _to_camel_case, "populate_by_name": True}


class JobStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


class JobMetadata(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    completed_at: datetime | None = None
    params: MergeJobStartParams
    pdf_count: int = 0
    failed_count: int = 0
