"""Resend transport. Provider secrets and email content never go into logs."""

import os
from urllib.parse import urlparse
import httpx
from fastapi import HTTPException


def request(method, path, *, body=None, key=None):
    token = os.getenv("RESEND_API_KEY", "")
    if not token:
        raise HTTPException(503, "Resend is not configured.")
    headers = {"Authorization": "Bearer " + token}
    if key:
        headers["Idempotency-Key"] = key
    try:
        response = httpx.request(
            method,
            "https://api.resend.com" + path,
            headers=headers,
            json=body,
            timeout=30,
        )
    except httpx.HTTPError:
        raise HTTPException(502, "Resend could not be reached. Try again later.")
    if not response.is_success:
        if response.status_code in (401, 403):
            raise HTTPException(
                503,
                "Resend rejected the key or its permissions. Forwarding requires a Full access key.",
            )
        if response.status_code == 429:
            raise HTTPException(
                503, "Resend is rate limiting requests. Try again shortly."
            )
        raise HTTPException(
            502,
            f"Resend could not complete this request (HTTP {response.status_code}).",
        )
    return response.json()


def status():
    return {
        "configured": bool(os.getenv("RESEND_API_KEY")),
        "sending_ready": bool(
            os.getenv("RESEND_API_KEY") and os.getenv("RIVET_EMAIL_FROM")
        ),
        "receiving_domain": os.getenv("RESEND_RECEIVING_DOMAIN", ""),
        "sender": os.getenv("RIVET_EMAIL_FROM", ""),
        "webhook_configured": bool(os.getenv("RESEND_WEBHOOK_SECRET")),
        "local_only": os.getenv("RIVET_LOCAL_ONLY", "true") == "true",
    }


def raw_email(email_id):
    data = request("GET", "/emails/receiving/" + email_id)
    url = (data.get("raw") or {}).get("download_url", "")
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or not any(
            host.endswith("." + suffix) or host == suffix
            for suffix in ("cloudfront.net", "resend.com")
        )
    ):
        raise HTTPException(
            502, "Resend did not provide a supported original-email download URL."
        )
    content = bytearray()
    try:
        with httpx.stream("GET", url, timeout=30, follow_redirects=False) as response:
            if response.status_code != 200:
                raise HTTPException(502, "The original email could not be downloaded.")
            for part in response.iter_bytes():
                content.extend(part)
                if len(content) > 20 * 1024 * 1024:
                    raise HTTPException(
                        422, "The forwarded email exceeds the 20 MB limit."
                    )
    except httpx.HTTPError:
        raise HTTPException(502, "The original email could not be downloaded.")
    return data, bytes(content)
