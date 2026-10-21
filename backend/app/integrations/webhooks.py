"""Outbound webhooks: deliver workflow events to external automation (n8n, Zapier, custom services).

A background thread follows the workflow_events table, so every event is covered whether the
orchestrator or the API recorded it. Each delivery is a signed JSON POST, retried with backoff,
and recorded in webhook_deliveries.

Signature: X-DevFlow-Signature: sha256=<hex HMAC-SHA256(secret, f"{X-DevFlow-Timestamp}.{raw body}")>
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select

from app.config import Settings
from app.db import SessionLocal
from app.models import Project, Webhook, WebhookDelivery, WorkflowEvent

log = logging.getLogger("devflow.webhooks")

# Events offered in the UI. Subscribing to "*" receives every event type.
EVENT_CATALOG = {
    "plan_ready": "Plan generated and waiting for approval",
    "plan_approved": "Plan approved",
    "plan_rejected": "Plan rejected",
    "planning_failed": "Planning failed",
    "task_started": "A task started",
    "task_completed": "A task passed its tests",
    "tests_failed": "A test run failed",
    "debug_attempt": "The debugger is attempting a fix",
    "review_completed": "Code review finished",
    "safety_violation": "A file operation was rejected by the sandbox",
    "workflow_halted": "Workflow stopped and needs a human decision",
    "workflow_paused": "Workflow paused",
    "workflow_cancelled": "Workflow cancelled",
    "limit_reached": "Cost or time limit reached",
    "executor_unavailable": "No sandbox available to run tests",
    "model_error": "A model call failed",
    "final_validation": "Final validation finished",
    "delivered": "Delivery package created",
}
DEFAULT_EVENTS = ["plan_ready", "workflow_halted", "safety_violation", "limit_reached", "model_error", "delivered"]
RETRY_DELAYS = (0, 2, 8)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def build_payload(event: WorkflowEvent, project: Project, app_url: str) -> dict:
    return {
        "id": f"evt_{event.id}",
        "event": event.type,
        "message": event.message,
        "agent": event.agent,
        "created_at": event.created_at.isoformat(),
        "project": {
            "id": project.id, "name": project.name, "status": project.status, "status_reason": project.status_reason,
            "mode": project.mode, "plan_version": project.plan_version,
        },
        "data": event.data or {},
        "links": {
            "dashboard": f"{app_url.rstrip('/')}/projects/{project.id}",
            "api": f"/api/projects/{project.id}",
        },
    }


def subscribed(hook: Webhook, event_type: str) -> bool:
    return hook.active and ("*" in hook.events or event_type in hook.events)


class WebhookDispatcher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="devflow-webhook")
        self._thread: threading.Thread | None = None
        self._cursor = 0

    def start(self) -> None:
        with SessionLocal() as db:
            # Start after existing events: history is not replayed on restart.
            self._cursor = db.scalar(select(func.coalesce(func.max(WorkflowEvent.id), 0)))
        self._thread = threading.Thread(target=self._loop, name="devflow-webhook-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._process()
            except Exception:  # noqa: BLE001 - the poller must survive bad rows or DB hiccups
                log.exception("webhook poll failed")
            self._stop.wait(self.settings.webhook_poll_seconds)

    def _process(self) -> None:
        with SessionLocal() as db:
            events = db.scalars(select(WorkflowEvent).where(WorkflowEvent.id > self._cursor).order_by(WorkflowEvent.id).limit(200)).all()
            if not events:
                return
            hooks_by_owner: dict[int, list[Webhook]] = {}
            for event in events:
                self._cursor = event.id
                project = db.get(Project, event.project_id)
                if project is None:
                    continue
                if project.owner_id not in hooks_by_owner:
                    hooks_by_owner[project.owner_id] = list(db.scalars(select(Webhook).where(
                        Webhook.owner_id == project.owner_id, Webhook.active.is_(True))))
                payload = None
                for hook in hooks_by_owner[project.owner_id]:
                    if subscribed(hook, event.type):
                        payload = payload or build_payload(event, project, self.settings.public_app_url)
                        self._pool.submit(self.deliver, hook.id, event.type, payload, event.id)

    def deliver(self, hook_id: int, event_type: str, payload: dict, event_id: int | None = None,
                max_attempts: int | None = None) -> WebhookDelivery:
        with SessionLocal() as db:
            hook = db.get(Webhook, hook_id)
            url, secret, headers = hook.url, hook.secret, dict(hook.headers or {})
        body = json.dumps(payload, separators=(",", ":"), default=str).encode()
        attempts, status_code, error, success = 0, None, None, False
        started = time.monotonic()
        for delay in RETRY_DELAYS[: max(1, max_attempts or self.settings.webhook_max_attempts)]:
            if delay:
                if self._stop.wait(delay):
                    break
            attempts += 1
            timestamp = str(int(time.time()))
            request_headers = {
                **headers,
                "Content-Type": "application/json",
                "User-Agent": "DevFlow-Webhooks/1.0",
                "X-DevFlow-Event": event_type,
                "X-DevFlow-Delivery": payload.get("id", ""),
                "X-DevFlow-Timestamp": timestamp,
                "X-DevFlow-Signature": sign(secret, timestamp, body),
            }
            try:
                response = httpx.post(url, content=body, headers=request_headers, timeout=self.settings.webhook_timeout_seconds,
                                      follow_redirects=False)
                status_code = response.status_code
                if 200 <= status_code < 300:
                    success, error = True, None
                    break
                error = f"HTTP {status_code}: {response.text[:300]}"
                if 400 <= status_code < 500 and status_code not in (408, 429):
                    break  # client errors will not fix themselves
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}"[:500]
        with SessionLocal() as db:
            delivery = WebhookDelivery(webhook_id=hook_id, event_id=event_id, event_type=event_type, success=success,
                                       status_code=status_code, attempts=attempts, error=error,
                                       duration_ms=int((time.monotonic() - started) * 1000))
            db.add(delivery)
            hook = db.get(Webhook, hook_id)
            if hook is not None:
                hook.last_delivery_at = datetime.now(UTC)
                hook.last_status_code = status_code
                hook.last_error = error
            db.commit()
            db.refresh(delivery)
        if not success:
            log.warning("webhook %s delivery of %s failed: %s", hook_id, event_type, error)
        return delivery

    def send_test(self, hook: Webhook, user_name: str) -> WebhookDelivery:
        payload = {
            "id": f"test_{int(time.time())}", "event": "ping", "message": f"Test delivery requested by {user_name}",
            "agent": None, "created_at": datetime.now(UTC).isoformat(), "project": None, "data": {},
            "links": {"dashboard": self.settings.public_app_url},
        }
        # One attempt: the user is waiting on the result.
        return self.deliver(hook.id, "ping", payload, max_attempts=1)
