"""Separate mailbox capture from order work. No mail is deleted or marked read.

An explicit order/thread link wins over bulk-mail hints. Without such a link,
routine account and marketing mail stays out of review, even if it mentions
equipment. This is a reversible, explainable filter, not an accuracy guarantee.
Originals outside that workflow remain available in capture history.
"""

import re
from email import policy
from email.parser import BytesParser
from pathlib import Path

from .intake_preview import email_preview

VERSION = 2


def classify(payload, detected, candidates):
    def result(relevant, reason):
        return {"version": VERSION, "relevant": relevant, "reason": reason}

    if candidates:
        return result(True, "An existing order or drawing conversation was found.")
    preview = email_preview(payload)
    subject = preview["subject"]
    body = preview["body"]
    filenames = [a["name"] for a in preview["attachments"]]
    content = "\n".join([subject, body, *filenames])
    message = BytesParser(policy=policy.default).parsebytes(payload)
    routine = re.search(
        r"\b(password|verification code|security code|sign[- ]in|account activity|subscription|billing|payment (?:receipt|confirmation)|thanks for (?:shopping|your (?:order|purchase))|welcome to microsoft|newsletter|unsubscribe|register for (?:our|the) webinar)\b",
        subject,
        re.IGNORECASE,
    )
    # A labeled job in a direct message may be a new customer order. A mailing
    # list mentioning a job/year is not sufficient to open a PM task.
    if message.get("List-Unsubscribe") or (routine and not detected.get("job_numbers")):
        return result(
            False,
            "Routine account, billing, or mailing-list message with no existing order link.",
        )
    if detected.get("job_numbers") or detected.get("po_numbers"):
        return {
            "version": VERSION,
            "relevant": True,
            "reason": "An order identifier or existing order conversation was found.",
        }
    # These are workflow cues, not technical conclusions or customer approval.
    equipment = re.search(
        r"\b(switchgear|switchboard|breaker|transformer|busbar|bus duct|control panel|cable entry|trip unit|single[- ]line|electrical equipment)\b",
        content,
        re.IGNORECASE,
    )
    review = re.search(
        r"\b(submittals?|resubmittal|revise and resubmit|approved as noted|returned (?:drawings?|comments?|markups?)|drawing (?:review|revision|comments?)|redlines?|mark(?:ed)?[- ]?ups?|shop drawings?)\b",
        content,
        re.IGNORECASE,
    )
    if equipment or review or detected.get("drawing_numbers"):
        return {
            "version": VERSION,
            "relevant": True,
            "reason": "The message references equipment, drawing review, or returned comments.",
        }
    plausible_attachment = any(
        Path(name).suffix.lower() in {".dwg", ".dxf"}
        or (
            Path(name).suffix.lower() in {".pdf", ".xlsx", ".xls"}
            and re.search(
                r"\b(drawings?|specifications?|submittals?|comments?|markups?|redlines?|equipment|scope|approval|rev(?:ision)?[ _.-]?[A-Z0-9]+)\b",
                name.replace("_", " "),
                re.IGNORECASE,
            )
        )
        for name in filenames
    )
    if plausible_attachment:
        return {
            "version": VERSION,
            "relevant": True,
            "reason": "A document attachment may belong to an order; its job is not yet identified.",
        }
    # A generic 'please confirm' or a PDF invoice alone is not order evidence.
    # A document plus a review request is plausible enough for one PM question.
    if re.search(
        r"\b(?:please (?:review|revise|confirm)|comments? (?:attached|below)|revised (?:specification|spec|drawing))\b",
        content,
        re.IGNORECASE,
    ) and any(Path(n).suffix.lower() in {".pdf", ".xlsx", ".xls"} for n in filenames):
        return {
            "version": VERSION,
            "relevant": True,
            "reason": "A review or clarification request may concern an order.",
        }
    return {
        "version": VERSION,
        "relevant": False,
        "reason": "No order, equipment review, or drawing attachment was identified.",
    }
