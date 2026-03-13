"""Clerk JWT authentication dependency for FastAPI."""

import httpx
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User

# Cache JWKS keys in-memory (refreshed on cold start)
_jwks_cache: dict | None = None


async def _get_jwks() -> dict:
    """Fetch Clerk JWKS (JSON Web Key Set) for token verification."""
    global _jwks_cache
    if _jwks_cache is not None:
        return _jwks_cache

    jwks_url = settings.clerk_jwks_url
    if not jwks_url:
        # Derive from Clerk publishable key if not explicitly set
        # Clerk frontend API domain is embedded in the publishable key
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="CLERK_JWKS_URL is not configured",
        )

    async with httpx.AsyncClient() as client:
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        return _jwks_cache


async def _verify_token(token: str) -> dict:
    """Verify and decode a Clerk-issued JWT."""
    jwks = await _get_jwks()
    try:
        # Clerk tokens are RS256
        payload = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        return payload
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {exc}",
        ) from exc


def _extract_bearer_token(request: Request) -> str:
    """Extract Bearer token from the Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    return auth_header[7:]


async def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency – returns the authenticated User, creating on first visit."""
    token = _extract_bearer_token(request)
    payload = await _verify_token(token)

    clerk_user_id: str | None = payload.get("sub")
    if not clerk_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )

    # Lookup or auto-provision user
    user = db.query(User).filter(User.clerk_user_id == clerk_user_id).first()
    if user is None:
        email = payload.get("email", payload.get("email_addresses", [{}])[0].get("email_address", "unknown@unknown"))
        if isinstance(email, list):
            email = email[0] if email else "unknown@unknown"
        user = User(clerk_user_id=clerk_user_id, email=str(email))
        db.add(user)
        db.commit()
        db.refresh(user)

    return user
