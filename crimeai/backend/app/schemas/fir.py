"""Schemas for FIR intake and results."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

FIR_NUMBER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9/\-_.]{2,49}$"


class FIRCreateRequest(BaseModel):
    fir_number: str = Field(..., pattern=FIR_NUMBER_PATTERN)
    raw_text: str = Field(..., min_length=20, max_length=50000)
    crime_id: uuid.UUID | None = None
    incident_date: datetime | None = None

    @field_validator("incident_date")
    @classmethod
    def _not_future(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            value = value if value.tzinfo else value.replace(tzinfo=UTC)
            if value > datetime.now(UTC):
                raise ValueError("incident_date cannot be in the future")
        return value


class FIRResponse(BaseModel):
    id: uuid.UUID
    fir_number: str
    crime_id: uuid.UUID | None
    submitted_by: uuid.UUID
    incident_date: datetime | None
    text: str
    text_redacted: bool
    file_type: str | None
    file_sha256: str | None
    extracted_entities: dict[str, Any] | None
    extraction_confidence: float | None
    nlp_status: str
    nlp_error: str | None
    language: str | None = None
    highlights: list[dict[str, Any]] | None = None
    has_document: bool = False
    created_at: datetime
    updated_at: datetime


class FIRListResponse(BaseModel):
    items: list[FIRResponse]
    total: int
