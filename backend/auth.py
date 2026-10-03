"""Clerk session verification and workspace authorization."""

import base64
import os
from fastapi import HTTPException
from clerk_backend_api import authenticate_request, AuthenticateRequestOptions
from backend.identity import Identity, tenant_id


def allowed_origins():
    return [
        x.strip().rstrip("/")
        for x in os.getenv(
            "RIVET_ALLOWED_ORIGINS",
            "http://127.0.0.1:5178,http://localhost:5178,http://127.0.0.1:8787,http://localhost:8787",
        ).split(",")
        if x.strip()
    ]


def publishable_key():
    return os.getenv("VITE_CLERK_PUBLISHABLE_KEY", "")


def clerk_issuer():
    key = publishable_key()
    try:
        encoded = key.split("_", 2)[2]
        host = (
            base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode().rstrip("$")
        )
        if not host or "/" in host:
            raise ValueError()
        return "https://" + host
    except (ValueError, IndexError):
        raise HTTPException(503, "Clerk configuration is incomplete.")


def verify_identity(request):
    secret = os.getenv("CLERK_SECRET_KEY")
    if not secret or not publishable_key():
        raise HTTPException(503, "Clerk configuration is incomplete.")
    if not request.headers.get("authorization", "").startswith("Bearer "):
        raise HTTPException(401, "Sign in to your Rivet workspace.")
    state = authenticate_request(
        request,
        AuthenticateRequestOptions(
            secret_key=secret,
            jwt_key=os.getenv("CLERK_JWT_KEY"),
            authorized_parties=allowed_origins(),
            accepts_token=["session_token"],
        ),
    )
    if not state.is_signed_in:
        raise HTTPException(401, "Your session has expired. Sign in again.")
    claims = state.payload or {}
    if (
        claims.get("iss") != clerk_issuer()
        or claims.get("azp") not in allowed_origins()
    ):
        raise HTTPException(401, "This session was not issued for Rivet.")
    if claims.get("sts") == "pending" or not claims.get("sub"):
        raise HTTPException(403, "Complete sign-in before opening the workspace.")
    org = claims.get("org_id") or claims.get("o", {}).get("id")
    role = claims.get("org_role") or claims.get("o", {}).get("rol", "")
    if not org:
        raise HTTPException(403, "Choose or create a team to continue.")
    role = role if role.startswith("org:") else "org:" + role
    return Identity(tenant_id(org), claims["sub"], role, org)


def authorize(request, identity):
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if identity.role not in {"org:admin", "org:member"}:
        raise HTTPException(403, "Your team role has read-only access.")
    path = request.url.path
    if (
        path.startswith("/api/integrations/") or "/inbox" in path
    ) and identity.role != "org:admin":
        raise HTTPException(403, "A team administrator must configure integrations.")
