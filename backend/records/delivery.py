"""Reviewable inbox brief. Building or downloading it never sends an email."""

import os
from email import policy
from email.message import EmailMessage
from html import escape
from urllib.parse import quote, urlencode, urlsplit

from fastapi import APIRouter, Query
from fastapi.responses import Response

from backend import mail
from backend.config import allowed_origins
from backend.domain.service import fail, scoped
from backend.orders.service import lock_order
from backend.storage.db import Session
from backend.storage.models import OrderInbox, TeamInbox

from . import service

router = APIRouter(prefix="/api", tags=["Inbox delivery"])

FLAG_LABELS = {
    "new": "New comments",
    "repeated": "Repeated comments",
    "unresolved": "Previously open comments",
    "late_change": "After an approved record",
    "scope_change": "Possible scope changes",
}


def workspace_url():
    """Only server-configured origins can become links in an exported message."""
    candidates = [os.getenv("RIVET_WORKSPACE_URL", "")]
    if not candidates[0]:
        # Public marketing origins can be allowed for CORS without hosting a
        # workspace. Hosted briefs require an explicit private workspace URL.
        candidates = allowed_origins() if mail.status()["local_only"] else []
    for candidate in candidates:
        value = candidate.strip().rstrip("/")
        try:
            p = urlsplit(value)
            _ = p.port
        except ValueError:
            continue
        if (
            p.hostname
            and not p.username
            and not p.password
            and not p.path
            and not p.query
            and not p.fragment
            and not any(c.isspace() for c in p.netloc)
            and not any(c in value for c in "\r\n\t")
            and (
                p.scheme == "https"
                or (
                    p.scheme == "http"
                    and p.hostname in {"localhost", "127.0.0.1", "::1"}
                    and mail.status()["local_only"]
                )
            )
        ):
            return value
    return ""


def order_link(base, order_id, comment_id=""):
    if not base:
        return ""
    query = {"tab": "comments"}
    if comment_id:
        query["comment"] = comment_id
    return f"{base}/#order/{quote(str(order_id), safe='')}?{urlencode(query)}"


def digest(w, base=""):
    review = w.get("revision_review", {})
    latest = review.get("latest_revision")
    comments = [
        c
        for c in w["comments"]
        if not latest or (c.get("revision") or "Unspecified") == latest
    ]
    comment_ids = {c["id"] for c in w["comments"]}
    # Review projections are server-owned, but a stale/foreign reference must
    # never manufacture an actionable link in an exported message.
    flags = [f for f in review.get("flags", []) if f.get("comment_id") in comment_ids]
    counts = {key: sum(f.get("kind") == key for f in flags) for key in FLAG_LABELS}
    open_comments = [c for c in comments if c.get("status") != "closed"]
    needs_response = [c for c in open_comments if not c.get("response", "").strip()]
    needs_review = [c for c in comments if not c.get("reviewed")]
    number = " ".join(str(w["order"]["number"]).split())
    revision = review.get("latest_revision") or next(
        (c.get("revision") for c in reversed(comments) if c.get("revision")), ""
    )
    subject = (
        f"{number}: {('Rev ' + str(revision) + ' · ') if revision else ''}review brief"
    )
    subject = " ".join(subject.split())[:220]
    overview = (
        f"{len(comments)} comments · {len(needs_response)} need a response · "
        f"{len(needs_review)} need source review"
    )
    link = order_link(base, w["order"]["id"])
    by_id = {c["id"]: c for c in w["comments"]}
    items = []
    for flag in flags:
        if flag.get("kind") == "new":
            continue
        comment = by_id[flag["comment_id"]]
        items.append(
            {
                "id": flag["id"],
                "label": FLAG_LABELS.get(flag["kind"], "Needs review"),
                "title": flag["title"],
                "detail": flag["detail"],
                "comment_id": comment["id"],
                "comment_number": comment.get("number", ""),
                "source_page": comment.get("source_page"),
                "url": order_link(base, w["order"]["id"], comment["id"]),
                "origin": "automatic",
            }
        )
    # Stable order: the commercial and approval checks are easiest to miss.
    priority = {
        "Possible scope changes": 0,
        "After an approved record": 1,
        "Previously open comments": 2,
        "Repeated comments": 3,
    }
    items.sort(
        key=lambda item: (priority.get(item["label"], 4), item["comment_number"])
    )
    lines = [
        subject,
        w["order"]["title"],
        "",
        overview,
        "",
        "Prepared from the saved record. Review flags before taking action.",
        "A flag is a review cue, not an engineering or commercial determination.",
        "",
    ]
    for key, label in FLAG_LABELS.items():
        lines.append(f"{label}: {counts[key]}")
    if items:
        lines.extend(["", "Items to review"])
        for item in items:
            page = (
                f" · source page {item['source_page']}" if item["source_page"] else ""
            )
            lines.extend(
                [
                    f"- {item['label']} · comment {item['comment_number']}{page}",
                    f"  {item['title']}",
                    f"  {item['detail']}",
                ]
            )
            if item["url"]:
                lines.append(f"  {item['url']}")
    if link:
        lines.extend(["", "Open the comment log (team sign-in required):", link])
    else:
        lines.extend(
            ["", "Open this order in Rivet. A workspace URL is not configured."]
        )
    lines.extend(
        [
            "",
            "Review and send this draft from your own email app.",
            "No message was sent. This working brief is not an approval record.",
        ]
    )
    text = "\n".join(lines)
    item_html = "".join(
        "<li><strong>"
        + escape(item["label"])
        + " · comment "
        + escape(str(item["comment_number"]))
        + "</strong><p>"
        + escape(item["title"])
        + "</p><p>"
        + escape(item["detail"])
        + "</p>"
        + (
            f'<a href="{escape(item["url"], quote=True)}">Review comment</a>'
            if item["url"]
            else ""
        )
        + "</li>"
        for item in items
    )
    html = (
        '<!doctype html><html><body style="font-family:Arial,sans-serif;line-height:1.6;color:#23212a">'
        f"<h1>{escape(subject)}</h1><p>{escape(w['order']['title'])}</p>"
        f"<p><strong>{escape(overview)}</strong></p>"
        "<p>Prepared from the saved record. Review flags before taking action. "
        "A flag is a review cue, not an engineering or commercial determination.</p>"
        + "<ul>"
        + "".join(
            f"<li>{escape(label)}: {counts[key]}</li>"
            for key, label in FLAG_LABELS.items()
        )
        + "</ul>"
        + ("<h2>Items to review</h2><ul>" + item_html + "</ul>" if items else "")
        + (
            f'<p><a href="{escape(link, quote=True)}">Open comment log</a> · Team sign-in required</p>'
            if link
            else "<p>A workspace URL is not configured.</p>"
        )
        + "<p>Review and send this draft from your own email app. No message was sent. "
        "This working brief is not an approval record.</p></body></html>"
    )
    return {
        "subject": subject,
        "summary": overview,
        "text": text,
        "html": html,
        "revision": revision,
        "version": w["version"],
        "counts": counts,
        "comment_count": len(comments),
        "needs_response": len(needs_response),
        "needs_review": len(needs_review),
        "items": items,
        "order_url": link,
        "sent": False,
        "origin": "automatic",
        "requires_review": True,
    }


