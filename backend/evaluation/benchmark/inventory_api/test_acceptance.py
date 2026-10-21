"""Independent acceptance tests for the inventory_api benchmark task. Never shown to the agents."""

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


def test_create_and_get(client):
    r = client.post("/products", json={"sku": "A-1", "name": "Bolt", "quantity": 10})
    assert r.status_code == 201
    assert client.get("/products/A-1").json()["quantity"] == 10
    assert client.get("/products/NOPE").status_code == 404


def test_duplicate_sku_conflicts(client):
    client.post("/products", json={"sku": "DUP", "name": "x"})
    assert client.post("/products", json={"sku": "DUP", "name": "y"}).status_code == 409


def test_negative_values_rejected(client):
    assert client.post("/products", json={"sku": "NEG", "name": "x", "quantity": -1}).status_code == 422


def test_adjust_cannot_go_negative(client):
    client.post("/products", json={"sku": "ADJ", "name": "Nut", "quantity": 3})
    assert client.post("/products/ADJ/adjust", json={"delta": 2}).json()["quantity"] == 5
    assert client.post("/products/ADJ/adjust", json={"delta": -9}).status_code == 400
    assert client.get("/products/ADJ").json()["quantity"] == 5


def test_low_stock_report(client):
    client.post("/products", json={"sku": "LOW-B", "name": "b", "quantity": 1, "reorder_level": 2})
    client.post("/products", json={"sku": "LOW-A", "name": "a", "quantity": 2, "reorder_level": 2})
    client.post("/products", json={"sku": "OK", "name": "c", "quantity": 50, "reorder_level": 2})
    skus = [p["sku"] for p in client.get("/products/low-stock").json()]
    assert "OK" not in skus
    low = [s for s in skus if s.startswith("LOW-")]
    assert low == ["LOW-A", "LOW-B"]
