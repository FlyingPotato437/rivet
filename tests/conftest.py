import os

os.environ["RIVET_DATABASE_URL"] = os.getenv(
    "RIVET_TEST_DATABASE_URL", "postgresql+psycopg://localhost:55432/rivet_test"
)
os.environ["RIVET_AUTH_MODE"] = "local"
os.environ["RIVET_LOCAL_ONLY"] = "true"
import pytest
from fastapi.testclient import TestClient
from backend.storage.models import Base
from backend.equipment.evidence import models as equipment_evidence_models  # noqa: F401 - register append-only evidence tables
from backend.storage.db import engine
from backend.api.main import app
from backend.storage import db
from backend.ingestion import parser
from backend.api import main


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    # Each test owns its generated originals and exports as well as its database.
    assert engine.url.database == "rivet_test" or (
        engine.url.database and engine.url.database.startswith("rivet_test_")
    ), (
        "Refusing to rebuild a non-test database"
    )
    # Most unit scenarios mock the sequential investigator. Parallel work has
    # its own durable-job tests and is explicitly enabled there.
    monkeypatch.setenv("RIVET_AGENT_COMMENT_TASKS", "false")
    monkeypatch.setenv("RIVET_VISION_ENABLED", "false")
    monkeypatch.setenv("RIVET_ENGINEERING_VISION_ENABLED", "false")
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    for module in (db, parser, main):
        monkeypatch.setattr(module, "BLOBS", blobs)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(app, headers={"X-Rivet-Client": "workspace"}) as c:
        yield c
