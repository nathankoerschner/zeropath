"""Finding and triage request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import Severity, TriageStatus


class TriageResponse(BaseModel):
    id: uuid.UUID
    finding_occurrence_id: uuid.UUID
    status: TriageStatus
    note: str | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class FindingOccurrenceResponse(BaseModel):
    id: uuid.UUID
    scan_id: uuid.UUID
    finding_identity_id: uuid.UUID
    file_path: str
    line_number: int
    severity: Severity
    vulnerability_type: str
    description: str
    explanation: str
    code_snippet: str | None
    created_at: datetime
    triage: TriageResponse | None = None
    github_deeplink: str | None = None

    model_config = {"from_attributes": True}


class TriageUpdate(BaseModel):
    status: TriageStatus
    note: str | None = None
