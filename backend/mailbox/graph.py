"""Read-only Microsoft Graph transport. Secrets and provider bodies are never logged."""

import json
import os
import re
import time
from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID, uuid4

import httpx
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

GRAPH = "https://graph.microsoft.com/v1.0"
MAX_MIME_BYTES = 20 * 1024 * 1024


class GraphError(Exception):
    def __init__(self, code, message, *, retry_after=None, status=0):
        super().__init__(message)
        self.code, self.retry_after, self.status = code, retry_after, status


def mailbox_policy(organization_id, mailbox, microsoft_tenant_id):
    """Application credentials are bound to ONE Microsoft tenant and a server allowlist."""
    try:
        expected = str(UUID(os.getenv("RIVET_MS_TENANT_ID", "")))
        supplied = str(UUID(microsoft_tenant_id))
        policies = json.loads(os.getenv("RIVET_MS_MAILBOX_ALLOWLIST", "{}"))
        organization = policies.get(organization_id, {})
    except (ValueError, TypeError, AttributeError):
        raise GraphError(
            "setup_required", "Microsoft mailbox access is not configured."
        )
    address = mailbox.strip().lower()
    if supplied != expected or not re.fullmatch(
        r"[^\s@/]+@[^\s@/]+\.[^\s@/]+", address
    ):
        raise GraphError(
            "access_denied", "This Microsoft tenant or mailbox is not authorized."
        )
    policy = (
        {"update_recipient": address}
        if isinstance(organization, list) and address in organization
        else organization.get(address)
        if isinstance(organization, dict)
        else None
    )
    if not isinstance(policy, dict):
        raise GraphError(
            "access_denied", "This mailbox is not authorized for this Rivet team."
        )
    recipient = policy.get("update_recipient", address)
    if not isinstance(recipient, str) or not re.fullmatch(
        r"[^\s@/]+@[^\s@/]+\.[^\s@/]+", recipient
    ):
        raise GraphError(
            "setup_required", "Configure an internal review-update recipient."
        )
    return {
        "mailbox": address,
        "update_recipient": recipient.lower(),
        "tenant_id": expected,
    }


def configured():
    certificate, key = (
        os.getenv("RIVET_MS_CERTIFICATE_PATH"),
        os.getenv("RIVET_MS_PRIVATE_KEY_PATH"),
    )
    credential = (
        bool(certificate and key)
        if certificate or key
        else bool(os.getenv("RIVET_MS_CLIENT_SECRET"))
    )
    return credential and all(
        os.getenv(name)
        for name in (
            "RIVET_MS_TENANT_ID",
            "RIVET_MS_CLIENT_ID",
            "RIVET_MS_MAILBOX_ALLOWLIST",
        )
    )


def credential_fields(client_id, token_url):
    """Prefer a server-mounted certificate; never fall back on a broken key pair."""
    certificate_path = os.getenv("RIVET_MS_CERTIFICATE_PATH", "")
    key_path = os.getenv("RIVET_MS_PRIVATE_KEY_PATH", "")
    if certificate_path or key_path:
        try:
            if not certificate_path or not key_path:
                raise ValueError("Incomplete certificate configuration")
            certificate = x509.load_pem_x509_certificate(
                Path(certificate_path).read_bytes()
            )
            key = serialization.load_pem_private_key(
                Path(key_path).read_bytes(), password=None
            )
            timestamp = datetime.now(UTC)
            if (
                not isinstance(key, rsa.RSAPrivateKey)
                or key.key_size < 2048
                or not isinstance(certificate.public_key(), rsa.RSAPublicKey)
                or key.public_key().public_numbers()
                != certificate.public_key().public_numbers()
                or not certificate.not_valid_before_utc
                <= timestamp
                < certificate.not_valid_after_utc
            ):
                raise ValueError("Invalid certificate key pair")
            issued = int(timestamp.timestamp())
            assertion = jwt.encode(
                {
                    "aud": token_url,
                    "iss": client_id,
                    "sub": client_id,
                    "iat": issued,
                    "nbf": issued,
                    "exp": issued + 300,
                    "jti": str(uuid4()),
                },
                key,
                algorithm="PS256",
                headers={
                    "x5t#S256": urlsafe_b64encode(
                        certificate.fingerprint(hashes.SHA256())
                    )
                    .rstrip(b"=")
                    .decode("ascii")
                },
            )
            return {
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion,
            }
        except (OSError, ValueError, TypeError, jwt.PyJWTError):
            raise GraphError(
                "setup_required",
                "Microsoft application certificate is missing, invalid, or expired.",
            ) from None
    secret = os.getenv("RIVET_MS_CLIENT_SECRET", "")
    if not secret:
        raise GraphError(
            "setup_required", "Microsoft application credentials are not configured."
        )
    return {"client_secret": secret}


