"""An Outlook-partitioned session, anchored to a live Clerk session and membership.

The browser holds an opaque HttpOnly cookie, never a Clerk token in local storage.
Every request rechecks Clerk so signing out/revoking access also revokes this session.
Only the same-origin HTTPS task pane may use this authentication mechanism.
"""

import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from clerk_backend_api import Clerk
from clerk_backend_api.models import SDKError
from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import DateTime, String, delete
from sqlalchemy.orm import Mapped, mapped_column

from backend.config import allowed_origins
from backend.identity import Identity, auth_mode, current_identity, tenant_id
from backend.storage.db import Session
from backend.storage.models import Base, now

COOKIE = "__Host-rivet_outlook"
router = APIRouter(prefix="/api/outlook/session")


class OutlookSession(Base):
    __tablename__ = "outlook_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    clerk_session_id: Mapped[str] = mapped_column(String(180))
    user_id: Mapped[str] = mapped_column(String(180))
    clerk_org_id: Mapped[str] = mapped_column(String(180))
    issuer: Mapped[str] = mapped_column(String(256))
    origin: Mapped[str] = mapped_column(String(256))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def browser_origin(request):
    """Custom header + same-origin fetch metadata prevent ambient-cookie CSRF."""
    if auth_mode() != "clerk":
        raise HTTPException(401, "Sign in to Rivet first.")
    if request.headers.get("x-rivet-session") != "outlook":
        raise HTTPException(403, "Missing Outlook session header.")
    if request.headers.get("sec-fetch-site") != "same-origin":
        raise HTTPException(403, "Outlook session requires a same-origin request.")
    origin = request.headers.get("origin")
    if not origin and request.method in {"GET", "HEAD"}:
        ref = urlsplit(request.headers.get("referer", ""))
        origin = f"{ref.scheme}://{ref.netloc}"
    if (
        not origin
        or origin not in allowed_origins()
        or not origin.startswith("https://")
    ):
        raise HTTPException(403, "Outlook session requires an approved HTTPS origin.")
    return origin


def live_account(session_id, user_id, org_id):
    """No cached authority: revoked sessions and removed members stop immediately."""
    try:
        with Clerk(
            bearer_auth=os.environ["CLERK_SECRET_KEY"], timeout_ms=8000
        ) as clerk:
            session = clerk.sessions.get(session_id=session_id)
            expires = datetime.fromtimestamp(session.expire_at / 1000, UTC)
            if (
                session.status != "active"
                or session.user_id != user_id
                or expires <= now()
            ):
                raise HTTPException(401, "Your session expired. Sign in again.")
            if (
                session.last_active_organization_id
                and session.last_active_organization_id != org_id
            ):
                raise HTTPException(
                    401, "Your team changed. Reconnect Rivet in Outlook."
                )
            if session.tasks:
                raise HTTPException(403, "Complete your account setup in Rivet.")
            # Bound pagination; a membership outside this bound fails closed.
            member = None
            for offset in range(0, 1000, 100):
                batch = clerk.users.get_organization_memberships(
                    user_id=user_id, limit=100, offset=offset
                ).data
                member = next((m for m in batch if m.organization.id == org_id), None)
                if member or len(batch) < 100:
                    break
            if not member:
                raise HTTPException(403, "You no longer have access to this team.")
            user = member.public_user_data
            name = " ".join(
                filter(
                    None,
                    [
                        getattr(user, "first_name", None),
                        getattr(user, "last_name", None),
                    ],
                )
            )
            return (
                Identity(tenant_id(org_id), user_id, member.role, org_id),
                {
                    "mode": "clerk",
                    "userId": user_id,
                    "name": name or "Team member",
                    "team": member.organization.name,
                    "role": member.role,
                },
                expires,
            )
    except HTTPException:
        raise
    except (SDKError, httpx.HTTPError, ValueError, KeyError, OSError) as exc:
        # Do not log provider responses or credentials. A deleted session is unauthenticated;
        # an unavailable provider must not become an accidental auth bypass.
        if getattr(exc, "status_code", None) in {401, 404}:
            raise HTTPException(401, "Your session expired. Sign in again.") from None
        raise HTTPException(
            503, "Could not verify your session. Try again shortly."
        ) from None


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def resolve(request):
    from backend.auth import clerk_issuer

    origin = browser_origin(request)
    value = request.cookies.get(COOKIE, "")
    if not value or len(value) > 200:
        raise HTTPException(401, "Sign in to Rivet first.")
    with Session() as db:
        record = db.get(OutlookSession, token_hash(value))
        if (
            not record
            or record.expires_at <= now()
            or record.issuer != clerk_issuer()
            or record.origin != origin
        ):
            raise HTTPException(401, "Your Outlook session expired. Sign in again.")
        return live_account(
            record.clerk_session_id, record.user_id, record.clerk_org_id
        )


def verify_identity(request):
    return resolve(request)[0]


@router.post("")
def connect(request: Request, response: Response):
    from backend.auth import clerk_issuer

    origin = browser_origin(request)
    # Binding must be authorized by a verified Clerk bearer, never another cookie.
    claims = getattr(request.state, "clerk_claims", {})
    sid = claims.get("sid")
    if not sid:
        raise HTTPException(401, "Sign in to Rivet before connecting Outlook.")
    identity = current_identity()
    _, account, clerk_expiry = live_account(
        sid, identity.user_id, identity.clerk_org_id
    )
    expiry = min(now() + timedelta(days=7), clerk_expiry)
    value = secrets.token_urlsafe(48)
    with Session() as db:
        db.execute(delete(OutlookSession).where(OutlookSession.expires_at <= now()))
        old = request.cookies.get(COOKIE)
        if old:
            db.execute(
                delete(OutlookSession).where(
                    OutlookSession.token_hash == token_hash(old)
                )
            )
        db.add(
            OutlookSession(
                token_hash=token_hash(value),
                clerk_session_id=sid,
                user_id=identity.user_id,
                clerk_org_id=identity.clerk_org_id,
                issuer=clerk_issuer(),
                origin=origin,
                expires_at=expiry,
            )
        )
        db.commit()
    # Appended explicitly for Python versions whose SimpleCookie does not know Partitioned.
    response.headers.append(
        "Set-Cookie",
        f"{COOKIE}={value}; Path=/; Max-Age={max(1, int((expiry - now()).total_seconds()))}; Secure; HttpOnly; SameSite=None; Partitioned",
    )
    return account


@router.post("/resume")
def resume(request: Request):
    return resolve(request)[1]


@router.delete("")
def disconnect(request: Request, response: Response):
    browser_origin(request)
    with Session() as db:
        db.execute(
            delete(OutlookSession).where(
                OutlookSession.token_hash == token_hash(request.cookies.get(COOKIE, ""))
            )
        )
        db.commit()
    response.headers.append(
        "Set-Cookie",
        f"{COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=None; Partitioned",
    )
    return {"disconnected": True}
