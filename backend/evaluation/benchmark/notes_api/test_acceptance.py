"""Independent acceptance tests for the notes_api benchmark task. Never shown to the agents."""

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


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_returns_201_and_fields(client):
    r = client.post("/notes", json={"title": "Acceptance"})
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "Acceptance" and body["body"] == "" and "id" in body and "created_at" in body


def test_title_validation(client):
    assert client.post("/notes", json={"title": ""}).status_code == 422
    assert client.post("/notes", json={"title": "x" * 201}).status_code == 422


def test_search_is_case_insensitive(client):
    client.post("/notes", json={"title": "Zebra facts", "body": "stripes"})
    titles = [n["title"] for n in client.get("/notes", params={"q": "STRIPES"}).json()]
    assert titles == ["Zebra facts"]


def test_update_and_delete(client):
    note = client.post("/notes", json={"title": "temp", "body": "a"}).json()
    r = client.put(f"/notes/{note['id']}", json={"body": "b"})
    assert r.status_code == 200 and r.json()["body"] == "b" and r.json()["title"] == "temp"
    assert client.delete(f"/notes/{note['id']}").status_code == 204
    assert client.get(f"/notes/{note['id']}").status_code == 404


def test_missing_ids_return_404(client):
    assert client.get("/notes/987654").status_code == 404
    assert client.put("/notes/987654", json={"title": "x"}).status_code == 404
    assert client.delete("/notes/987654").status_code == 404