def email_bytes(preview):
    message = EmailMessage(policy=policy.SMTP)
    message["Subject"] = preview["subject"]
    # Open as a draft. Deliberately no recipients, sender, or send operation.
    message["X-Unsent"] = "1"
    message.set_content(preview["text"])
    message.add_alternative(preview["html"], subtype="html")
    return message.as_bytes()


def get_digest(id):
    with Session.begin() as s:
        return digest(service.view(s, lock_order(s, id)), workspace_url())


@router.get("/orders/{id}/record/digest")
def preview_digest(id: str):
    return get_digest(id)


@router.get("/orders/{id}/record/digest.eml")
def download_digest(id: str, expected_version: int = Query(ge=1)):
    preview = get_digest(id)
    if preview["version"] != expected_version:
        fail("The order changed. Refresh the brief before downloading it.", 409)
    return Response(
        email_bytes(preview),
        media_type="message/rfc822",
        headers={
            "Content-Disposition": 'attachment; filename="rivet-review-brief.eml"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/integrations/adoption")
def adoption_status():
    state = mail.status()
    with Session() as s:
        aliases = list(s.scalars(scoped(OrderInbox).where(OrderInbox.enabled == True)))
        team = s.scalar(scoped(TeamInbox).where(TeamInbox.enabled == True))
    receiving = bool(state["configured"] and state["receiving_domain"])
    delivery = bool(
        receiving and state["webhook_configured"] and not state["local_only"]
    )
    return {
        "forwarding": {
            "status": "configured" if receiving else "setup_needed",
            "automatic_delivery_configured": delivery,
            "address_count": len(aliases),
            "address_scope": "team_and_order",
            "team_address": team.address if team else "",
            "team_address_enabled": bool(team),
            "detail": "Team forwarding matches incoming job identifiers and queues uncertain files for confirmation. Provider access and DNS must be verified during setup.",
        },
        "integrations": [
            {
                "id": "uploads",
                "name": "PDF and saved email",
                "status": "available",
                "detail": "Upload returned drawings and .eml files. Review extracted comments against their source.",
            },
            {
                "id": "excel",
                "name": "Excel",
                "status": "export_available",
                "detail": "Download the comment log as an .xlsx file. There is no live workbook connection.",
            },
            {
                "id": "outlook",
                "name": "Outlook",
                "status": "not_connected",
                "detail": "Forward email or import a saved .eml file. Automatic shared-mailbox sync is not connected.",
            },
            {
                "id": "sharepoint",
                "name": "SharePoint",
                "status": "not_connected",
                "detail": "Upload a downloaded file. Rivet does not currently browse or write to SharePoint.",
            },
            {
                "id": "sap",
                "name": "SAP",
                "status": "not_connected",
                "detail": "Order identifiers can be entered at intake. Rivet does not read from or update SAP.",
            },
        ],
        "outbound": {
            "status": "drafts_available",
            "automatic_sending": False,
            "detail": "Preview and download review briefs. Sending through Rivet requires a separate, explicit review and send action.",
        },
    }
