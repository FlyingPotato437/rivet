"""Email transmittals are evidence; independent customer requests stay visible."""

import pytest

from backend.records.email_comment_context import attachment_cover_reason

REHEARSAL_TRANSMITTAL = """Fictional product rehearsal only. This is not a customer order,
purchase authorization or engineering release.

Hi Rivet team,

Please review the 11 annotations in the attached returned drawing for
job DEMO-LIFECYCLE-1015-081854, Rev C. Use the existing specification,
accepted scope and fictional engineering decision in the order record.
Please prepare the responses and proposed change order for the
additional breaker; keep the drawing edits as an engineering draft.

Thanks,
Rivet demo reviewer
"""


@pytest.mark.parametrize(
    "body",
    [
        "Please review the attached drawing.",
        "Hi team,\nPlease review the attached returned drawing for job 24-118, Rev C.\nThanks,\nCasey Reviewer",
        "The returned comments are attached for job 24-118, Revision C.",
        REHEARSAL_TRANSMITTAL,
    ],
)
def test_known_attachment_covers_are_context_only(body):
    assert attachment_cover_reason(body, has_attachments=True)


@pytest.mark.parametrize(
    "body",
    [
        "Please review the attached drawing and confirm the trip unit is 3000 A.",
        "Please review the attached drawing. Please change the enclosure to NEMA 3R.",
        "Please review the attached drawing. Can you confirm the last revision is released?",
        "Please review the attached drawing. Approved as noted.",
        "Please review the attached drawing. Add the breaker shown in blue.",
        "Please review the attached drawing.\nThanks,\nPlease change the trip unit.",
        "The latest looks fine except the red item on the second sheet.",
        "Please prepare the proposed change order for the additional breaker.",
        "Please review the attached drawing.\n1. Confirm the cable entry.",
        "Please review the attached drawing. Unknown customer prose is retained.",
    ],
)
def test_unknown_or_independent_request_is_not_hidden(body):
    assert attachment_cover_reason(body, has_attachments=True) == ""


def test_a_missing_attachment_is_not_treated_as_a_completed_transmittal():
    assert (
        attachment_cover_reason(
            "Please review the attached drawing.", has_attachments=False
        )
        == ""
    )
