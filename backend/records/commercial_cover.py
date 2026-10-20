"""Recognize a commercial transmittal only beside its actual complete attachment.

This does not discard the message or answer independent instructions. Unknown
prose stays reviewable; stated quantity/tag must agree with the attached schedule.
"""

import re
from decimal import Decimal
from email.utils import parseaddr

_NUMBER_WORDS = {
    word: index
    for index, word in enumerate(
        ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"),
        1,
    )
}
_JOB = r"(?: for (?:job )?[a-z0-9]+(?:[-./][a-z0-9]+)*)?"
_CONTEXT = (
    (
        r"(?:please )?(?:identify|list|note) any exceptions(?:, including remote indication,)?"
        r"(?: and prepare the quotation for review)?"
    ),
    r"(?:please )?prepare (?:the |a )?(?:quotation|quote|purchase[- ]order comparison) for review",
    r"this is a fictional integration rehearsal between my own accounts",
    r"no real customer order, pricing commitment or engineering approval is represented",
    r"it is not a customer order, purchase authorization or engineering release",
    r"(?:please )?identify any differences and prepare a clarification before accepting the order",
    r"(?:fictional|sample|test|demo) (?:product |integration )?(?:test|rehearsal|example)(?: only)?",
)


def is_complete_attachment_cover(text, document, rows):
    """No filename/order-wide guessing: IDs come from the original EML importer."""
    if not document.get("email") or not text or len(text) > 6000:
        return False
    attached = set(document.get("attachment_document_ids", []))
    rows = [row for row in rows if row["document_id"] in attached]
    if not rows or len({row["role"] for row in rows}) != 1:
        return False
    role = rows[0]["role"]
    noun = (
        r"(?:rfq|request for quot(?:ation|e)|quotation request)"
        if role == "quote_request"
        else r"(?:po|purchase order)"
    )
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    recipient = parseaddr(document["email"].get("to", ""))[0].casefold()
    salutations = {
        greeting + (" " + target if target else "")
        for greeting in ("hi", "hello", "dear")
        for target in {"", "team", "rivet team", recipient}
    }
    if lines and lines[0].rstrip(",!").casefold() in salutations:
        lines.pop(0)
    signatures = {parseaddr(document["email"].get("from", ""))[0].casefold()} - {""}
    if "fictional" in text.casefold():
        signatures.add("rivet demo reviewer")
    for index, line in enumerate(lines):
        if re.fullmatch(r"(?:thanks|thank you|best|regards)[,!]?", line, re.IGNORECASE):
            signature = lines[index + 1 :]
            if len(signature) <= 1 and all(
                name.casefold() in signatures for name in signature
            ):
                lines = lines[:index]
            break
    content = re.sub(r"\s+", " ", " ".join(lines)).strip()
    sentences = [p.strip(" ,") for p in re.split(r"[.;]\s*", content) if p.strip(" ,")]
    saw_cover = False
    for sentence in sentences:
        simple = (
            rf"(?:please )?(?:review|see|find) (?:the )?(?:attached|enclosed) {noun}{_JOB}",
            rf"(?:the )?{noun} (?:is )?(?:attached|enclosed){_JOB}",
        )
        specified = re.fullmatch(
            r"(?:please )?quote the (?P<quantity>\d+|one|two|three|four|five|six|seven|eight|nine|ten) "
            r"(?P<tag>[a-z0-9]+(?:[-_][a-z0-9]+)*) "
            r"(?:circuit[- ]breaker packages?|circuit[- ]breakers?|breakers?) "
            rf"(?:specified|described) in the (?:attached|enclosed) {noun}{_JOB}",
            sentence,
            re.IGNORECASE,
        )
        compared_po = re.fullmatch(
            r"(?:please )?compare the (?:attached|enclosed) purchase order "
            r"(?P<po>[a-z0-9]+(?:[-./][a-z0-9]+)*), revision (?P<revision>\d+),"
            rf"{_JOB} against (?:your|the) quotation",
            sentence,
            re.IGNORECASE,
        )
        if compared_po:
            if role != "purchase_order" or any(
                row["value"]["purchase_order_number"].casefold()
                != compared_po["po"].casefold()
                or row["value"]["po_revision"] != int(compared_po["revision"])
                for row in rows
            ):
                return False
            saw_cover = True
        elif specified:
            quantity = specified["quantity"].casefold()
            quantity = Decimal(str(_NUMBER_WORDS.get(quantity, quantity)))
            if (
                role != "quote_request"
                or len(rows) != 1
                or rows[0]["value"]["tag"].casefold() != specified["tag"].casefold()
                or Decimal(rows[0]["value"]["quantity"]) != quantity
            ):
                return False
            saw_cover = True
        elif any(re.fullmatch(pattern, sentence, re.IGNORECASE) for pattern in simple):
            saw_cover = True
        elif not any(
            re.fullmatch(pattern, sentence, re.IGNORECASE) for pattern in _CONTEXT
        ):
            return False
    return saw_cover
