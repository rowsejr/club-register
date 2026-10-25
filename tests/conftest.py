import os
import tempfile
from pathlib import Path

# Tests must never touch a real database: point at a throwaway file BEFORE app.db is imported.
_TEST_DIR = tempfile.mkdtemp(prefix="club-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TEST_DIR, 'test.db').as_posix()}"
os.environ["CLUB_ENV"] = "dev"
os.environ["CLUB_BACKUP_DIR"] = str(Path(_TEST_DIR, "backups"))
os.environ["CLUB_AUTH_RATE_LIMIT"] = "1000"
os.environ["CLUB_FORM_MIN_SECONDS"] = "0"  # public forms normally refuse instant submissions

import pytest
from fastapi.testclient import TestClient

pytest_plugins = ["books_fixtures"]

from app.db import SessionLocal, init_db
from app.main import app
from app.models import Base


@pytest.fixture(autouse=True)
def reset_db() -> None:
    """Empty every table (children first) so each test starts clean, whatever tables exist."""
    init_db()
    with SessionLocal() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
