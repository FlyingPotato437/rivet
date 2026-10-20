"""Entra tokens plus explicit person mappings; never trust a card's actor field."""

import json
import os
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import HTTPException

from backend.identity import Identity


@lru_cache(maxsize=16)
def keys(tenant):
    return jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
        cache_keys=True,
    )


def verify(request):
    audience = os.getenv("RIVET_ACTIONABLE_AUDIENCE", "")
    mapping = json.loads(os.getenv("RIVET_ACTIONABLE_IDENTITIES", "{}"))
    if not audience or not mapping:
        raise HTTPException(503, "Actionable messages are not registered and enabled.")
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        tenant = str(UUID(unverified["tid"]))
        if tenant not in {k.split(":")[0] for k in mapping}:
            raise ValueError()
        claims = jwt.decode(
            token,
            keys(tenant).get_signing_key_from_jwt(token).key,
            algorithms=["RS256"],
            audience=audience,
            issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
            options={"require": ["exp", "iat", "iss", "aud", "tid", "oid"]},
        )
        if "access_as_user" not in claims.get("scp", "").split():
            raise ValueError()
        person = mapping[tenant + ":" + claims["oid"]]
        return Identity(person["organization_id"], person["user_id"], "org:member")
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise HTTPException(
            401, "Microsoft identity could not be verified for this decision."
        ) from None