def initial_delta_url(mailbox, folder_id):
    return (
        f"{GRAPH}/users/{quote(mailbox, safe='')}/mailFolders/{quote(folder_id, safe='')}/messages/delta"
        "?$select=id,internetMessageId,subject,from,receivedDateTime,conversationId,hasAttachments,isDraft,webLink"
    )


def validate_delta_url(url, mailbox, folder_id):
    """Only follow an opaque Graph cursor for this exact mailbox and folder.

    Do not follow redirects or Microsoft Location headers on reset. Restart from
    our own initial query. Never let a stored cursor exfiltrate the bearer token.
    """
    parsed = urlsplit(url)
    valid_host = (
        parsed.scheme == "https"
        and parsed.netloc == "graph.microsoft.com"
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )
    # Both slash and OData parenthesis forms (including mixed notation) are
    # documented by Graph. Resource identifiers remain case-sensitive.
    match = re.fullmatch(
        r"/v1\.0/(?:users/([^/]+)|users\('([^']+)'\))/"
        r"(?:mailfolders/([^/]+)|mailfolders\('([^']+)'\))/messages/delta",
        parsed.path,
        re.IGNORECASE,
    )
    valid_path = bool(
        match
        and unquote(match[1] or match[2]).lower() == mailbox.lower()
        and unquote(match[3] or match[4]) == folder_id
    )
    if not valid_host or not valid_path:
        raise GraphError(
            "invalid_cursor",
            "Microsoft returned an unexpected synchronization cursor. Review the connection.",
        )
    return url


