"""Source files produced by the mock provider's scripted "Notes API" scenario.

The first implementation of the delete endpoint contains a deliberate defect so that a demo run
exercises failure detection, debugging and regression validation.
"""

DATABASE_PY = '''"""Database engine, session factory and declarative base."""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./notes.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
'''

MODELS_PY = '''"""ORM models."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
'''

CONFTEST_T1 = '''import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register tables)
from app.database import Base


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(engine):
    session = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
'''

CONFTEST_T2 = CONFTEST_T1 + '''

@pytest.fixture
def client(engine):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    testing_session = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
'''

TEST_MODELS_PY = '''from app.models import Note


def test_note_persists_with_defaults(db_session):
    note = Note(title="First")
    db_session.add(note)
    db_session.commit()
    db_session.refresh(note)

    assert note.id is not None
    assert note.body == ""
    assert note.created_at is not None
'''

SCHEMAS_PY = '''"""Request and response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NoteCreate(BaseModel):
    title: str
    body: str = ""


class NoteUpdate(BaseModel):
    title: str | None = None
    body: str | None = None


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    body: str
    created_at: datetime
'''

SCHEMAS_VALIDATED_PY = '''"""Request and response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

TITLE_MAX = 200
BODY_MAX = 10_000


class NoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    body: str = Field(default="", max_length=BODY_MAX)


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    body: str | None = Field(default=None, max_length=BODY_MAX)


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    body: str
    created_at: datetime
'''

_MAIN_HEAD = '''"""Notes API."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import models
from app.database import Base, engine, get_db
from app.schemas import NoteCreate, NoteOut, NoteUpdate


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Notes API", version="1.0.0", lifespan=lifespan)


def _get_or_404(db: Session, note_id: int) -> models.Note:
    note = db.get(models.Note, note_id)
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return note


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
def create_note(payload: NoteCreate, db: Session = Depends(get_db)):
    note = models.Note(title=payload.title, body=payload.body)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note
'''

_LIST_PLAIN = '''

@app.get("/notes", response_model=list[NoteOut])
def list_notes(db: Session = Depends(get_db)):
    return db.scalars(select(models.Note).order_by(models.Note.id)).all()
'''

_LIST_SEARCH = '''

@app.get("/notes", response_model=list[NoteOut])
def list_notes(q: str | None = None, db: Session = Depends(get_db)):
    query = select(models.Note).order_by(models.Note.id)
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(models.Note.title.ilike(pattern), models.Note.body.ilike(pattern)))
    return db.scalars(query).all()
'''

_ITEM_ROUTES = '''

@app.get("/notes/{note_id}", response_model=NoteOut)
def get_note(note_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, note_id)


@app.put("/notes/{note_id}", response_model=NoteOut)
def update_note(note_id: int, payload: NoteUpdate, db: Session = Depends(get_db)):
    note = _get_or_404(db, note_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(note, field, value)
    db.commit()
    db.refresh(note)
    return note
'''

_DELETE_BUGGY = '''

@app.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note = db.get(models.Note, note_id)
    if note is not None:
        db.delete(note)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
'''

_DELETE_FIXED = '''

@app.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note = _get_or_404(db, note_id)
    db.delete(note)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
'''

MAIN_T2_BUGGY = _MAIN_HEAD + _LIST_PLAIN + _ITEM_ROUTES + _DELETE_BUGGY
MAIN_T2_FIXED = _MAIN_HEAD + _LIST_PLAIN + _ITEM_ROUTES + _DELETE_FIXED
MAIN_T3 = _MAIN_HEAD + _LIST_SEARCH + _ITEM_ROUTES + _DELETE_FIXED
MAIN_SINGLE_AGENT = _MAIN_HEAD + _LIST_SEARCH + _ITEM_ROUTES + _DELETE_BUGGY

TEST_API_PY = '''def _create(client, title="Groceries", body="milk, eggs"):
    response = client.post("/notes", json={"title": title, "body": body})
    assert response.status_code == 201
    return response.json()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_and_get_note(client):
    created = _create(client)
    response = client.get(f"/notes/{created['id']}")
    assert response.status_code == 200
    assert response.json()["title"] == "Groceries"


def test_list_notes(client):
    _create(client, "a")
    _create(client, "b")
    titles = [n["title"] for n in client.get("/notes").json()]
    assert titles == ["a", "b"]


def test_update_note(client):
    created = _create(client)
    response = client.put(f"/notes/{created['id']}", json={"body": "bread"})
    assert response.status_code == 200
    assert response.json()["body"] == "bread"
    assert response.json()["title"] == "Groceries"


def test_delete_note(client):
    created = _create(client)
    assert client.delete(f"/notes/{created['id']}").status_code == 204
    assert client.get(f"/notes/{created['id']}").status_code == 404


def test_missing_note_returns_404(client):
    assert client.get("/notes/999").status_code == 404
    assert client.put("/notes/999", json={"title": "x"}).status_code == 404


def test_delete_missing_note_returns_404(client):
    assert client.delete("/notes/999").status_code == 404
'''

TEST_SEARCH_PY = '''def test_search_matches_title_and_body(client):
    client.post("/notes", json={"title": "Shopping list", "body": "apples"})
    client.post("/notes", json={"title": "Ideas", "body": "buy a bike"})
    client.post("/notes", json={"title": "Work", "body": "standup at 10"})

    titles = sorted(n["title"] for n in client.get("/notes", params={"q": "buy"}).json())
    assert titles == ["Ideas"]

    titles = sorted(n["title"] for n in client.get("/notes", params={"q": "SHOP"}).json())
    assert titles == ["Shopping list"]


def test_empty_query_returns_all(client):
    client.post("/notes", json={"title": "one"})
    assert len(client.get("/notes", params={"q": ""}).json()) == 1
'''

TEST_VALIDATION_PY = '''def test_rejects_empty_title(client):
    assert client.post("/notes", json={"title": ""}).status_code == 422


def test_rejects_overlong_title(client):
    assert client.post("/notes", json={"title": "x" * 201}).status_code == 422


def test_update_rejects_empty_title(client):
    note = client.post("/notes", json={"title": "ok"}).json()
    assert client.put(f"/notes/{note['id']}", json={"title": ""}).status_code == 422
'''

README_MD = '''# Notes API

A small FastAPI service for creating, listing, searching, updating and deleting notes, backed by SQLite through SQLAlchemy.

## Setup

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Set `DATABASE_URL` to use a database other than `./notes.db`.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Liveness check |
| POST | /notes | Create a note (201) |
| GET | /notes?q= | List notes, optionally filtered by a case-insensitive search |
| GET | /notes/{id} | Fetch one note (404 if missing) |
| PUT | /notes/{id} | Partially update a note |
| DELETE | /notes/{id} | Delete a note (204, or 404 if missing) |

## Tests

```bash
pytest
```
'''
