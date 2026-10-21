"""Independent acceptance tests for the todo_api benchmark task. Never shown to the agents."""

import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/acceptance.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_create_defaults(client):
    r = client.post("/todos", json={"title": "write report"})
    assert r.status_code == 201
    body = r.json()
    assert body["priority"] == "medium" and body["done"] is False


def test_validation(client):
    assert client.post("/todos", json={"title": "x", "priority": "urgent"}).status_code == 422
    assert client.post("/todos", json={"title": ""}).status_code == 422


def test_patch_and_filters(client):
    todo = client.post("/todos", json={"title": "ship", "priority": "high"}).json()
    r = client.patch(f"/todos/{todo['id']}", json={"done": True})
    assert r.status_code == 200 and r.json()["done"] is True
    done_titles = [t["title"] for t in client.get("/todos", params={"done": "true"}).json()]
    assert "ship" in done_titles
    high = client.get("/todos", params={"priority": "high"}).json()
    assert all(t["priority"] == "high" for t in high) and high


def test_stats(client):
    stats = client.get("/todos/stats").json()
    assert stats["total"] == stats["done"] + stats["open"] and stats["done"] >= 1


def test_delete_and_404(client):
    todo = client.post("/todos", json={"title": "temp"}).json()
    assert client.delete(f"/todos/{todo['id']}").status_code == 204
    assert client.patch(f"/todos/{todo['id']}", json={"done": True}).status_code == 404
    assert client.delete("/todos/987654").status_code == 404