def retry_seconds(value):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        try:
            return max(
                1,
                int((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()),
            )
        except (TypeError, ValueError, OverflowError):
            return None


def response_error(response):
    try:
        code = str(response.json().get("error", {}).get("code", ""))
    except (ValueError, AttributeError):
        code = ""
    status = response.status_code
    if status == 410 or code.lower() in {
        "syncstatenotfound",
        "resyncrequired",
        "invaliddeltatoken",
    }:
        return GraphError(
            "cursor_expired",
            "Microsoft requires a fresh folder reconciliation.",
            status=status,
        )
    if status == 429 or status >= 500:
        return GraphError(
            "retry",
            "Microsoft mailbox service is temporarily unavailable; retry is scheduled.",
            retry_after=retry_seconds(response.headers.get("Retry-After")),
            status=status,
        )
    if status in (401, 403):
        return GraphError(
            "access_denied",
            "Microsoft rejected access. An administrator must check consent and mailbox permissions.",
            status=status,
        )
    if status == 404:
        return GraphError(
            "not_found",
            "The Microsoft message or folder is unavailable. Review retention or mailbox scope.",
            status=status,
        )
    return GraphError(
        "provider_error",
        f"Microsoft mailbox request failed (HTTP {status}).",
        status=status,
    )


class GraphClient:
    def __init__(
        self, organization_id, mailbox, microsoft_tenant_id, *, transport=None
    ):
        self.policy = mailbox_policy(organization_id, mailbox, microsoft_tenant_id)
        self.mailbox = self.policy["mailbox"]
        self.client = httpx.Client(
            timeout=30, follow_redirects=False, transport=transport
        )
        self.token = ""
        self.expires_at = 0

    def close(self):
        self.client.close()

    def headers(self):
        if not self.token or time.monotonic() >= self.expires_at:
            client_id = os.getenv("RIVET_MS_CLIENT_ID", "")
            if not client_id:
                raise GraphError(
                    "setup_required",
                    "Microsoft application credentials are not configured.",
                )
            token_url = f"https://login.microsoftonline.com/{self.policy['tenant_id']}/oauth2/v2.0/token"
            credentials = credential_fields(client_id, token_url)
            try:
                response = self.client.post(
                    token_url,
                    data={
                        "client_id": client_id,
                        **credentials,
                        "scope": "https://graph.microsoft.com/.default",
                        "grant_type": "client_credentials",
                    },
                )
                if not response.is_success:
                    raise response_error(response)
                body = response.json()
                token = body.get("access_token")
                if not isinstance(token, str) or not token:
                    raise GraphError(
                        "setup_required",
                        "Microsoft did not issue an application access token.",
                    )
                self.token = token
                self.expires_at = time.monotonic() + max(
                    1, int(body.get("expires_in", 3600)) - 60
                )
            except httpx.HTTPError:
                raise GraphError(
                    "retry",
                    "Microsoft authentication could not be reached; retry is scheduled.",
                )
            except (ValueError, TypeError):
                raise GraphError(
                    "provider_error",
                    "Microsoft returned an invalid authentication response.",
                )
        return {
            "Authorization": "Bearer " + self.token,
            "Prefer": 'IdType="ImmutableId", odata.maxpagesize=100',
        }

    def delta(self, folder_id, cursor=""):
        url = validate_delta_url(
            cursor or initial_delta_url(self.mailbox, folder_id),
            self.mailbox,
            folder_id,
        )
        try:
            response = self.client.get(url, headers=self.headers())
            if not response.is_success:
                raise response_error(response)
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("value"), list):
                raise TypeError()
            next_link, delta_link = (
                body.get("@odata.nextLink"),
                body.get("@odata.deltaLink"),
            )
            if bool(next_link) == bool(delta_link):
                raise ValueError()
            validate_delta_url(next_link or delta_link, self.mailbox, folder_id)
            return body
        except httpx.HTTPError:
            raise GraphError(
                "retry",
                "Microsoft mailbox service could not be reached; retry is scheduled.",
            )
        except (ValueError, TypeError):
            raise GraphError(
                "provider_error",
                "Microsoft returned an incomplete synchronization page; the checkpoint was not advanced.",
            )

    def resolve_folder(self, folder_id):
        """Resolve well-known names before storing a cursor bound to the actual ID."""
        url = f"{GRAPH}/users/{quote(self.mailbox, safe='')}/mailFolders/{quote(folder_id, safe='')}?$select=id"
        try:
            response = self.client.get(url, headers=self.headers())
            if not response.is_success:
                raise response_error(response)
            identifier = response.json().get("id")
            if not isinstance(identifier, str) or not 0 < len(identifier) <= 512:
                raise ValueError()
            return identifier
        except httpx.HTTPError:
            raise GraphError(
                "retry",
                "Microsoft could not resolve the monitored folder; retry is scheduled.",
            )
        except (ValueError, AttributeError):
            raise GraphError(
                "provider_error", "Microsoft did not return a valid mailbox folder ID."
            )

    def mime(self, message_id):
        url = f"{GRAPH}/users/{quote(self.mailbox, safe='')}/messages/{quote(message_id, safe='')}/$value"
        try:
            with self.client.stream("GET", url, headers=self.headers()) as response:
                if not response.is_success:
                    response.read()
                    raise response_error(response)
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_MIME_BYTES:
                        raise GraphError(
                            "too_large",
                            "This email exceeds the 20 MB intake limit. Import the relevant documents separately.",
                        )
                if not payload:
                    raise GraphError(
                        "provider_error",
                        "Microsoft returned an empty email. Retry or inspect the original.",
                    )
                return bytes(payload)
        except httpx.HTTPError:
            raise GraphError(
                "retry",
                "Microsoft could not return the original email; retry is scheduled.",
            )
