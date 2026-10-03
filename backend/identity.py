"""Request and job identity. Never derive a tenant from browser-supplied IDs."""

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

LOCAL_ORG = "9f0e7bbc-7fd1-43ca-b552-dae668fc82e1"


@dataclass(frozen=True)
class Identity:
    organization_id: str
    user_id: str
    role: str = "org:admin"
    clerk_org_id: str = ""


_identity: ContextVar[Identity | None] = ContextVar("rivet_identity", default=None)


def auth_mode():
    return os.getenv("RIVET_AUTH_MODE") or (
        "clerk"
        if os.getenv("CLERK_SECRET_KEY") or os.getenv("VITE_CLERK_PUBLISHABLE_KEY")
        else "local"
    )


def tenant_id(clerk_org_id):
    return str(uuid5(NAMESPACE_URL, "https://rivet.local/clerk/" + clerk_org_id))


def current_identity():
    identity = _identity.get()
    if identity:
        return identity
    if auth_mode() == "local" and os.getenv("RIVET_LOCAL_ONLY", "true") == "true":
        return Identity(LOCAL_ORG, "Local estimator")
    raise RuntimeError("An authenticated workspace or job identity is required.")


def current_org():
    return current_identity().organization_id


def current_actor():
    return current_identity().user_id


@contextmanager
def identity_scope(identity):
    token = _identity.set(identity)
    try:
        yield identity
    finally:
        _identity.reset(token)
