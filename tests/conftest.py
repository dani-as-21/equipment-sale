import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="capture-test-"))
os.environ["CAPTURE_DB"] = str(TMP / "app.db")
os.environ["CAPTURE_PHOTO_DIR"] = str(TMP / "photos")
os.environ["CAPTURE_THUMB_DIR"] = str(TMP / "thumbs")
os.environ["CAPTURE_SOURCES"] = str(ROOT / "data" / "sources")
os.environ["EQUIPMENT_PASSWORD"] = "test-pass"
os.environ["CAPTURE_SECRET"] = "test-secret"

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        response = test_client.post("/api/login", json={"password": "test-pass"})
        assert response.status_code == 200, response.text
        yield test_client
