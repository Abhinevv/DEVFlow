"""API keys and outbound webhooks for automation tools such as n8n."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.integrations.webhooks import DEFAULT_EVENTS, EVENT_CATALOG, WebhookDispatcher
from app.models import ApiKey, User, Webhook, WebhookDelivery
from app.security import current_user, new_api_key

router = APIRouter(prefix="/api", tags=["integrations"])

HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
RESERVED_HEADERS = {"content-type", "content-length", "host", "user-agent", "transfer-encoding", "connection"}
MAX_WEBHOOKS = 20
MAX_KEYS = 20


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- API keys --------------------------------------------------------------------------------

class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ApiKeyOut(ORM):
    id: int
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str  # returned only at creation


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_keys(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.id.desc())).all()


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_key(body: ApiKeyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    active = db.scalars(select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))).all()
    if len(active) >= MAX_KEYS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"At most {MAX_KEYS} active API keys")
    key, key_hash = new_api_key()
    record = ApiKey(user_id=user.id, name=body.name.strip(), prefix=key[:10], key_hash=key_hash)
    db.add(record)
    db.commit()
    return ApiKeyCreated(**ApiKeyOut.model_validate(record).model_dump(), key=key)


@router.post("/api-keys/{key_id}/revoke", response_model=ApiKeyOut)
def revoke_key(key_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    record = db.get(ApiKey, key_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    record.revoked_at = record.revoked_at or datetime.now(UTC)
    db.commit()
    return record


# --- webhooks --------------------------------------------------------------------------------

def _check_events(events: list[str]) -> list[str]:
    unknown = [e for e in events if e != "*" and e not in EVENT_CATALOG]
    if unknown:
        raise ValueError(f"unknown events: {', '.join(unknown)}")
    if not events:
        raise ValueError("subscribe to at least one event")
    return sorted(set(events))


def _check_headers(headers: dict[str, str]) -> dict[str, str]:
    if len(headers) > 5:
        raise ValueError("at most 5 custom headers")
    for name, value in headers.items():
        if not HEADER_NAME_RE.match(name):
            raise ValueError(f"invalid header name {name!r}")
        if name.lower() in RESERVED_HEADERS or name.lower().startswith("x-devflow-"):
            raise ValueError(f"header {name!r} is set by DevFlow")
        if not isinstance(value, str) or len(value) > 500 or "\n" in value or "\r" in value:
            raise ValueError(f"invalid value for header {name!r}")
    return headers


class WebhookIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(pattern=r"^https?://[^\s]+$", max_length=500)
    events: list[str] = Field(default_factory=lambda: list(DEFAULT_EVENTS))
    headers: dict[str, str] = Field(default_factory=dict)
    active: bool = True

    @field_validator("events")
    @classmethod
    def _events(cls, v: list[str]) -> list[str]:
        return _check_events(v)

    @field_validator("headers")
    @classmethod
    def _headers(cls, v: dict[str, str]) -> dict[str, str]:
        return _check_headers(v)


class WebhookPatch(BaseModel):
    active: bool | None = None
    events: list[str] | None = None

    @field_validator("events")
    @classmethod
    def _events(cls, v):
        return None if v is None else _check_events(v)


class WebhookOut(ORM):
    id: int
    name: str
    url: str
    events: list[str]
    header_names: list[str]
    active: bool
    created_at: datetime
    last_delivery_at: datetime | None
    last_status_code: int | None
    last_error: str | None

    @classmethod
    def of(cls, hook: Webhook) -> WebhookOut:
        # Header values may hold credentials, so only their names are returned.
        return cls.model_validate({**{c: getattr(hook, c) for c in (
            "id", "name", "url", "events", "active", "created_at", "last_delivery_at", "last_status_code", "last_error")},
            "header_names": sorted(hook.headers or {})})


class WebhookCreated(WebhookOut):
    secret: str  # returned only at creation


class DeliveryOut(ORM):
    id: int
    event_id: int | None
    event_type: str
    success: bool
    status_code: int | None
    attempts: int
    error: str | None
    duration_ms: int
    created_at: datetime


def _own_hook(hook_id: int, user: User, db: Session) -> Webhook:
    hook = db.get(Webhook, hook_id)
    if hook is None or hook.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook not found")
    return hook


def get_dispatcher(request: Request) -> WebhookDispatcher:
    return request.app.state.webhooks


@router.get("/webhooks/events")
def event_catalog(user: User = Depends(current_user)):
    return {"events": EVENT_CATALOG, "defaults": DEFAULT_EVENTS}


@router.get("/webhooks", response_model=list[WebhookOut])
def list_webhooks(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [WebhookOut.of(h) for h in db.scalars(select(Webhook).where(Webhook.owner_id == user.id).order_by(Webhook.id))]


@router.post("/webhooks", response_model=WebhookCreated, status_code=status.HTTP_201_CREATED)
def create_webhook(body: WebhookIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if len(db.scalars(select(Webhook).where(Webhook.owner_id == user.id)).all()) >= MAX_WEBHOOKS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"At most {MAX_WEBHOOKS} webhooks")
    hook = Webhook(owner_id=user.id, name=body.name.strip(), url=body.url, secret="whsec_" + secrets.token_urlsafe(24),
                   events=body.events, headers=body.headers, active=body.active)
    db.add(hook)
    db.commit()
    return WebhookCreated(**WebhookOut.of(hook).model_dump(), secret=hook.secret)


@router.patch("/webhooks/{hook_id}", response_model=WebhookOut)
def update_webhook(hook_id: int, body: WebhookPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    hook = _own_hook(hook_id, user, db)
    if body.active is not None:
        hook.active = body.active
    if body.events is not None:
        hook.events = body.events
    db.commit()
    return WebhookOut.of(hook)


@router.delete("/webhooks/{hook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(hook_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    hook = _own_hook(hook_id, user, db)
    db.execute(delete(WebhookDelivery).where(WebhookDelivery.webhook_id == hook.id))
    db.delete(hook)
    db.commit()


@router.post("/webhooks/{hook_id}/test", response_model=DeliveryOut)
def test_webhook(hook_id: int, user: User = Depends(current_user), db: Session = Depends(get_db),
                 dispatcher: WebhookDispatcher = Depends(get_dispatcher)):
    hook = _own_hook(hook_id, user, db)
    return dispatcher.send_test(hook, user.display_name)


@router.get("/webhooks/{hook_id}/deliveries", response_model=list[DeliveryOut])
def deliveries(hook_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    hook = _own_hook(hook_id, user, db)
    return db.scalars(select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook.id)
                      .order_by(WebhookDelivery.id.desc()).limit(50)).all()
