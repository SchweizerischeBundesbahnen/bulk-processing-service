from datetime import datetime  # noqa: TC003
from enum import StrEnum

from pydantic import BaseModel


def _to_camel_case(field_name: str) -> str:
    return "".join(word if i == 0 else word.capitalize() for i, word in enumerate(field_name.split("_")))


class MergeJobStartParams(BaseModel):
    encoding: str = "utf-8"
    media_type: str = "print"
    presentational_hints: bool = False
    base_url: str | None = None
    scale_factor: str | None = None
    file_name: str = "merged-document.pdf"
    pdf_variant: str | None = None
    custom_metadata: bool = False
    full_fonts: bool = False
    weasy_print_service_url: str | None = None

    model_config = {"alias_generator": _to_camel_case, "populate_by_name": True}


class AddDocumentWithCoverRequest(BaseModel):
    html: str
    cover_page_html: str

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
