"""FindingTriage model – per-scan triage state for finding occurrences."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import TriageStatus


class FindingTriage(Base):
    __tablename__ = "finding_triage"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    finding_occurrence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("finding_occurrences.id"), unique=True, nullable=False
    )
    status: Mapped[TriageStatus] = mapped_column(default=TriageStatus.open, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    finding_occurrence = relationship("FindingOccurrence", back_populates="triage")
