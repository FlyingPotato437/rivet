"""The actual Outlook thread, not only imported receipts, guards every write."""

from copy import deepcopy
from urllib.parse import quote, urlencode

import httpx
import pytest
from fastapi import HTTPException

from backend.equipment.outlook import _assert_thread_current
from backend.mailbox.graph import GRAPH, GraphError


def message(
    identity="original",
    *,
    received="2026-10-18T12:00:00Z",
    folder="inbox",
    draft=False,
    conversation="customer-thread",
):
    return {
        "id": identity,
        "receivedDateTime": received,
        "parentFolderId": folder,
        "isDraft": draft,
        "conversationId": conversation,
    }


class ThreadRead:
    def __init__(self, *, original=None, pages=None, status=200):
        self.original = deepcopy(original or message())
        self.pages = deepcopy(
            pages if pages is not None else [{"value": [self.original]}]
        )
        self.calls = []
        self.status = status
        self.mailbox = "pm@example.com"
        self.base = f"{GRAPH}/users/{quote(self.mailbox, safe='')}/messages"
        self.page = 0
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.headers = lambda: {"Prefer": 'IdType="ImmutableId"'}

    def handle(self, request):
        self.calls.append(request)
        assert request.method == "GET", (
            "Freshness never sends, edits, deletes or writes drafts."
        )
        assert request.url.host == "graph.microsoft.com"
        assert request.headers["Prefer"] == 'IdType="ImmutableId"'
        assert "pm%40example.com" in request.url.raw_path.decode()
        assert "body" not in request.url.params.get("$select", "")
        if request.url.path.endswith("/mailFolders/sentitems"):
            return httpx.Response(200, json={"id": "sent"})
        if request.url.path.endswith("/messages/original"):
            return httpx.Response(200, json=self.original)
        assert request.url.path.endswith("/messages")
        assert (
            request.url.params["$filter"]
            == "conversationId eq '"
            + self.original["conversationId"].replace("'", "''")
            + "'"
        )
        assert "$orderby" not in request.url.params
        payload = self.pages[min(self.page, len(self.pages) - 1)]
        self.page += 1
        return httpx.Response(self.status, json=payload)

    def next_link(self, **changes):
        params = {
            "$filter": "conversationId eq '"
            + self.original["conversationId"].replace("'", "''")
            + "'",
            "$select": "id,isDraft,conversationId,receivedDateTime,parentFolderId",
            "$skiptoken": "second",
        }
        params.update(changes)
        return self.base + "?" + urlencode(params)


def test_current_thread_checks_read_only_metadata_and_escaped_conversation():
    original = message(conversation="customer's-thread")
    graph = ThreadRead(original=original)
    result = _assert_thread_current(graph, "original")
    assert result["checked_messages"] == 1
    assert result["received_at"] == "2026-10-18T12:00:00+00:00"
    assert len(graph.calls) == 3


@pytest.mark.parametrize("stamp", ["2026-10-18T12:00:01Z", "2026-10-18T12:00:00+00:00"])
def test_new_uncaptured_inbound_mail_or_ambiguous_equal_time_blocks(stamp):
    graph = ThreadRead(
        pages=[{"value": [message(), message("uncaptured", received=stamp)]}]
    )
    with pytest.raises(HTTPException, match="newer customer email"):
        _assert_thread_current(graph, "original")


def test_sent_copies_and_unsent_drafts_do_not_masquerade_as_customer_updates():
    graph = ThreadRead(
        pages=[
            {
                "value": [
                    message(),
                    message("old", received="2026-10-17T12:00:00Z"),
                    message("sent", received="2026-10-18T13:00:00Z", folder="sent"),
                    message("draft", received=None, folder="drafts", draft=True),
                ]
            }
        ]
    )
    assert _assert_thread_current(graph, "original")["checked_messages"] == 4
    # A self-addressed received copy is inbound by folder, even if its sender
    # happens to be the authorized mailbox used for the fictional demo.
    graph = ThreadRead(
        pages=[
            {
                "value": [
                    message(),
                    message("self-received", received="2026-10-18T13:00:00Z"),
                ]
            }
        ]
    )
    with pytest.raises(HTTPException):
        _assert_thread_current(graph, "original")


