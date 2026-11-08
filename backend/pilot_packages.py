"""Authenticated, tenant-private pilot intake, isolated from automated mail work.

No public upload token, email ingestion or automatic correction is created here.
The existing API authentication boundary protects every route in this module.
"""

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from backend.domain.service import fail
from backend.identity import current_actor, current_org
from backend.ingestion.limits import DWG_REQUEST, upload_max_bytes, validate_pdf_upload
from backend.storage import db
from backend.storage.models import now

router = APIRouter(prefix="/api/pilot-packages", tags=["Private pilot intake"])
# The payload remains capped at 400 MiB in upload_package. This receive-stream
# allowance includes multipart boundaries and metadata before any file parser
# can spool an unbounded request to the API's temporary storage.
MAX_PILOT_REQUEST_BYTES = 402 * 1024 * 1024
REQUEST_LIMIT_DETAIL = (
    "Pilot upload exceeds the 400 MB package limit plus multipart allowance."
)


class PilotPackageBodyLimitMiddleware:
    """Limit actual incoming bytes before multipart parsing/spooling.

    Content-Length is an early rejection only: chunked bodies and inaccurate
    smaller declarations are still counted. Raising HTTPException preserves a
    413 response and lets Starlette close temporary files on parser failure.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path", "").rstrip("/") != "/api/pilot-packages"
        ):
            await self.app(scope, receive, send)
            return
        headers = [
            value
            for key, value in scope.get("headers", ())
            if key.lower() == b"content-length"
        ]
        if headers and (
            len(headers) != 1 or len(headers[0]) > 20 or not headers[0].isdigit()
        ):
            await JSONResponse(
                {"detail": "Invalid Content-Length."},
                400,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
            return
        limit = MAX_PILOT_REQUEST_BYTES
        if headers and int(headers[0]) > limit:
            await JSONResponse(
                {"detail": REQUEST_LIMIT_DETAIL},
                413,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
            return
        consumed, exceeded, started = 0, False, False

        async def bounded_receive():
            nonlocal consumed, exceeded
            if exceeded:
                raise HTTPException(413, REQUEST_LIMIT_DETAIL)
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    exceeded = True
                    # The overflowing chunk never reaches parser.write or an
                    # UploadFile. The parser cleans up earlier bounded parts.
                    raise HTTPException(413, REQUEST_LIMIT_DETAIL)
            return message

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracked_send)
        except HTTPException as exc:
            # Normally FastAPI's exception boundary produces the 413. Keep the
            # guard valid when mounted around another plain ASGI application.
            if not exceeded or started or exc.status_code != 413:
                raise
            await JSONResponse(
                {"detail": REQUEST_LIMIT_DETAIL},
                413,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)


CHECKLIST = [
    "Remove customer/company names, addresses, personal names, phone numbers and email addresses, including title blocks and PDF metadata.",
    "Apply permanent redaction, not a black rectangle or hidden layer; reopen the exported file and test text search/copy.",
    "Keep equipment/device tags, ratings, quantities, dimensions, revision identifiers, reviewer comments and their page locations.",
    "Use consistent aliases across revisions and BOMs. Retain sheet/drawing numbers so revisions can be matched.",
    "Include the original return, prior drawing, revised drawing and BOM where available; identify missing parts explicitly.",
    "Confirm permission to share the redacted package and the agreed retention and model-processing policy.",
]


def root():
    directory = db.DATA / "pilot-intake" / current_org()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory


def _unique_json_object(pairs):
    """Reject ambiguous metadata objects with duplicate JSON keys."""
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate metadata key.")
        value[key] = item
    return value


def package_path(package_id):
    try:
        value = str(uuid.UUID(package_id))
    except ValueError:
        fail("Pilot package not found.", 404)
    path = root() / value
    if not (path / "package.json").is_file():
        fail("Pilot package not found.", 404)
    return path


@router.get("/checklist")
def checklist():
    return {
        "redaction_checklist": CHECKLIST,
        "uploads_are_private": True,
        "automatic_processing": False,
        "external_ai_authorized_by_default": False,
        "external_ai_authorization_required": True,
    }


@router.post("", status_code=201)
async def upload_package(
    files: Annotated[list[UploadFile], File()],
    file_metadata: str = Form(),
    redaction_confirmed: bool = Form(False),
    external_ai_authorized: bool = Form(False),
    processing_authorization_reference: str = Form(""),
):
    if not redaction_confirmed:
        fail("Review the redaction checklist and confirm permission before uploading.")
    reference = processing_authorization_reference.strip()
    if len(reference) > 500 or any(c in reference for c in ("\x00", "\r", "\n")):
        fail("Use a single-line authorization reference of at most 500 characters.")
    if external_ai_authorized and not reference:
        fail(
            "Record the customer's external AI processing authorization reference first."
        )
    if not 1 <= len(files) <= 20:
        fail("Upload between one and twenty files per pilot package.")
    try:
        if len(file_metadata) > 64_000:
            raise ValueError
        entries = json.loads(file_metadata, object_pairs_hook=_unique_json_object)
        if not isinstance(entries, list) or len(entries) != len(files):
            raise ValueError
        for entry in entries:
            if set(entry) != {"name", "role", "revision", "side"}:
                raise ValueError
            if (
                not isinstance(entry["name"], str)
                or not 1 <= len(entry["name"]) <= 255
                or any(c in entry["name"] for c in ("\x00", "\r", "\n", "/", "\\"))
                or entry["name"].strip() in {".", ".."}
            ):
                raise ValueError
            if entry["role"] not in {
                "drawing",
                "bom",
                "markup",
                "specification",
            } or entry["side"] not in {"baseline", "target", "requirements"}:
                raise ValueError
            if (
                not isinstance(entry["revision"], str)
                or not 1 <= len(entry["revision"]) <= 80
            ):
                raise ValueError
    except (ValueError, TypeError, KeyError):
        fail(
            "Identify each file's exact name, role, revision and side; Rivet will not guess the revision sequence."
        )
    names = [f.filename for f in files]
    if len(set(names)) != len(names) or set(names) != {
        entry["name"] for entry in entries
    }:
        fail("Use distinct filenames matching the file metadata.")
    package_id = str(uuid.uuid4())
    pending = root() / (".pending-" + package_id)
    pending.mkdir(mode=0o700)
    (pending / "sources").mkdir(mode=0o700)
    sources, total = [], 0
    try:
        for index, file in enumerate(files, 1):
            name = Path(file.filename or "document").name
            suffix = Path(name).suffix.lower()
            if suffix == ".dwg":
                fail(DWG_REQUEST)
            if suffix not in {".pdf", ".dxf", ".xlsx", ".csv", ".xml", ".tsv"}:
                fail(
                    "Pilot originals must be PDF, DXF, XLSX, CSV/TSV or electrical CAD report XML. Do not upload archives or executable files."
                )
            limit = upload_max_bytes(name)
            data = await file.read(limit + 1)
            total += len(data)
            if not data or len(data) > limit or total > 400 * 1024 * 1024:
                fail(
                    "Use nonempty files within the 200 MB PDF / 20 MB other-file limits and 400 MB package limit.",
                    413,
                )
            if suffix == ".pdf":
                try:
                    validate_pdf_upload(data)
                except ValueError as exc:
                    fail(str(exc))
            relative = f"sources/{index:02d}{suffix}"
            destination = pending / relative
            descriptor = os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
            meta = next(entry for entry in entries if entry["name"] == file.filename)
            sources.append(
                {
                    **meta,
                    "id": f"source-{index:02d}",
                    "path": relative,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                }
            )
        manifest = {
            "schema_version": "rivet.real-packages.v1",
            "redaction_review": {
                "confirmed": True,
                "actor": current_actor(),
                "received_at": now().isoformat(),
                "checklist": CHECKLIST,
            },
            "packages": [
                {
                    "id": package_id,
                    "kind": "real_private",
                    "sources": sources,
                    "model_processing": {
                        "external_ai_allowed": external_ai_authorized,
                        "authorization_reference": reference,
                        "recorded_by": current_actor(),
                        "recorded_at": now().isoformat(),
                        "source_hashes": {s["id"]: s["sha256"] for s in sources},
                    },
                }
            ],
        }
        manifest_file = pending / "package.json"
        manifest_file.write_text(json.dumps(manifest, indent=2) + "\n")
        manifest_file.chmod(0o600)
        pending.rename(root() / package_id)
    except BaseException:
        shutil.rmtree(pending, ignore_errors=True)
        raise
    return {
        "package_id": package_id,
        "files": [
            {k: source[k] for k in ("id", "name", "sha256", "bytes")}
            for source in sources
        ],
        "manifest_url": f"/api/pilot-packages/{package_id}/manifest",
        "processing_started": False,
        "external_ai_authorized": external_ai_authorized,
    }


@router.get("/{package_id}/manifest")
def manifest(package_id: str):
    return FileResponse(
        package_path(package_id) / "package.json",
        media_type="application/json",
        filename="package.json",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{package_id}/sources/{source_id}")
def original(package_id: str, source_id: str):
    directory = package_path(package_id)
    manifest = json.loads((directory / "package.json").read_text())
    source = next(
        (s for s in manifest["packages"][0]["sources"] if s["id"] == source_id), None
    )
    if source is None:
        fail("Pilot source not found.", 404)
    path = (directory / source["path"]).resolve()
    if (
        not path.is_relative_to(directory.resolve())
        or hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]
    ):
        fail("Pilot original is unavailable or changed.", 409)
    return FileResponse(
        path,
        filename=Path(source["name"]).name,
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
