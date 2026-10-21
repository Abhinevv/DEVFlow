import os
import tempfile
import time

# Configure an isolated environment before the app (and its cached settings/engine) is imported.
_DATA_DIR = tempfile.mkdtemp(prefix="devflow-test-")
os.environ.update({
    "DEVFLOW_DATA_DIR": _DATA_DIR,
    "DEVFLOW_DATABASE_URL": "",
    "DEVFLOW_LLM_PROVIDER": "mock",
    "DEVFLOW_EXECUTOR": "local",
    "DEVFLOW_ALLOW_LOCAL_EXECUTION": "true",
    "DEVFLOW_MOCK_DELAY": "0",
    "DEVFLOW_SECRET_KEY": "test-secret-key",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

TERMINAL = {"completed", "partial", "failed", "cancelled", "blocked", "paused", "awaiting_approval", "draft"}


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


_counter = iter(range(10_000))


@pytest.fixture
def auth(client):
    n = next(_counter)
    r = client.post("/api/auth/register", json={"email": f"user{n}@example.com", "password": "correct-horse", "display_name": f"User {n}"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


REQUIREMENTS = "Build a notes service where users can create, list, search, update and delete notes."


def create_project(client, auth, **overrides):
    body = {"name": "Notes", "requirements": REQUIREMENTS, **overrides}
    r = client.post("/api/projects", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def wait_for(client, auth, project_id, statuses, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        project = client.get(f"/api/projects/{project_id}", headers=auth).json()
        if project["status"] in statuses and not project["running"]:
            return project
        time.sleep(0.2)
    raise AssertionError(f"project {project_id} did not reach {statuses}; last status {project['status']}")


def plan_and_approve(client, auth, project_id):
    assert client.post(f"/api/projects/{project_id}/plan", headers=auth).status_code == 202
    project = wait_for(client, auth, project_id, {"awaiting_approval", "draft"})
    assert project["status"] == "awaiting_approval", project["status_reason"]
    assert client.post(f"/api/projects/{project_id}/plan/approve", json={}, headers=auth).status_code == 202
