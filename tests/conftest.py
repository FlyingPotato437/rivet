import os

os.environ["RIVET_DATABASE_URL"] = "postgresql+psycopg://localhost:55432/rivet_test"
import pytest
from fastapi.testclient import TestClient
from backend.storage.models import Base
from backend.storage.db import engine
from backend.api.main import app


@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(app, headers={"X-Rivet-Client": "workspace"}) as c:
        yield c
