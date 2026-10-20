"""A bounded client boundary. Current configuration deliberately permits local HTTP only."""

import json
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from backend.domain.service import digest, fail

from .schemas import Command


def intent_hash(command):
    # A retry reconciles the same action even if the remote version has advanced.
    return digest(
        command.model_dump(
            mode="json",
            exclude={
                "expected_remote_version",
                "order_version",
                "requested_by",
                "reason",
            },
        )
    )


@dataclass(frozen=True)
class Configuration:
    base_url: str
    token: str = field(repr=False)
    organization_ids: frozenset[str]
    buyer_ids: dict[str, list[str]]
    timeout: float = 5

    def __post_init__(self):
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or not parsed.port
            or parsed.path not in {"", "/"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "The action harness requires an explicit loopback HTTP origin."
            )
        if len(self.token) < 24 or not self.organization_ids:
            raise ValueError(
                "Configure a local token and explicit organization allowlist."
            )
        for value in self.organization_ids:
            UUID(value)
        if not isinstance(self.buyer_ids, dict) or any(
            key not in self.organization_ids
            or not isinstance(values, list)
            or any(
                not isinstance(value, str) or not value or len(value) > 200
                for value in values
            )
            for key, values in self.buyer_ids.items()
        ):
            raise ValueError(
                "Buyer identities must be exact, server-configured IDs for an enabled organization."
            )


def configuration():
    try:
        return Configuration(
            os.environ["RIVET_LOCAL_ACTIONS_URL"].rstrip("/"),
            os.environ["RIVET_LOCAL_ACTIONS_TOKEN"],
            frozenset(
                str(UUID(value.strip()))
                for value in os.environ["RIVET_LOCAL_ACTIONS_ORGANIZATIONS"].split(",")
                if value.strip()
            ),
            json.loads(os.getenv("RIVET_LOCAL_ACTIONS_BUYERS", "{}")),
        )
    except (KeyError, ValueError, TypeError):
        fail("Local enterprise action execution is not configured.", 409)


class DeliveryError(Exception):
    def __init__(self, message, *, retryable):
        super().__init__(message)
        self.retryable = retryable


class LocalClient:
    def __init__(self, config):
        self.config = config

    def request(self, method, path, payload=None, *, missing_ok=False):
        try:
            with httpx.Client(
                timeout=self.config.timeout, follow_redirects=False, trust_env=False
            ) as client:
                response = client.request(
                    method,
                    self.config.base_url + path,
                    headers={"Authorization": "Bearer " + self.config.token},
                    json=payload,
                )
        except httpx.TransportError as exc:
            raise DeliveryError(
                "The local service did not return a conclusive receipt.", retryable=True
            ) from exc
        if missing_ok and response.status_code == 404:
            return None
        if response.status_code != 200:
            raise DeliveryError(
                f"Local service rejected the action (HTTP {response.status_code}); its prior receipts remain valid.",
                retryable=response.status_code >= 500,
            )
        try:
            value = response.json()
        except ValueError as exc:
            raise DeliveryError(
                "The local service returned an unreadable receipt.", retryable=True
            ) from exc
        if value.get("mode") != "local_http_demo":
            raise DeliveryError(
                "The response is not from the configured local simulator.",
                retryable=False,
            )
        return value

    def state(self, organization_id, order_id):
        value = self.request("GET", f"/v1/state/{organization_id}/{order_id}")
        if value.get("organization_id") != str(organization_id) or value.get(
            "order_id"
        ) != str(order_id):
            raise DeliveryError(
                "The local state belongs to another order or organization.",
                retryable=False,
            )
        return value

    def receipt(self, organization_id, order_id, action_id):
        return self.request(
            "GET",
            f"/v1/actions/{organization_id}/{order_id}/{action_id}",
            missing_ok=True,
        )

    def deliver(self, command: Command, action_id):
        existing = self.receipt(command.organization_id, command.order_id, action_id)
        if existing:
            return self._verify(command, action_id, existing), True
        try:
            receipt = self.request(
                "POST", f"/v1/actions/{action_id}", command.model_dump(mode="json")
            )
        except DeliveryError as exc:
            if not exc.retryable:
                raise
            # A lost response is not evidence that a write failed. Reconcile by
            # stable action identity before any retry can create another effect.
            receipt = self.receipt(command.organization_id, command.order_id, action_id)
            if not receipt:
                raise
            return self._verify(command, action_id, receipt), True
        return self._verify(command, action_id, receipt), False

    def _verify(self, command, action_id, receipt):
        if (
            receipt.get("action_id") != action_id
            or receipt.get("organization_id") != str(command.organization_id)
            or receipt.get("order_id") != str(command.order_id)
            or receipt.get("intent_sha256") != intent_hash(command)
            or receipt.get("package_fingerprint") != command.package_fingerprint
            or receipt.get("status") != "applied_local_simulation"
        ):
            raise DeliveryError(
                "The action receipt does not match the approved intent.",
                retryable=False,
            )
        return receipt
