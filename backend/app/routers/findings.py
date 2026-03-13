"""Finding triage endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.finding_occurrence import FindingOccurrence
from app.models.finding_triage import FindingTriage
from app.models.repository import Repository
from app.models.scan import Scan
from app.models.user import User
from app.schemas.findings import TriageResponse, TriageUpdate

router = APIRouter(tags=["findings"])


@router.patch(
    "/api/finding-occurrences/{finding_occurrence_id}/triage",
    response_model=TriageResponse,
)
async def update_triage(
    finding_occurrence_id: uuid.UUID,
    body: TriageUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update triage status and note for a finding occurrence."""
    occurrence = db.query(FindingOccurrence).filter(FindingOccurrence.id == finding_occurrence_id).first()
    if not occurrence:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding occurrence not found")

    # Verify ownership chain: occurrence → scan → repo → user
    scan = db.query(Scan).filter(Scan.id == occurrence.scan_id).first()
    if not scan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found")
    repo = db.query(Repository).filter(Repository.id == scan.repository_id, Repository.user_id == user.id).first()
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")

    # Upsert triage record
    triage = db.query(FindingTriage).filter(FindingTriage.finding_occurrence_id == finding_occurrence_id).first()
    if triage:
        triage.status = body.status
        triage.note = body.note
    else:
        triage = FindingTriage(
            finding_occurrence_id=finding_occurrence_id,
            status=body.status,
            note=body.note,
        )
        db.add(triage)

    db.commit()
    db.refresh(triage)
    return triage
