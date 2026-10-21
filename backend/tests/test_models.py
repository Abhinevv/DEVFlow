from datetime import UTC, datetime, timedelta, timezone

from app.db import session_scope
from app.models import User


def test_timestamps_round_trip_as_utc_aware():
    ist = timezone(timedelta(hours=5, minutes=30))
    with session_scope() as db:
        user = User(email="tz@example.com", display_name="tz", password_hash="x", created_at=datetime(2026, 1, 1, 12, 0, tzinfo=ist))
        db.add(user)
        db.flush()
        uid = user.id
    with session_scope() as db:
        loaded = db.get(User, uid).created_at
    assert loaded.tzinfo is not None
    assert loaded == datetime(2026, 1, 1, 6, 30, tzinfo=UTC)
