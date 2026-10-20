"""Durable, opt-in Teams decision notices and a local-time morning brief."""

import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.domain.service import get, scoped
from backend.identity import Identity, identity_scope
from backend.lifecycle import service as lifecycle
from backend.storage.db import Session
from backend.storage.models import Base, Order, Row

from . import cards


class InternalNotification(Row, Base):
    __tablename__ = "internal_notifications"
    dedupe_key: Mapped[str] = mapped_column(String(180))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    error: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("organization_id", "dedupe_key"),)


def configuration():
    path = os.getenv("RIVET_INTERNAL_CHANNEL_CONFIG", "")
    return json.loads(Path(path).read_text()).get("organizations", {}) if path else {}


def queue(s, key, card):
    if not s.scalar(
        scoped(InternalNotification).where(InternalNotification.dedupe_key == key)
    ):
        s.add(InternalNotification(dedupe_key=key, payload=card))


def schedule(s, config, at=None):
    from backend.project_flow.activity import brief

    at = at or datetime.now(ZoneInfo(config.get("timezone", "America/Los_Angeles")))
    if at.hour == int(config.get("brief_hour", 8)):
        report = brief(s)
        queue(
            s,
            "daily:" + at.date().isoformat(),
            {
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {
                        "type": "TextBlock",
                        "text": "Rivet · Morning brief",
                        "size": "Large",
                        "weight": "Bolder",
                    },
                    {
                        "type": "TextBlock",
                        "text": report["text"]
                        + f" · {report['waiting_items_due']} follow-ups due",
                        "wrap": True,
                    },
                ],
                "actions": [
                    {
                        "type": "Action.OpenUrl",
                        "title": "Open Today",
                        "url": cards.base_url() + "/#today",
                    }
                ],
            },
        )
    for order in s.scalars(scoped(Order)):
        state = lifecycle._state(order)
        for p in state["packages"]:
            if (
                p["status"] != "awaiting_decision"
                or p["blockers"]
                or not lifecycle._current(s, order, state, p)
            ):
                continue
            done = {d["gate"] for d in p["decisions"]}
            for gate in set(p["gates"]) - done:
                row = cards.create(s, order, p["id"], gate)
                rendered = cards.render(s, row, "teams")
                queue(s, f"decision:{row.id}", rendered["card"])


def deliver(id, config, *, transport=None):
    with Session.begin() as s:
        row = get(s, InternalNotification, id, True)
        if row.status != "queued":
            return
        url = os.getenv(config.get("webhook_env", ""), "")
        p = urlsplit(url)
        if (
            p.scheme != "https"
            or p.hostname not in config.get("allowed_hosts", [])
            or p.username
            or p.password
        ):
            row.status = "configuration_required"
            row.error = (
                "The administrator must configure the exact Teams workflow endpoint."
            )
            return
        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": row.payload,
                }
            ],
        }
        row.status = "sending"
    # A restarted sending attempt is not blindly posted again. The card ID and
    # retained payload make a duplicate/uncertain internal notice reconcilable.
    try:
        with httpx.Client(
            timeout=20, follow_redirects=False, transport=transport
        ) as client:
            response = client.post(url, json=payload)
        accepted = 200 <= response.status_code < 300
    except httpx.HTTPError:
        accepted = False
    with Session.begin() as s:
        row = get(s, InternalNotification, id, True)
        row.status = "provider_accepted" if accepted else "needs_check"
        row.error = (
            ""
            if accepted
            else "Teams did not confirm acceptance. Check the channel before resending."
        )


def scheduled_pass():
    for org, config in configuration().items():
        if not config.get("enabled"):
            continue
        with identity_scope(Identity(org, "Rivet internal coordinator")):
            with Session.begin() as s:
                schedule(s, config)
                s.flush()
                ids = list(
                    s.scalars(
                        scoped(InternalNotification)
                        .where(InternalNotification.status == "queued")
                        .limit(10)
                    )
                )
            for row in ids:
                deliver(row.id, config)
