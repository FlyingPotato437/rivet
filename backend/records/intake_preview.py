"""Read-only, tenant-scoped intake previews. Never render received email HTML."""

from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from backend.storage import db

BODY_LIMIT = 30000


def attachment_bytes(part):
    """Preserve attached messages too; their decoded payload is a message list."""
    payload = part.get_payload(decode=True)
    if payload is not None:
        return payload
    nested = part.get_payload()
    if isinstance(nested, list):
        return b"\r\n".join(message.as_bytes(policy=policy.SMTP) for message in nested)
    return b""


class EmailText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "div", "br", "li", "tr", "hr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head"} and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def email_preview(payload):
    message = BytesParser(policy=policy.default).parsebytes(payload)
    body = message.get_body(preferencelist=("plain", "html"))
    content = ""
    if body:
        try:
            content = body.get_content()
        except (LookupError, UnicodeError):
            content = (body.get_payload(decode=True) or b"").decode(
                "utf-8", errors="replace"
            )
        # Some clients emit an empty text alternative followed by useful HTML.
        if not content.strip():
            html_body = message.get_body(preferencelist=("html",))
            if html_body:
                body = html_body
                content = body.get_content()
        if body.get_content_type() == "text/html":
            parser = EmailText()
            parser.feed(content)
            content = "\n".join(
                line.strip()
                for line in "".join(parser.parts).splitlines()
                if line.strip()
            )
    content = content.strip()
    return {
        **{
            key: str(message.get(header, ""))
            for key, header in (
                ("subject", "Subject"),
                ("from", "From"),
                ("to", "To"),
                ("date", "Date"),
            )
        },
        "body": content[:BODY_LIMIT],
        "truncated": len(content) > BODY_LIMIT,
        "attachments": [
            {
                "index": index,
                "name": Path(part.get_filename() or "attachment").name,
                "content_type": part.get_content_type(),
                "size": len(attachment_bytes(part)),
            }
            for index, part in enumerate(message.iter_attachments())
        ],
    }


def preview(s, row):
    """The caller must resolve row through get/scoped before accessing its blob."""
    result = {"kind": "file", "filename": row.name}
    suffix = Path(row.name).suffix.lower()
    try:
        payload = (db.BLOBS / row.blob).read_bytes()
    except OSError:
        return {
            **result,
            "error": "The original is unavailable. Check the email in Outlook before confirming.",
        }
    if suffix == ".eml":
        result = {**result, "kind": "email", **email_preview(payload)}
        from backend.domain.service import scoped
        from backend.mailbox.models import MailboxReceipt

        receipt = s.scalar(
            scoped(MailboxReceipt).where(MailboxReceipt.intake_id == row.id)
        )
        if receipt:
            result["origin"] = "Outlook"
            result["received_at"] = receipt.data.get("received_at", "")
            link = receipt.data.get("web_link", "")
            parsed = urlsplit(link)
            if (
                parsed.scheme == "https"
                and parsed.netloc
                in {"outlook.office.com", "outlook.office365.com", "outlook.live.com"}
                and not parsed.username
                and not parsed.password
            ):
                result["outlook_url"] = link
    elif suffix == ".txt":
        content = payload.decode("utf-8", errors="replace")
        result.update(body=content[:BODY_LIMIT], truncated=len(content) > BODY_LIMIT)
    return result
