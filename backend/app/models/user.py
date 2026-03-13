"""User model – mapped from Clerk authenticated users."""

import uuid

from sqlalchemy import String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clerk_user_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[str] = mapped_column(server_default=func.now(), nullable=False)

    # Relationships
    repositories = relationship("Repository", back_populates="user", cascade="all, delete-orphan")
