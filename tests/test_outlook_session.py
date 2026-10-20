from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from backend import auth, outlook_session as session
from backend.storage.db import Session
from backend.storage.models import now

ORIGIN = "https://localhost:5179"
HEADERS = {
    "Origin": ORIGIN,
    "Sec-Fetch-Site": "same-origin",
    "X-Rivet-Session": "outlook",
}


@pytest.fixture
def clerk(monkeypatch):
    monkeypatch.setenv("RIVET_AUTH_MODE", "clerk")
    monkeypatch.setenv("CLERK_SECRET_KEY", "test-only")
    monkeypatch.setenv("VITE_CLERK_PUBLISHABLE_KEY", "pk_test_Y2xlcmsuZXhhbXBsZSQ")
    monkeypatch.setenv("RIVET_ALLOWED_ORIGINS", ORIGIN)
    user = NS(first_name="Test", last_name="PM")
    member = NS(
        organization=NS(id="org_one", name="One"),
        role="org:member",
        public_user_data=user,
    )
    live = NS(
        status="active",
        user_id="user_one",
        expire_at=int((now() + timedelta(days=1)).timestamp() * 1000),
        last_active_organization_id="org_one",
        tasks=[],
    )
    state = NS(live=live, members=[member], unavailable=False)

    class FakeClerk:
        def __init__(self, **kwargs):
            self.sessions = NS(get=self.get)
            self.users = NS(
                get_organization_memberships=lambda **kw: NS(data=state.members)
            )

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, **kwargs):
            if state.unavailable:
                raise ConnectionError("provider unavailable")
            return live

    monkeypatch.setattr(session, "Clerk", FakeClerk)

    def authenticate(request, options):
        return NS(
            is_signed_in=True,
            payload={
                "iss": auth.clerk_issuer(),
                "azp": ORIGIN,
                "sub": "user_one",
                "sid": "sess_one",
                "org_id": "org_one",
                "org_role": "org:member",
            },
        )

    monkeypatch.setattr(auth, "authenticate_request", authenticate)
    return state


def connect(client):
    result = client.post(
        "/api/outlook/session", headers={**HEADERS, "Authorization": "Bearer test"}
    )
    assert result.status_code == 200, result.text
    cookie = result.headers["set-cookie"].split(";")[0]
    client.cookies.clear()  # TestClient is HTTP; explicitly model the browser's HTTPS cookie.
    return cookie


def test_reopen_uses_live_clerk_and_http_only_partitioned_cookie(client, clerk):
    result = client.post(
        "/api/outlook/session", headers={**HEADERS, "Authorization": "Bearer test"}
    )
    assert result.status_code == 200
    header = result.headers["set-cookie"]
    for attribute in ("Secure", "HttpOnly", "SameSite=None", "Partitioned", "Path=/"):
        assert attribute in header
    cookie = header.split(";")[0]
    with Session() as db:
        saved = db.scalars(select(session.OutlookSession)).one()
        assert saved.token_hash != cookie.split("=")[1]
        assert saved.expires_at <= now() + timedelta(days=1)
    resumed = client.post(
        "/api/outlook/session/resume", headers={**HEADERS, "Cookie": cookie}
    )
    assert resumed.status_code == 200
    assert resumed.json()["team"] == "One"
    me = client.get("/api/me", headers={**HEADERS, "Cookie": cookie})
    assert me.status_code == 200 and me.json()["user_id"] == "user_one"


@pytest.mark.parametrize(
    "change",
    ["revoked", "removed", "different_user", "different_org", "expired", "pending"],
)
def test_reopen_does_not_extend_or_bypass_clerk_access(client, clerk, change):
    cookie = connect(client)
    if change == "revoked":
        clerk.live.status = "revoked"
    elif change == "removed":
        clerk.members = []
    elif change == "different_user":
        clerk.live.user_id = "user_two"
    elif change == "different_org":
        clerk.live.last_active_organization_id = "org_two"
    elif change == "expired":
        clerk.live.expire_at = int((now() - timedelta(minutes=1)).timestamp() * 1000)
    elif change == "pending":
        clerk.live.tasks = ["setup"]
    assert client.post(
        "/api/outlook/session/resume", headers={**HEADERS, "Cookie": cookie}
    ).status_code in (401, 403)
    assert client.get("/api/me", headers={**HEADERS, "Cookie": cookie}).status_code in (
        401,
        403,
    )


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": ORIGIN},
        {**HEADERS, "Sec-Fetch-Site": "cross-site"},
        {**HEADERS, "Origin": "https://attacker.example"},
        {**HEADERS, "Origin": "http://localhost:5179"},
    ],
)
def test_cookie_requires_same_origin_https_and_explicit_header(client, clerk, headers):
    cookie = connect(client)
    assert (
        client.post(
            "/api/outlook/session/resume", headers={**headers, "Cookie": cookie}
        ).status_code
        == 403
    )


def test_session_cannot_bind_without_clerk_bearer_or_forge_cookie(client, clerk):
    assert client.post("/api/outlook/session", headers=HEADERS).status_code == 401
    assert (
        client.post(
            "/api/outlook/session/resume",
            headers={**HEADERS, "Cookie": session.COOKIE + "=forged"},
        ).status_code
        == 401
    )
    cookie = connect(client)
    assert (
        client.post(
            "/api/outlook/session", headers={**HEADERS, "Cookie": cookie}
        ).status_code
        == 401
    )


def test_membership_role_is_current_and_provider_failure_fails_closed(client, clerk):
    cookie = connect(client)
    clerk.members[0].role = "org:viewer"
    response = client.post(
        "/api/projects", json={}, headers={**HEADERS, "Cookie": cookie}
    )
    assert response.status_code == 403
    clerk.unavailable = True
    assert (
        client.get("/api/me", headers={**HEADERS, "Cookie": cookie}).status_code == 503
    )


def test_disconnect_and_database_expiry(client, clerk):
    cookie = connect(client)
    clerk.live.status = "revoked"  # Even a revoked session can forget its own cookie.
    result = client.delete(
        "/api/outlook/session", headers={**HEADERS, "Cookie": cookie}
    )
    assert result.status_code == 200
    assert "Max-Age=0" in result.headers["set-cookie"]
    assert (
        client.post(
            "/api/outlook/session/resume", headers={**HEADERS, "Cookie": cookie}
        ).status_code
        == 401
    )
    clerk.live.status = "active"
    cookie = connect(client)
    with Session() as db:
        row = db.scalars(select(session.OutlookSession)).one()
        row.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert (
        client.post(
            "/api/outlook/session/resume", headers={**HEADERS, "Cookie": cookie}
        ).status_code
        == 401
    )
