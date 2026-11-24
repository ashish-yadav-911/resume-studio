import os
import tempfile
from pathlib import Path

import pytest

# Configure an isolated data directory and database before importing the app.
_tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = _tmp
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["AUTH_MODE"] = "dev"
os.environ["DEV_USER_ID"] = "test-user"
os.environ.setdefault("OPENAI_API_KEY", "")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import rate_limiter  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_database():
    """Give every test an isolated schema; the suite must be order-independent."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    rate_limiter._buckets.clear()
    yield


@pytest.fixture
def client(_reset_database):
    with TestClient(app) as client:
        client.headers.update({"X-Dev-User": "test-user"})
        yield client


@pytest.fixture
def sample_resume_path():
    return Path(__file__).resolve().parents[1] / "sample_resume.docx"