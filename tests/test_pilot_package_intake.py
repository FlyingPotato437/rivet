"""Tenant isolation and safe private originals for the actual pilot intake path."""

import asyncio
import hashlib
import json
import tempfile
from io import BytesIO

import pymupdf
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.identity import Identity
from backend.storage import db


@pytest.fixture
def upload_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setenv("RIVET_AUTH_MODE", "local")
    monkeypatch.setenv("RIVET_LOCAL_ONLY", "true")
    monkeypatch.setenv("RIVET_WORKSPACE_ENABLED", "true")
    return TestClient(app, headers={"X-Rivet-Client": "workspace"}), tmp_path


def pdf_bytes():
    # Parser-only boundary input; never included in the real accuracy corpus.
    with pymupdf.open() as document:
        document.new_page()
        return document.tobytes()


def submit(client, **changes):
    fields = {
        "redaction_confirmed": "true",
        "file_metadata": json.dumps(
            [
                {
                    "name": "drawing.pdf",
                    "role": "drawing",
                    "revision": "A",
                    "side": "baseline",
                }
            ]
        ),
    }
    fields.update(changes)
    return client.post(
        "/api/pilot-packages",
        data=fields,
        files=[("files", ("drawing.pdf", BytesIO(pdf_bytes()), "application/pdf"))],
    )


def test_upload_requires_redaction_and_does_not_queue_work(upload_client):
    client, root = upload_client
    assert submit(client, redaction_confirmed="false").status_code == 422
    result = submit(client)
    assert result.status_code == 201, result.text
    record = result.json()
    assert record["processing_started"] is False
    package = client.get(record["manifest_url"])
    assert package.status_code == 200
    manifest = package.json()
    assert manifest["redaction_review"]["confirmed"]
    assert manifest["packages"][0]["kind"] == "real_private"
    assert manifest["packages"][0]["model_processing"]["external_ai_allowed"] is False
    assert record["external_ai_authorized"] is False
    assert next(root.rglob("01.pdf")).stat().st_mode & 0o777 == 0o600
    downloaded = client.get(
        f"/api/pilot-packages/{record['package_id']}/sources/source-01"
    ).content
    assert hashlib.sha256(downloaded).hexdigest() == record["files"][0]["sha256"]


def test_external_ai_authorization_is_separate_from_redaction_and_records_exact_sources(
    upload_client,
):
    client, root = upload_client
    denied = submit(client, external_ai_authorized="true")
    assert denied.status_code == 422
    assert not list(root.rglob("package.json"))
    result = submit(
        client,
        external_ai_authorized="true",
        processing_authorization_reference="Approved redacted evaluation - synthetic reference",
    )
    assert result.status_code == 201, result.text
    record = result.json()
    package = client.get(record["manifest_url"]).json()["packages"][0]
    receipt = package["model_processing"]
    assert receipt["external_ai_allowed"] is True
    assert (
        receipt["authorization_reference"]
        == "Approved redacted evaluation - synthetic reference"
    )
    assert receipt["recorded_by"] and receipt["recorded_at"]
    assert receipt["source_hashes"] == {
        s["id"]: s["sha256"] for s in package["sources"]
    }
    assert record["processing_started"] is False


@pytest.mark.parametrize("reference", ["x" * 501, "email\nbody", "email\x00body"])
def test_invalid_authorization_reference_is_rejected_before_storage(
    upload_client, reference
):
    client, root = upload_client
    response = submit(client, processing_authorization_reference=reference)
    assert response.status_code == 422
    assert not list(root.rglob("package.json"))


def test_other_tenant_cannot_read_manifest_or_original(upload_client, monkeypatch):
    client, _ = upload_client
    record = submit(client).json()
    from backend import auth

    monkeypatch.setenv("RIVET_AUTH_MODE", "clerk")
    monkeypatch.setattr(
        auth,
        "verify_identity",
        lambda request: Identity("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "second-user"),
    )
    assert client.get(record["manifest_url"]).status_code == 404
    assert (
        client.get(
            f"/api/pilot-packages/{record['package_id']}/sources/source-01"
        ).status_code
        == 404
    )
    assert client.get("/api/pilot-packages/not-a-uuid/manifest").status_code == 404


def test_missing_auth_is_rejected_and_wrong_metadata_is_not_guessed(
    upload_client, monkeypatch
):
    client, _ = upload_client
    assert submit(client, file_metadata="[]").status_code == 422
    monkeypatch.setenv("RIVET_AUTH_MODE", "clerk")
    assert client.get("/api/pilot-packages/checklist").status_code in {401, 503}


def test_malformed_metadata_name_returns_validation_error_not_server_error(
    upload_client,
):
    client, root = upload_client
    metadata = [{"name": [], "role": "drawing", "revision": "A", "side": "target"}]
    assert submit(client, file_metadata=json.dumps(metadata)).status_code == 422
    assert not list(root.rglob("package.json"))


@pytest.mark.parametrize("name", ["../drawing.pdf", r"..\drawing.pdf"])
def test_path_like_metadata_filename_is_rejected_before_storage(upload_client, name):
    client, root = upload_client
    metadata = [{"name": name, "role": "drawing", "revision": "A", "side": "target"}]
    response = client.post(
        "/api/pilot-packages",
        data={
            "redaction_confirmed": "true",
            "file_metadata": json.dumps(metadata),
        },
        files=[("files", (name, BytesIO(pdf_bytes()), "application/pdf"))],
    )
    assert response.status_code == 422
    assert not list(root.rglob("package.json"))


def test_duplicate_json_metadata_keys_are_rejected(upload_client):
    client, root = upload_client
    duplicate = (
        '[{"name":"drawing.pdf","name":"other.pdf","role":"drawing",'
        '"revision":"A","side":"baseline"}]'
    )
    assert submit(client, file_metadata=duplicate).status_code == 422
    assert not list(root.rglob("package.json"))


def test_unsupported_cad_format_has_clear_export_path(upload_client):
    client, root = upload_client
    response = client.post(
        "/api/pilot-packages",
        data={
            "redaction_confirmed": "true",
            "file_metadata": json.dumps(
                [
                    {
                        "name": "drawing.dwg",
                        "role": "drawing",
                        "revision": "A",
                        "side": "baseline",
                    }
                ]
            ),
        },
        files=[("files", ("drawing.dwg", BytesIO(b"DWG"), "application/octet-stream"))],
    )
    assert response.status_code == 422
    assert "Native DWG conversion is not configured" in response.json()["detail"]
    assert (
        "DXF" in response.json()["detail"]
        and "matching PDF" in response.json()["detail"]
    )
    assert not list(root.rglob("package.json"))


def limit_test_app():
    from backend.pilot_packages import PilotPackageBodyLimitMiddleware

    bounded = FastAPI()
    bounded.add_middleware(PilotPackageBodyLimitMiddleware)

    @bounded.post("/api/pilot-packages")
    async def parse(request: Request):
        async with request.form() as form:
            return {"parts": len(form)}

    return bounded


def asgi_upload(application, chunks, extra_headers=()):
    """Real ASGI message chunks: TestClient combines streaming content first."""
    received, sent = [], []

    async def run():
        remaining = iter(enumerate(chunks))

        async def receive():
            try:
                index, chunk = next(remaining)
            except StopIteration:
                return {"type": "http.disconnect"}
            received.append(index)
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": index < len(chunks) - 1,
            }

        async def send(message):
            sent.append(message)

        await application(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/pilot-packages",
                "raw_path": b"/api/pilot-packages",
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"content-type", b"multipart/form-data; boundary=rivet"),
                    *extra_headers,
                ],
                "client": ("127.0.0.1", 1000),
                "server": ("test", 80),
            },
            receive,
            send,
        )

    asyncio.run(run())
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    return status, json.loads(body), received


