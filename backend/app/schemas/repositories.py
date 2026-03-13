"""Repository request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class RepositoryCreate(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_github_https_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith("https://github.com/"):
            raise ValueError("Only public GitHub HTTPS URLs are supported")
        parts = v.replace("https://github.com/", "").split("/")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError("URL must be in the form https://github.com/{owner}/{repo}")
        # Strip .git suffix if present
        if parts[1].endswith(".git"):
            parts[1] = parts[1][:-4]
        return f"https://github.com/{parts[0]}/{parts[1]}"


class RepositoryResponse(BaseModel):
    id: uuid.UUID
    url: str
    host: str
    owner: str
    name: str
    default_branch: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
