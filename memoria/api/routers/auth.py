"""API key management endpoints."""

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from memoria.api.database import get_db_session
from memoria.api.dependencies import ADMIN_USER_ID, get_current_user_id, require_admin
from memoria.api.models import ApiKey, User

router = APIRouter(tags=["auth"])


class CreateKeyRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    expires_at: datetime | None = None


class KeyResponse(BaseModel):
    key_id: str
    user_id: str
    name: str
    key_prefix: str
    created_at: str
    expires_at: str | None = None
    last_used_at: str | None = None
    raw_key: str | None = None


def _key_to_response(key: ApiKey, raw_key: str | None = None) -> KeyResponse:
    return KeyResponse(
        key_id=key.key_id,
        user_id=key.user_id,
        name=key.name,
        key_prefix=key.key_prefix,
        created_at=key.created_at.isoformat(),
        expires_at=key.expires_at.isoformat() if key.expires_at else None,
        last_used_at=key.last_used_at.isoformat() if key.last_used_at else None,
        raw_key=raw_key,
    )


@router.post("/keys", response_model=KeyResponse, status_code=status.HTTP_201_CREATED)
def create_api_key(
    req: CreateKeyRequest,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db_session),
):
    """Create an API key for a user. Requires master key. Auto-creates user if new."""
    user = db.query(User.user_id).filter_by(user_id=req.user_id).first()
    if not user:
        db.add(User(user_id=req.user_id))

    raw_key, key_hash, key_prefix = ApiKey.generate_key()
    key = ApiKey(
        key_id=str(uuid4()),
        user_id=req.user_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=req.name,
        expires_at=req.expires_at,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return _key_to_response(key, raw_key)


@router.get("/keys", response_model=list[KeyResponse])
def list_api_keys(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db_session),
):
    rows = db.query(ApiKey).filter_by(user_id=user_id, is_active=1).all()
    return [_key_to_response(r) for r in rows]


@router.get("/keys/{key_id}", response_model=KeyResponse)
def get_api_key(
    key_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db_session),
):
    """Get a single API key by ID."""
    row = db.query(ApiKey).filter_by(key_id=key_id, is_active=1).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Key not found")
    if user_id != ADMIN_USER_ID and row.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not your key")
    return _key_to_response(row)


@router.put(
    "/keys/{key_id}/rotate",
    response_model=KeyResponse,
    status_code=status.HTTP_201_CREATED,
)
def rotate_api_key(
    key_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db_session),
):
    """Atomically revoke old key and issue a new one with the same name/user/expiry."""
    old = db.query(ApiKey).filter_by(key_id=key_id, is_active=1).first()
    if old is None:
        raise HTTPException(status_code=404, detail="Key not found")
    if user_id != ADMIN_USER_ID and old.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not your key")

    raw_key, key_hash, key_prefix = ApiKey.generate_key()
    new_key = ApiKey(
        key_id=str(uuid4()),
        user_id=old.user_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=old.name,
        expires_at=old.expires_at,
    )
    old.is_active = 0
    db.add(new_key)
    db.commit()
    db.refresh(new_key)
    return _key_to_response(new_key, raw_key)


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db_session),
):
    row = db.query(ApiKey.key_id, ApiKey.user_id).filter_by(key_id=key_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Key not found")
    if user_id != ADMIN_USER_ID and row.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not your key")
    db.query(ApiKey).filter_by(key_id=key_id).update({"is_active": 0})
    db.commit()