def test_declared_oversize_is_rejected_without_reading_or_starting_parser(monkeypatch):
    import starlette.formparsers as parsers

    import backend.pilot_packages as intake

    monkeypatch.setattr(intake, "MAX_PILOT_REQUEST_BYTES", 100)

    async def must_not_parse(self):
        raise AssertionError("The multipart parser ran before declared-size rejection")

    monkeypatch.setattr(parsers.MultiPartParser, "parse", must_not_parse)
    status, body, received = asgi_upload(
        limit_test_app(), [b"not consumed"], [(b"content-length", b"101")]
    )
    assert status == 413 and not received
    assert "package limit" in body["detail"]


def test_chunked_overflow_rejects_before_any_file_is_spooled(monkeypatch):
    import starlette.formparsers as parsers

    import backend.pilot_packages as intake

    monkeypatch.setattr(intake, "MAX_PILOT_REQUEST_BYTES", 100)

    def must_not_spool(*args, **kwargs):
        raise AssertionError("The overflowing ASGI chunk reached the multipart parser")

    monkeypatch.setattr(parsers, "SpooledTemporaryFile", must_not_spool)
    status, _, received = asgi_upload(limit_test_app(), [b"x" * 101, b"must not read"])
    assert status == 413 and received == [0]


@pytest.mark.parametrize("declared", [None, b"1"])
def test_cumulative_stream_limit_closes_earlier_spooled_parts(monkeypatch, declared):
    import starlette.formparsers as parsers

    import backend.pilot_packages as intake

    monkeypatch.setattr(intake, "MAX_PILOT_REQUEST_BYTES", 400)
    created = []

    def tracked_spool(*args, **kwargs):
        kwargs["max_size"] = 1  # Exercise actual disk rollover using only a few bytes.
        # The parser owns this file; this assertion verifies its error cleanup.
        file = tempfile.SpooledTemporaryFile(*args, **kwargs)  # noqa: SIM115
        created.append(file)
        return file

    monkeypatch.setattr(parsers, "SpooledTemporaryFile", tracked_spool)
    prefix = b'--rivet\r\nContent-Disposition: form-data; name="files"; filename="drawing.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
    chunks = [prefix + b"prior bounded data", b"x" * 400, b"\r\n--rivet--\r\n"]
    headers = [(b"content-length", declared)] if declared else []
    status, _, received = asgi_upload(limit_test_app(), chunks, headers)
    assert status == 413 and received == [0, 1]
    assert len(created) == 1 and created[0].closed


def test_exact_stream_limit_still_accepts_valid_multipart(monkeypatch):
    import backend.pilot_packages as intake

    data = b'--rivet\r\nContent-Disposition: form-data; name="note"\r\n\r\nbounded\r\n--rivet--\r\n'
    monkeypatch.setattr(intake, "MAX_PILOT_REQUEST_BYTES", len(data))
    status, body, received = asgi_upload(limit_test_app(), [data[:20], data[20:]])
    assert status == 200 and body == {"parts": 1} and received == [0, 1]


def test_real_api_registers_limit_before_pilot_upload_spooling(
    upload_client, monkeypatch
):
    import starlette.formparsers as parsers

    import backend.pilot_packages as intake

    client, root = upload_client
    monkeypatch.setattr(intake, "MAX_PILOT_REQUEST_BYTES", 1)

    def must_not_spool(*args, **kwargs):
        raise AssertionError("The real API spooled a declared oversized upload")

    monkeypatch.setattr(parsers, "SpooledTemporaryFile", must_not_spool)
    assert submit(client).status_code == 413
    assert not list(root.rglob("package.json"))
