"""Microsoft Graph writes for drafts and labels. There is deliberately no send call.

These use the read client's token and immutable-ID header. They require the
application permission Mail.ReadWrite on the connected mailbox; without it Graph
answers 403 and the caller records that an administrator must grant it.
"""

import re
from datetime import datetime
from html import escape, unescape
from urllib.parse import quote, unquote, urlencode, urlsplit

import httpx

from backend.mailbox.graph import GRAPH, GraphError, response_error

# Graph accepts inline file attachments up to 3 MB; larger ones need an upload session.
MAX_INLINE_ATTACHMENT = 3 * 1024 * 1024
TAG = "Rivet: reply drafted"
MAX_DRAFT_CHECK_PAGES = 4
MAX_DRAFT_CHECK_ITEMS = 200
# Where Outlook starts the quoted original under a reply.
QUOTE_MARKERS = (
    'id="divrplyfwdmsg"',
    "id='divrplyfwdmsg'",
    'id="appendonsend"',
    "<hr",
    "-----original message-----",
)


def _url(graph, *parts):
    return f"{GRAPH}/users/{quote(graph.mailbox, safe='')}/" + "/".join(
        quote(p, safe="") for p in parts
    )


def _call(graph, method, url, payload=None, *, immutable=True):
    try:
        headers = {**graph.headers(), "Content-Type": "application/json"}
        if not immutable:
            # Identifier format is a response preference, scoped to this one
            # read. The long-lived tracked ID remains immutable in our database.
            headers.pop("Prefer", None)
        response = graph.client.request(
            method,
            url,
            headers=headers,
            json=payload,
        )
        if not response.is_success:
            raise response_error(response)
        return response.json() if response.content else {}
    except httpx.HTTPError:
        raise GraphError(
            "retry",
            "Microsoft mailbox service could not be reached; retry is scheduled.",
        )
    except ValueError:
        raise GraphError("provider_error", "Microsoft returned an unreadable response.")


def create_reply(graph, message_id):
    """An unsent reply in the original thread, addressed to the sender by Outlook."""
    draft = _call(graph, "POST", _url(graph, "messages", message_id, "createReply"), {})
    if not isinstance(draft.get("id"), str) or not draft["id"]:
        raise GraphError("provider_error", "Microsoft did not return the new draft.")
    return draft


def set_body(graph, draft_id, html_content):
    _call(
        graph,
        "PATCH",
        _url(graph, "messages", draft_id),
        {"body": {"contentType": "html", "content": html_content}},
    )


def attach(graph, draft_id, name, content_type, base64_bytes):
    _call(
        graph,
        "POST",
        _url(graph, "messages", draft_id, "attachments"),
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name,
            "contentType": content_type or "application/octet-stream",
            "contentBytes": base64_bytes,
        },
    )


def get_message(graph, message_id):
    url = (
        _url(graph, "messages", message_id)
        + "?$select=id,isDraft,body,sentDateTime,lastModifiedDateTime,categories,parentFolderId,webLink,conversationId"
    )
    return _call(graph, "GET", url)


def existing_reply(graph, message_id):
    """Preserve a reply the person already started in this conversation."""
    original = get_message(graph, message_id)
    conversation = original.get("conversationId")
    if not isinstance(conversation, str) or not conversation:
        return None
    query = urlencode(
        {
            "$filter": "conversationId eq '" + conversation.replace("'", "''") + "'",
            "$select": "id,isDraft,webLink,conversationId,lastModifiedDateTime",
            "$top": "50",
        }
    )
    base = _url(graph, "mailFolders", "drafts", "messages")
    link, candidates, seen, count = base + "?" + query, {}, set(), 0
    for _ in range(MAX_DRAFT_CHECK_PAGES):
        if link in seen:
            raise GraphError(
                "provider_error",
                "Microsoft repeated the draft list cursor. Existing drafts must be checked before another is created.",
            )
        seen.add(link)
        result = _call(graph, "GET", link)
        items = result.get("value")
        if not isinstance(items, list) or any(
            not isinstance(item, dict) for item in items
        ):
            raise GraphError(
                "provider_error", "Microsoft did not return the existing drafts check."
            )
        count += len(items)
        if count > MAX_DRAFT_CHECK_ITEMS:
            break
        for item in items:
            if (
                item.get("isDraft") is True
                and item.get("conversationId") == conversation
            ):
                if not isinstance(item.get("id"), str) or not item["id"]:
                    raise GraphError(
                        "provider_error",
                        "Microsoft returned a draft without an identifier. Check Outlook Drafts.",
                    )
                candidates[item["id"]] = item
        following = result.get("@odata.nextLink")
        if not following:
            if len(candidates) <= 1:
                return next(iter(candidates.values()), None)

            def modified(item):
                try:
                    stamp = datetime.fromisoformat(item.get("lastModifiedDateTime", ""))
                    if stamp.tzinfo is None:
                        raise ValueError()
                    return stamp, item["id"]
                except (ValueError, TypeError):
                    raise GraphError(
                        "provider_error",
                        "Microsoft did not identify the newest existing draft. Open Outlook Drafts to choose; Rivet will not create a duplicate.",
                    ) from None

            return max(candidates.values(), key=modified)
        try:
            target, expected = urlsplit(following), urlsplit(base)
            safe = (
                target.scheme == "https"
                and target.netloc == expected.netloc
                and unquote(target.path) == unquote(expected.path)
                and not target.fragment
                and not target.username
                and not target.password
            )
        except (TypeError, ValueError):
            safe = False
        if not safe:
            raise GraphError(
                "invalid_cursor", "Microsoft returned an unexpected draft list cursor."
            )
        link = following
    raise GraphError(
        "provider_error",
        "This thread has too many existing drafts to check safely. Review Outlook Drafts; Rivet will not create another.",
    )


