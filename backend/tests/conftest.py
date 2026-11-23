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

from app.main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as client:
        client.headers.update({"X-Dev-User": "test-user"})
        yield client


@pytest.fixture
def sample_resume_path():
    return Path(__file__).resolve().parents[1] / "sample_resume.docx"