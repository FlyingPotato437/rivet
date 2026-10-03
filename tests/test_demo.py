"""Demo remains an ordinary isolated tenant, imported through real ingestion."""

from backend.demo import demo_config
from backend.domain.service import scoped
from backend.identity import Identity, identity_scope
from backend.orders.service import lock_order
from backend.records.service import view
from backend.storage.db import Session
from backend.storage.models import Document, Order
from backend.worker import claim, process
from scripts.setup_demo import seed_public_documents


def test_demo_config_is_local_development_only(monkeypatch, client):
    for k, v in {
        "RIVET_DEMO_ENABLED": "true",
        "RIVET_LOCAL_ONLY": "true",
        "CLERK_SECRET_KEY": "sk_test_x",
        "VITE_CLERK_PUBLISHABLE_KEY": "pk_test_x",
        "RIVET_DEMO_USER_ID": "user_demo",
        "RIVET_DEMO_ORG_ID": "org_demo",
    }.items():
        monkeypatch.setenv(k, v)
    assert demo_config()["user_id"] == "user_demo"
    for key, invalid in [
        ("RIVET_DEMO_ENABLED", "false"),
        ("RIVET_LOCAL_ONLY", "false"),
        ("CLERK_SECRET_KEY", "sk_live_x"),
        ("VITE_CLERK_PUBLISHABLE_KEY", "pk_live_x"),
        ("RIVET_DEMO_ORG_ID", ""),
    ]:
        previous = __import__("os").environ[key]
        monkeypatch.setenv(key, invalid)
        assert demo_config() is None
        monkeypatch.setenv(key, previous)


def test_public_demo_seed_preserves_tenant_and_is_repeatable(tmp_path):
    from scripts.make_order_replay import write_pdf

    path = tmp_path / "sample.pdf"
    write_pdf(path, "Review", ["Comment 1: Confirm the cable entry location."])
    package = {
        "name": "alachua-test",
        "excerpt": "sample.pdf",
        "pages": [33],
        "url": "https://example.com/original.pdf",
        "sha256": "fixture",
    }
    team = Identity("10000000-0000-0000-0000-000000000001", "demo")
    with identity_scope(team):
        with Session.begin() as s:
            ids = seed_public_documents(s, [package], tmp_path)
        with Session.begin() as s:
            assert seed_public_documents(s, [package], tmp_path) == ids
            assert len(s.scalars(scoped(Document)).all()) == 1
    task = claim()
    assert task[4] == team.organization_id
    process(task)
    with identity_scope(team), Session.begin() as s:
        record = view(s, lock_order(s, ids[0]))
        assert record["documents"][0]["state"] == "ready"
        assert record["documents"][0]["public_source"]["pages"] == [33]
        assert record["comments"]
        assert record["approvals"] == []
    with (
        identity_scope(
            Identity("20000000-0000-0000-0000-000000000002", "someone-else")
        ),
        Session() as s,
    ):
        assert s.scalars(scoped(Order)).all() == []
