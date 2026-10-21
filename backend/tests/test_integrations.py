"""API keys and outbound webhooks (used by the n8n integration)."""

import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.conftest import create_project, plan_and_approve, wait_for


class _Receiver(BaseHTTPRequestHandler):
    received: list = []
    status = 200

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).received.append({"headers": dict(self.headers), "body": body})
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def receiver():
    _Receiver.received = []
    _Receiver.status = 200
    server = HTTPServer(("127.0.0.1", 0), _Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/hook", _Receiver
    server.shutdown()


def _wait(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def test_api_key_authenticates_and_can_be_revoked(client, auth):
    r = client.post("/api/api-keys", json={"name": "n8n"}, headers=auth)
    assert r.status_code == 201
    key = r.json()["key"]
    assert key.startswith("dfk_") and r.json()["prefix"] == key[:10]
    assert "key" not in client.get("/api/api-keys", headers=auth).json()[0]

    key_auth = {"Authorization": f"Bearer {key}"}
    assert client.get("/api/projects", headers=key_auth).status_code == 200
    assert client.get("/api/api-keys", headers=auth).json()[0]["last_used_at"] is not None

    client.post(f"/api/api-keys/{r.json()['id']}/revoke", headers=auth)
    assert client.get("/api/projects", headers=key_auth).status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer dfk_madeup"}).status_code == 401


def test_webhook_validation(client, auth):
    bad = [
        {"name": "x", "url": "ftp://example.com"},
        {"name": "x", "url": "http://example.com", "events": ["not_an_event"]},
        {"name": "x", "url": "http://example.com", "events": []},
        {"name": "x", "url": "http://example.com", "headers": {"X-DevFlow-Signature": "spoof"}},
        {"name": "x", "url": "http://example.com", "headers": {"Bad Header": "v"}},
        {"name": "x", "url": "http://example.com", "headers": {"X-Token": "a\r\nInjected: 1"}},
    ]
    for body in bad:
        assert client.post("/api/webhooks", json=body, headers=auth).status_code == 422, body


def test_webhooks_deliver_signed_events(client, auth, receiver):
    url, rec = receiver
    r = client.post("/api/webhooks", json={"name": "n8n", "url": url, "events": ["*"], "headers": {"X-Webhook-Token": "tok"}}, headers=auth)
    assert r.status_code == 201
    hook, secret = r.json(), r.json()["secret"]
    listed = client.get("/api/webhooks", headers=auth).json()[0]
    assert listed["header_names"] == ["X-Webhook-Token"] and "secret" not in listed and "headers" not in listed

    pid = create_project(client, auth, constraints={"mock_scenario": "clean"})["id"]
    plan_and_approve(client, auth, pid)
    wait_for(client, auth, pid, {"completed", "partial", "failed", "blocked"})
    events = lambda: [json.loads(x["body"])["event"] for x in rec.received]  # noqa: E731
    assert _wait(lambda: "delivered" in events())
    assert {"plan_ready", "plan_approved", "task_completed", "final_validation"} <= set(events())

    first = rec.received[0]
    headers = {k.lower(): v for k, v in first["headers"].items()}
    expected = "sha256=" + hmac.new(secret.encode(), headers["x-devflow-timestamp"].encode() + b"." + first["body"], hashlib.sha256).hexdigest()
    assert headers["x-devflow-signature"] == expected
    assert headers["x-webhook-token"] == "tok"
    payload = json.loads(first["body"])
    assert payload["project"]["id"] == pid and payload["links"]["dashboard"].endswith(f"/projects/{pid}")

    deliveries = client.get(f"/api/webhooks/{hook['id']}/deliveries", headers=auth).json()
    assert deliveries and all(d["success"] for d in deliveries)


def test_event_filter_test_endpoint_and_failures(client, auth, receiver):
    url, rec = receiver
    hook = client.post("/api/webhooks", json={"name": "only plans", "url": url, "events": ["plan_ready"]}, headers=auth).json()
    pid = create_project(client, auth, constraints={"mock_scenario": "clean"})["id"]
    client.post(f"/api/projects/{pid}/plan", headers=auth)
    wait_for(client, auth, pid, {"awaiting_approval"})
    assert _wait(lambda: len(rec.received) >= 1)
    time.sleep(1.5)
    assert {json.loads(x["body"])["event"] for x in rec.received} == {"plan_ready"}

    r = client.post(f"/api/webhooks/{hook['id']}/test", headers=auth)
    assert r.json()["success"] and r.json()["event_type"] == "ping"

    rec.status = 404
    r = client.post(f"/api/webhooks/{hook['id']}/test", headers=auth)
    assert not r.json()["success"] and r.json()["attempts"] == 1  # 4xx is not retried
    assert client.get("/api/webhooks", headers=auth).json()[-1]["last_status_code"] == 404

    assert client.patch(f"/api/webhooks/{hook['id']}", json={"active": False}, headers=auth).json()["active"] is False
    assert client.delete(f"/api/webhooks/{hook['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/webhooks/{hook['id']}/deliveries", headers=auth).status_code == 404


def test_stale_plan_version_cannot_be_approved(client, auth):
    pid = create_project(client, auth)["id"]
    client.post(f"/api/projects/{pid}/plan", headers=auth)
    wait_for(client, auth, pid, {"awaiting_approval"})
    client.post(f"/api/projects/{pid}/plan/revise", json={"feedback": "Add tags"}, headers=auth)
    project = wait_for(client, auth, pid, {"awaiting_approval"})
    assert project["plan_version"] == 2
    r = client.post(f"/api/projects/{pid}/plan/approve", json={"plan_version": 1}, headers=auth)
    assert r.status_code == 409 and "superseded" in r.json()["detail"]
    client.post(f"/api/projects/{pid}/cancel", json={}, headers=auth)


def test_webhooks_are_private(client, auth):
    hook = client.post("/api/webhooks", json={"name": "mine", "url": "http://127.0.0.1:9/x"}, headers=auth).json()
    other = client.post("/api/auth/register", json={"email": "other-hooks@example.com", "password": "password123", "display_name": "O"})
    other_auth = {"Authorization": f"Bearer {other.json()['access_token']}"}
    assert client.get("/api/webhooks", headers=other_auth).json() == []
    assert client.post(f"/api/webhooks/{hook['id']}/test", headers=other_auth).status_code == 404
    assert client.delete(f"/api/webhooks/{hook['id']}", headers=other_auth).status_code == 404