def is_deleted(graph, message):
    """An immutable ID remains readable after an ordinary move to Deleted Items."""
    parent = message.get("parentFolderId")
    if not parent:
        return False
    folder = _call(
        graph, "GET", _url(graph, "mailFolders", "deleteditems") + "?$select=id"
    )
    if not isinstance(folder.get("id"), str) or not folder["id"]:
        raise GraphError("provider_error", "Microsoft did not identify Deleted Items.")
    return parent == folder["id"]


def open_target(graph, message_id):
    """Resolve only this existing draft, without changing mail or directory scope."""
    url = (
        _url(graph, "messages", message_id)
        + "?$select=id,isDraft,sentDateTime,parentFolderId,webLink"
    )
    normal_format = True
    try:
        message = _call(graph, "GET", url, immutable=False)
    except GraphError as exc:
        if exc.status not in {400, 404}:
            raise
        # Some hosts may require the matching immutable-ID preference. The
        # provider's webLink still opens the existing message without converting.
        normal_format = False
        message = get_message(graph, message_id)
    rest_id = message.get("id", "") if normal_format else ""
    if (
        not isinstance(rest_id, str)
        or not 0 < len(rest_id) <= 2048
        or rest_id == message_id
    ):
        rest_id = ""
    web_link = message.get("webLink", "")
    try:
        parsed = urlsplit(web_link)
        if (
            parsed.scheme != "https"
            or parsed.netloc
            not in {
                "outlook.office.com",
                "outlook.office365.com",
                "outlook.live.com",
                "outlook.cloud.microsoft",
            }
            or parsed.username
            or parsed.password
        ):
            web_link = ""
    except (TypeError, ValueError):
        web_link = ""
    return message, {"rest_item_id": rest_id, "outlook_url": web_link}


def tag(graph, message_id, label=TAG):
    """Adds a category so the PM sees Rivet's work in the inbox list itself."""
    current = (
        _call(
            graph, "GET", _url(graph, "messages", message_id) + "?$select=categories"
        ).get("categories")
        or []
    )
    if label in current:
        return
    _call(
        graph,
        "PATCH",
        _url(graph, "messages", message_id),
        {"categories": [*current, label]},
    )


def reply_html(text):
    lines = escape(text.strip()).replace("\r\n", "\n").split("\n")
    return (
        '<div id="rivet-reply" style="font-family:Aptos,Calibri,Arial,sans-serif;'
        'font-size:11pt">' + "<br>".join(lines) + "</div><br>"
    )


def compose_body(existing_html, text, content_type="html"):
    """Put Rivet's reply above Outlook's quoted original, keeping the thread intact."""
    ours = reply_html(text)
    if content_type.lower() == "text":
        quoted = escape(existing_html or "").replace("\r\n", "\n").replace("\n", "<br>")
        return ours + '<div id="divRplyFwdMsg">' + quoted + "</div>"
    match = re.search(r"<body[^>]*>", existing_html or "", flags=re.IGNORECASE)
    if match:
        return existing_html[: match.end()] + ours + existing_html[match.end() :]
    return ours + (existing_html or "")


def sent_text(html_body):
    """The words the person actually sent, above the quoted original."""
    html_body = html_body or ""
    lower = html_body.lower()
    cuts = []
    for marker in QUOTE_MARKERS:
        i = lower.find(marker)
        if i >= 0:
            # An attribute marker cuts at the start of the tag that carries it.
            cuts.append(lower.rfind("<", 0, i) if marker.startswith("id=") else i)
    cuts = [c for c in cuts if c >= 0]
    head = html_body[: min(cuts)] if cuts else html_body
    head = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", head)
    head = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", head)
    text = unescape(re.sub(r"<[^>]+>", "", head)).replace("\xa0", " ")
    # Outlook can flatten a plain-text original into a separator and mail headers.
    # Require the whole header sequence so ordinary response text is preserved.
    quote = re.search(r"(?is)_{5,}\s*From:.*?\bSent:.*?\bTo:.*?\bSubject:", text)
    if quote:
        text = text[: quote.start()]
    lines, out = (
        [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()],
        [],
    )
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def same_words(a, b):
    return re.sub(r"\s+", " ", a or "").strip() == re.sub(r"\s+", " ", b or "").strip()
