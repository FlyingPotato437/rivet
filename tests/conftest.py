import os

os.environ["RIVET_DATABASE_URL"] = "postgresql+psycopg://localhost:55432/rivet_test"
os.environ["RIVET_AUTH_MODE"] = "local"
os.environ["RIVET_LOCAL_ONLY"] = "true"
import pytest
from fastapi.testclient import TestClient
from backend.storage.models import Base
from backend.storage.db import engine
from backend.api.main import app
from backend.storage import db
from backend.ingestion import parser
from backend.api import main


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    # Each test owns its generated originals and exports as well as its database.
    assert engine.url.database == "rivet_test", (
        "Refusing to rebuild a non-test database"
    )
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