@pytest.mark.parametrize("stamp", [None, "unreadable", "2026-10-18T12:00:00"])
def test_unknown_original_timestamps_cannot_establish_freshness(stamp):
    graph = ThreadRead(original=message(received=stamp))
    with pytest.raises(GraphError):
        _assert_thread_current(graph, "original")


@pytest.mark.parametrize(
    "payload",
    [
        {"value": []},
        {"value": None},
        {"value": [message("other", conversation="unrelated")]},
        {"value": [message(), message()]},
    ],
)
def test_incomplete_or_inconsistent_provider_results_fail_closed(payload):
    graph = ThreadRead(pages=[payload])
    with pytest.raises(GraphError):
        _assert_thread_current(graph, "original")


def test_complete_bounded_paging_finds_uncaptured_mail_on_later_page():
    graph = ThreadRead()
    graph.pages = [
        {"value": [message()], "@odata.nextLink": graph.next_link()},
        {"value": [message("new", received="2026-10-18T12:01:00Z")]},
    ]
    with pytest.raises(HTTPException):
        _assert_thread_current(graph, "original")
    assert graph.page == 2


@pytest.mark.parametrize("kind", ["host", "mailbox", "filter", "select", "cycle"])
def test_provider_cursors_cannot_expand_mailbox_or_conversation_scope(kind):
    graph = ThreadRead()
    following = graph.next_link()
    if kind == "host":
        following = following.replace("graph.microsoft.com", "elsewhere.example")
    elif kind == "mailbox":
        following = following.replace("pm%40example.com", "other%40example.com")
    elif kind == "filter":
        following = graph.next_link(**{"$filter": "conversationId eq 'other'"})
    elif kind == "select":
        following = graph.next_link(**{"$select": "id,body"})
    elif kind == "cycle":
        following = (
            graph.base
            + "?"
            + urlencode(
                {
                    "$filter": "conversationId eq 'customer-thread'",
                    "$select": "id,isDraft,conversationId,receivedDateTime,parentFolderId",
                    "$top": "100",
                }
            )
        )
    graph.pages = [{"value": [message()], "@odata.nextLink": following}]
    with pytest.raises(GraphError):
        _assert_thread_current(graph, "original")
    assert graph.page == 1


def test_provider_read_failure_does_not_count_as_current():
    graph = ThreadRead(status=503)
    with pytest.raises(GraphError):
        _assert_thread_current(graph, "original")


def test_integration_new_mail_before_create_leaves_outlook_untouched(packet):
    from test_equipment_outlook import DraftMailbox, add_thread, identity, save

    add_thread(packet)
    graph = DraftMailbox()
    graph.extra_inbound = [
        message(
            "new-customer-mail",
            received="2026-10-18T12:01:00Z",
            conversation="order-thread",
        )
    ]
    with identity(), pytest.raises(HTTPException, match="newer customer email"):
        save(packet, graph)
    assert not any(request.method == "POST" for request in graph.calls)


def test_integration_new_mail_after_create_stops_attachments_and_preserves_draft(
    packet,
):
    from test_equipment_outlook import DraftMailbox, add_thread, identity, save

    add_thread(packet)
    graph = DraftMailbox()
    graph.after_create = lambda: graph.extra_inbound.append(
        message(
            "new-customer-mail",
            received="2026-10-18T12:01:00Z",
            conversation="order-thread",
        )
    )
    with identity(), pytest.raises(HTTPException, match="newer customer email"):
        save(packet, graph)
    assert graph.messages["native-correction"]["isDraft"]
    assert not graph.messages["native-correction"]["attachments"]
    assert len([request for request in graph.calls if request.method == "POST"]) == 1


# Reuse the established isolated-database package fixture; pure guard tests above
# remain independently runnable with --noconftest -k 'not integration'.
import test_equipment_outlook as correction_tests

packet = correction_tests.packet
