import os, json, secrets, hashlib
from pathlib import Path
from datetime import date
from typing import Literal
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, UploadFile, File, Form, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.cors import CORSMiddleware
from backend.config import allowed_origins, allowed_hosts, validate_configuration, production
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from backend.storage.db import Session, BLOBS, ROOT
from backend.identity import current_actor, auth_mode
from backend.storage.models import *
from backend.domain.schemas import *
from backend.domain.service import *
from backend.ingestion.parser import persist_blob, ALLOWED, map_document
from backend.domain.exports import customer_snapshot, xlsx, pdf, html
from backend.agents.gateway import configured
from backend.domain.coordination import (
    clarification_view,
    project_clarifications,
    create_clarification,
    update_clarification,
    record_answer,
    queue_recheck,
    check_revision,
    work_queue,
    touch,
)


@asynccontextmanager
async def lifespan(app):
    validate_configuration()
    if not os.access(BLOBS, os.W_OK):
        raise RuntimeError("Rivet's document storage is not writable.")
    yield


app = FastAPI(
    title="Rivet API", version="0.1.0", lifespan=lifespan,
    docs_url=None if production() else "/docs",
    redoc_url=None if production() else "/redoc",
    openapi_url=None if production() else "/openapi.json",
)
from backend.pilot import router as pilot_router
app.include_router(pilot_router)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=allowed_hosts(),
)


@app.middleware("http")
async def workspace_boundary(request, call_next):
    from starlette.concurrency import run_in_threadpool
    from backend import auth
    from backend.identity import auth_mode, Identity, LOCAL_ORG, identity_scope

    mode = auth_mode()
    local_only = os.getenv("RIVET_LOCAL_ONLY", "true") == "true"
    try:
        if mode not in {"local", "clerk"} or (not local_only and mode != "clerk"):
            raise HTTPException(
                503, "Shared access requires configured authentication."
            )
        if (
            local_only
            and request.client
            and request.client.host not in ("127.0.0.1", "::1", "testclient")
        ):
            raise HTTPException(403, "Local access only.")
        path = request.url.path
        webhook = path == "/api/webhooks/resend"
        public = (
            path in {"/api/health", "/api/auth/config", "/api/pilot-requests"}
            or path.startswith("/api/shared/")
            or webhook
        )
        if request.method not in ("GET", "HEAD", "OPTIONS") and not webhook:
            if path == "/api/pilot-requests":
                length = request.headers.get("content-length", "0")
                if not length.isdigit():
                    raise HTTPException(400, "Invalid request length.")
                if int(length) > 6000:
                    raise HTTPException(413, "Pilot request is too large.")
            origin = request.headers.get("origin")
            if origin and origin not in auth.allowed_origins():
                raise HTTPException(403, "Origin is not allowed.")
            if request.headers.get("x-rivet-client") != "workspace":
                raise HTTPException(403, "Missing workspace request header.")
        if public:
            response = await call_next(request)
        else:
            identity = (
                await run_in_threadpool(auth.verify_identity, request)
                if mode == "clerk"
                else Identity(LOCAL_ORG, "Local estimator")
            )
            auth.authorize(request, identity)
            with identity_scope(identity):
                response = await call_next(request)
    except HTTPException as exc:
        response = JSONResponse({"detail": exc.detail}, exc.status_code)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


# Outermost user middleware: browser preflights do not have Clerk tokens, and
# allowed origins must also receive CORS headers on authentication errors.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,  # Clerk bearer tokens; no cross-site API cookies.
    allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Rivet-Client", "Idempotency-Key", "Range"],
    expose_headers=["Content-Disposition", "Content-Length", "Content-Range", "Accept-Ranges"],
)


@app.get("/api/auth/config")
def auth_config():
    from backend.auth import publishable_key
    from backend.identity import auth_mode
    from backend.demo import demo_config

    return {
        "mode": auth_mode(),
        "publishable_key": publishable_key(),
        "demo": demo_config(),
    }


@app.get("/api/me")
def me():
    from dataclasses import asdict
    from backend.identity import current_identity, auth_mode

    return {"mode": auth_mode(), **asdict(current_identity())}


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({"detail": str(exc)}, 422)


@app.exception_handler(IntegrityError)
async def integrity_error(request, exc):
    return JSONResponse(
        {
            "detail": "This record conflicts with an existing value. Refresh and try again."
        },
        409,
    )


@app.get("/api/health")
def health():
    with Session() as s:
        s.execute(select(1))
    return {
        "status": "ok",
        "mode": auth_mode(),
        "assistant_configured": configured(),
        "model": os.getenv("OPENAI_MODEL", ""),
        "actor": "Authenticated team"
        if os.getenv("CLERK_SECRET_KEY")
        else "Local estimator",
    }


def project_view(s, p):
    q = s.scalar(scoped(Quote).where(Quote.project_id == p.id))
    v = quote_view(s, q, p)
    events = list(
        s.scalars(
            scoped(Event)
            .where(Event.project_id == p.id)
            .order_by(Event.created_at.desc())
            .limit(1)
        )
    )
    pending = list(
        s.scalars(
            scoped(Proposal).where(
                Proposal.quote_id == q.id, Proposal.status == "pending"
            )
        )
    )
    return {
        "id": p.id,
        "title": p.title,
        "customer": p.customer,
        "category": p.category,
        "due_date": p.due_date,
        "synthetic": p.synthetic,
        "input_revision": p.input_revision,
        "color": p.color,
        "quote_id": q.id,
        "number": q.number,
        "status": q.status,
        "version": q.version,
        "total": v["total"],
        "blockers": len(v["checks"]),
        "pending": len(pending),
        "lines": len(v["lines"]),
        "updated_at": (events[0].created_at if events else p.created_at).isoformat(),
    }


def run_view(r):
    return {
        k: getattr(r, k)
        for k in (
            "id",
            "goal",
            "status",
            "base_version",
            "input_revision",
            "plan",
            "steps",
            "overlay",
            "questions",
            "answers",
            "result",
            "model",
        )
    }


@app.get("/api/projects", response_model=list[ProjectView])
def projects():
    with Session() as s:
        return [
            project_view(s, p)
            for p in s.scalars(
                scoped(Project)
                .where(
                    Project.id.not_in(scoped(Order).with_only_columns(Order.project_id))
                )
                .order_by(Project.created_at.desc())
            )
        ]


@app.post("/api/projects", response_model=ProjectView)
def create_project(body: ProjectCreate, idempotency_key: str = Header()):
    if body.due_date:
        date.fromisoformat(body.due_date)
    with Session.begin() as s:
        # Serialize global creation retry keys without trusting browser identity.
        from sqlalchemy import text

        s.execute(text("SELECT pg_advisory_xact_lock(777001)"))
        return idempotent(
            s,
            "project:" + idempotency_key,
            body.model_dump(),
            lambda: project_view(s, new_project(s, body.model_dump())[0]),
        )


@app.get("/api/projects/{id}", response_model=WorkspaceView)
def workspace(id: str):
    with Session() as s:
        p = get(s, Project, id)
        q = s.scalar(scoped(Quote).where(Quote.project_id == p.id))
        ds = docs(s, p)
        return {
            "project": project_view(s, p),
            "quote": quote_view(s, q, p),
            "documents": [document_dict(d) for d in ds],
            "proposals": [
                proposal_dict(pr)
                for pr in s.scalars(
                    scoped(Proposal)
                    .where(Proposal.quote_id == q.id)
                    .order_by(Proposal.created_at.desc())
                )
            ],
            "sources": [
                {
                    "id": sp.id,
                    "document_id": sp.document_id,
                    "text": sp.text,
                    "location": sp.location,
                }
                for sp in s.scalars(
                    scoped(Span).where(Span.document_id.in_([d.id for d in ds]))
                )
            ],
            "requirements": [
                {
                    "id": r.id,
                    "tag": r.tag,
                    "attribute": r.attribute,
                    "value": r.value,
                    "sources": r.sources,
                }
                for r in s.scalars(
                    scoped(Requirement).where(
                        Requirement.project_id == p.id, Requirement.active == True
                    )
                )
            ],
            "history": [
                {
                    "id": v.id,
                    "version": v.version,
                    "input_revision": v.input_revision,
                    "summary": v.summary,
                    "actor": v.actor,
                    "at": v.created_at.isoformat(),
                    "total": str(
                        sum(Decimal(l["extended"] or "0") for l in v.snapshot["lines"])
                    ),
                }
                for v in s.scalars(
                    scoped(Version)
                    .where(Version.quote_id == q.id)
                    .order_by(Version.version.desc())
                )
            ],
            "events": [
                {
                    "id": e.id,
                    "summary": e.summary,
                    "kind": e.kind,
                    "actor": e.actor,
                    "meta": e.meta,
                    "at": e.created_at.isoformat(),
                }
                for e in s.scalars(
                    scoped(Event)
                    .where(Event.project_id == p.id)
                    .order_by(Event.created_at.desc())
                    .limit(100)
                )
            ],
            "runs": [
                run_view(r)
                for r in s.scalars(
                    scoped(Run)
                    .where(Run.quote_id == q.id)
                    .order_by(Run.created_at.desc())
                    .limit(10)
                )
            ],
            "clarifications": [
                clarification_view(c) for c in project_clarifications(s, p)
            ],
        }


@app.get("/api/work-queue")
def get_work_queue():
    with Session() as s:
        return work_queue(s)


@app.get("/api/projects/{id}/clarifications")
def get_clarifications(id: str):
    with Session() as s:
        p = get(s, Project, id)
        return [clarification_view(c) for c in project_clarifications(s, p)]


@app.post("/api/projects/{id}/clarifications")
def new_clarification(
    id: str, body: ClarificationCreate, idempotency_key: str = Header()
):
    with Session.begin() as s:
        p = get(s, Project, id, True)
        q = s.scalar(scoped(Quote).where(Quote.project_id == p.id).with_for_update())
        return idempotent(
            s,
            p.id + ":clarify:" + idempotency_key,
            body.model_dump(),
            lambda: clarification_view(
                create_clarification(s, q, p, body.model_dump())
            ),
        )


def lock_clarification(s, id):
    initial = get(s, Clarification, id)
    p = get(s, Project, initial.project_id, True)
    q = s.scalar(scoped(Quote).where(Quote.project_id == p.id).with_for_update())
    c = get(s, Clarification, id, True)
    s.refresh(c)
    return c, q, p


@app.patch("/api/clarifications/{id}")
def edit_clarification(
    id: str, body: ClarificationUpdate, idempotency_key: str = Header()
):
    with Session.begin() as s:
        c, q, p = lock_clarification(s, id)
        return idempotent(
            s,
            c.id + ":edit:" + idempotency_key,
            body.model_dump(exclude_unset=True),
            lambda: clarification_view(
                update_clarification(s, c, q, p, body.model_dump(exclude_unset=True))
            ),
        )


@app.post("/api/clarifications/{id}/answers")
def answer_clarification(
    id: str, body: ClarificationAnswer, idempotency_key: str = Header()
):
    with Session.begin() as s:
        c, q, p = lock_clarification(s, id)

        def action():
            check_revision(c, body.expected_revision)
            return clarification_view(
                record_answer(s, c, q, p, body.answer, body.source_ids)
            )

        return idempotent(
            s, c.id + ":answer:" + idempotency_key, body.model_dump(), action
        )


@app.post("/api/clarifications/{id}/resume", status_code=202)
def resume_clarification(
    id: str, body: ClarificationRevision, idempotency_key: str = Header()
):
    if not configured():
        fail(
            "The AI connection is not configured. You can record answers and review the quote manually.",
            503,
        )
    with Session.begin() as s:
        c, q, p = lock_clarification(s, id)

        def action():
            check_revision(c, body.expected_revision)
            run = queue_recheck(s, c, q, p, os.getenv("OPENAI_MODEL", ""))
            return {"clarification": clarification_view(c), "run_id": run.id}

        return idempotent(
            s, c.id + ":resume:" + idempotency_key, body.model_dump(), action
        )


@app.get("/api/quotes/{id}", response_model=QuoteView)
def get_quote(id: str):
    with Session() as s:
        q = get(s, Quote, id)
        return quote_view(s, q, get(s, Project, q.project_id))


@app.post("/api/quotes/{id}/commands", response_model=QuoteView)
def commands(id: str, body: Command, idempotency_key: str = Header()):
    with Session.begin() as s:
        q, p = lock_quote(s, id)

        def action():
            check_version(q, p, body.expected_version, body.expected_input_revision)
            summary = {
                "review_line": "Reviewed equipment",
                "set_quantity": "Updated quantity",
                "set_gross_margin": "Applied gross margin",
                "set_markup": "Applied markup",
                "add_line": "Added equipment",
                "reconcile_inputs": "Reconciled source revision",
            }.get(body.operations[0].type, "Updated quote")
            command(s, q, p, body.operations, summary)
            return quote_view(s, q, p)

        return idempotent(
            s, q.id + ":" + idempotency_key, body.model_dump(mode="json"), action
        )


@app.post("/api/proposals/{id}/accept", response_model=QuoteView)
def accept(id: str, body: VersionRequest, idempotency_key: str = Header()):
    with Session.begin() as s:
        initial = get(s, Proposal, id)
        q, p = lock_quote(s, initial.quote_id)
        pr = get(s, Proposal, id, True)

        def action():
            check_version(q, p, body.expected_version, body.expected_input_revision)
            if pr.status != "pending":
                fail(
                    "This proposal is no longer pending. Reanalyze against the current revision.",
                    409,
                )
            check_version(q, p, pr.base_version, pr.input_revision)
            command(
                s,
                q,
                p,
                [Operation.model_validate(op) for op in pr.operations],
                "Accepted: " + pr.title,
            )
            pr.status = "applied"
            return quote_view(s, q, p)

        return idempotent(
            s,
            q.id + ":" + idempotency_key,
            {"proposal": id, **body.model_dump()},
            action,
        )


@app.post("/api/proposals/{id}/reject")
def reject(id: str, body: VersionRequest, idempotency_key: str = Header()):
    with Session.begin() as s:
        initial = get(s, Proposal, id)
        q, p = lock_quote(s, initial.quote_id)
        pr = get(s, Proposal, id, True)

        def action():
            check_version(q, p, body.expected_version, body.expected_input_revision)
            if pr.status != "pending":
                fail("Proposal is no longer pending.", 409)
            pr.status = "rejected"
            pr.reason = body.reason
            s.add(
                Event(project_id=p.id, summary="Rejected: " + pr.title, kind="review")
            )
            return {"status": "rejected"}

        return idempotent(
            s, q.id + ":" + idempotency_key, {"reject": id, **body.model_dump()}, action
        )


@app.post("/api/quotes/{id}/approve", response_model=QuoteView)
def approve(id: str, body: VersionRequest, idempotency_key: str = Header()):
    with Session.begin() as s:
        q, p = lock_quote(s, id)

        def action():
            check_version(q, p, body.expected_version, body.expected_input_revision)
            problems = checks(s, q, p)
            if problems:
                fail(
                    {
                        "message": "Resolve the review items before approval.",
                        "checks": problems,
                    }
                )
            q.status = "approved"
            s.add(
                Approval(
                    quote_id=q.id,
                    version=q.version,
                    input_revision=p.input_revision,
                    actor=current_actor(),
                    reason=body.reason,
                )
            )
            s.add(
                Event(
                    project_id=p.id,
                    summary=f"Approved revision {q.version}",
                    kind="approval",
                )
            )
            s.flush()
            return quote_view(s, q, p)

        return idempotent(
            s, q.id + ":" + idempotency_key, {"approve": body.model_dump()}, action
        )


@app.post("/api/quotes/{id}/revert", response_model=QuoteView)
def revert(id: str, body: Revert, idempotency_key: str = Header()):
    with Session.begin() as s:
        q, p = lock_quote(s, id)

        def action():
            check_version(q, p, body.expected_version, body.expected_input_revision)
            v = s.scalar(
                scoped(Version).where(
                    Version.quote_id == q.id, Version.version == body.target_version
                )
            )
            if not v:
                fail("Revision not found.", 404)
            if v.input_revision != p.input_revision:
                fail(
                    "Sources changed since this revision. Use a new proposal to reconcile them.",
                    409,
                )
            for l in lines(s, q):
                s.delete(l)
            s.flush()
            for row in v.snapshot["lines"]:
                data = {
                    k: row[k]
                    for k in (
                        "id",
                        "position",
                        "tag",
                        "description",
                        "quantity",
                        "unit",
                        "model",
                        "cost",
                        "price",
                        "lead_time",
                        "meta",
                    )
                }
                s.add(Line(quote_id=q.id, **data, review="unreviewed"))
            q.terms = v.snapshot["terms"]
            invalidate(s, q)
            q.version += 1
            save_version(
                s, q, p, f"Restored revision {body.target_version} as a new draft"
            )
            return quote_view(s, q, p)

        return idempotent(
            s, q.id + ":" + idempotency_key, {"revert": body.model_dump()}, action
        )


def add_document(s, p, name, kind, data, metadata=None):
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        fail("Upload a PDF, CSV, XLSX, TXT, or EML file.")
    if not data or len(data) > 20 * 1024 * 1024:
        fail("Upload a nonempty file no larger than 20 MB.")
    if ext == ".pdf" and not data.startswith(b"%PDF"):
        fail("This file is not a valid PDF.")
    if ext == ".pdf":
        from io import BytesIO
        import pdfplumber

        try:
            with pdfplumber.open(BytesIO(data)) as pdf:
                page_count = len(pdf.pages)
        except Exception:
            fail("This PDF cannot be opened. Export an unlocked PDF and try again.")
        if page_count > 150:
            fail(
                "PDFs are limited to 150 pages. Split this package into clearly named sections before importing."
            )
    blob, sha = persist_blob(data, ext)
    d = Document(
        project_id=p.id,
        name=Path(name).name,
        kind=kind,
        blob=blob,
        sha256=sha,
        meta=metadata or {},
    )
    s.add(d)
    s.flush()
    p.input_revision += 1
    q = s.scalar(scoped(Quote).where(Quote.project_id == p.id).with_for_update())
    invalidate(s, q)
    s.add(
        Job(
            project_id=p.id,
            kind="parse",
            payload={"document_id": d.id},
            key="parse:" + d.id + ":v1",
        )
    )
    s.add(Event(project_id=p.id, summary="Uploaded " + d.name, kind="upload"))
    s.flush()
    from backend.orders.service import refresh_project_orders

    refresh_project_orders(s, p.id)
    return document_dict(d)


@app.post("/api/projects/{id}/documents", status_code=202)
async def upload(
    id: str,
    file: UploadFile = File(),
    kind: Literal[
        "auto",
        "schedule",
        "specification",
        "offer",
        "addendum",
        "catalog",
        "purchase_order",
        "approval_drawing",
        "markups",
        "accepted_exception",
        "bom",
        "supplier_po",
        "nameplate",
    ] = Form("schedule"),
    revision_label: str = Form("", max_length=40),
    idempotency_key: str = Header(),
):
    data = await file.read(20 * 1024 * 1024 + 1)
    with Session.begin() as s:
        p = get(s, Project, id, True)
        return idempotent(
            s,
            p.id + ":upload:" + idempotency_key,
            {
                "name": file.filename,
                "kind": kind,
                "revision_label": revision_label,
                "hash": hashlib.sha256(data).hexdigest(),
            },
            lambda: add_document(
                s,
                p,
                file.filename or "document.txt",
                kind,
                data,
                {
                    **({"revision_label": revision_label} if revision_label else {}),
                    **(
                        {"order_role": kind}
                        if kind
                        in {
                            "specification",
                            "purchase_order",
                            "approval_drawing",
                            "markups",
                            "accepted_exception",
                            "bom",
                            "supplier_po",
                            "nameplate",
                        }
                        else {}
                    ),
                },
            ),
        )


@app.post("/api/projects/{id}/text", status_code=202)
def pasted(id: str, body: TextInput, idempotency_key: str = Header()):
    date.fromisoformat(body.date)
    with Session.begin() as s:
        p = get(s, Project, id, True)
        return idempotent(
            s,
            p.id + ":text:" + idempotency_key,
            body.model_dump(),
            lambda: add_document(
                s,
                p,
                body.name + ".txt",
                body.kind,
                body.text.encode(),
                {"sender": body.sender, "date": body.date},
            ),
        )


@app.post("/api/documents/{id}/mapping")
def mapping(id: str, body: Mapping, idempotency_key: str = Header()):
    with Session.begin() as s:
        initial = get(s, Document, id)
        p = get(s, Project, initial.project_id, True)
        q = s.scalar(scoped(Quote).where(Quote.project_id == p.id).with_for_update())
        d = get(s, Document, id, True)

        def action():
            if body.expected_input_revision != p.input_revision:
                fail("Input revision changed. Refresh before mapping.", 409)
            if d.state not in ("needs_mapping", "mapped"):
                fail("Wait for the document to finish parsing.")
            pr = map_document(s, d, q, p, body.mapping, body.purpose)
            s.add(
                Event(
                    project_id=p.id,
                    summary="Confirmed columns for " + d.name,
                    kind="review",
                )
            )
            return {"proposal_id": pr, "status": d.state}

        return idempotent(
            s, p.id + ":mapping:" + idempotency_key, body.model_dump(), action
        )


@app.get("/api/documents/{id}/content")
def content(id: str):
    with Session() as s:
        d = get(s, Document, id)
        return FileResponse(
            BLOBS / d.blob, filename=d.name, content_disposition_type="inline"
        )


@app.get("/api/sources/{id}")
def source(id: str):
    with Session() as s:
        sp = get(s, Span, id)
        return {
            "id": sp.id,
            "text": sp.text,
            "location": sp.location,
            "document": document_dict(get(s, Document, sp.document_id)),
        }


@app.post("/api/quotes/{id}/agent-runs", status_code=202)
def start_run(id: str, body: RunRequest, idempotency_key: str = Header()):
    if not configured():
        fail(
            "The AI connection is not configured. Mapped imports and manual edits are available.",
            503,
        )
    with Session.begin() as s:
        q, p = lock_quote(s, id)

        def action():
            for lineid in body.selected_lines:
                if get(s, Line, lineid).quote_id != q.id:
                    fail("Selected line belongs to another quote.", 403)
            r = Run(
                quote_id=q.id,
                goal=body.goal
                + (
                    "\nSelected line IDs: " + ", ".join(body.selected_lines)
                    if body.selected_lines
                    else ""
                ),
                base_version=q.version,
                input_revision=p.input_revision,
                model=os.getenv("OPENAI_MODEL", ""),
            )
            s.add(r)
            s.flush()
            s.add(
                Job(
                    project_id=p.id,
                    kind="agent",
                    payload={"run_id": r.id},
                    key="agent:" + r.id,
                )
            )
            return run_view(r)

        return idempotent(
            s, q.id + ":run:" + idempotency_key, body.model_dump(), action
        )


@app.get("/api/agent-runs/{id}")
def agent_run(id: str):
    with Session() as s:
        return run_view(get(s, Run, id))


@app.post("/api/agent-runs/{id}/answers")
def answer(id: str, body: Answer, idempotency_key: str = Header()):
    with Session.begin() as s:
        initial = get(s, Run, id)
        q, p = lock_quote(s, initial.quote_id)
        r = get(s, Run, id, True)

        def action():
            if r.status != "waiting_for_input":
                fail("This run is not waiting for an answer.", 409)
            check_version(q, p, r.base_version, r.input_revision)
            p.input_revision += 1
            r.input_revision = p.input_revision
            invalidate(s, q)
            r.answers = [
                *r.answers,
                {
                    "answer": body.answer,
                    "actor": current_actor(),
                    "at": now().isoformat(),
                    "origin": "human entered",
                },
            ]
            # The legacy assistant answer route also updates durable issues.
            for issue in project_clarifications(s, p):
                if issue.run_id == r.id and issue.status in (
                    "draft",
                    "awaiting_reply",
                    "answered",
                ):
                    issue.answer = body.answer.strip()
                    issue.status = "answered"
                    issue.answer_source_ids = []
                    issue.resolution_note = ""
                    touch(issue)
            r.status = "queued"
            r.questions = []
            s.add(
                Job(
                    project_id=p.id,
                    kind="agent",
                    payload={"run_id": r.id},
                    key=f"agent:{r.id}:answer:{len(r.answers)}",
                )
            )
            s.add(
                Event(
                    project_id=p.id,
                    summary="Answered assistant clarification",
                    kind="answer",
                )
            )
            return run_view(r)

        return idempotent(
            s, q.id + ":answer:" + idempotency_key, body.model_dump(), action
        )


@app.post("/api/agent-runs/{id}/cancel")
def cancel(id: str, idempotency_key: str = Header()):
    with Session.begin() as s:
        r = get(s, Run, id, True)
        if r.status not in ("completed", "ready_for_review", "failed"):
            r.status = "cancelled"
        return run_view(r)


@app.get("/api/catalog")
def catalog():
    with Session() as s:
        return [
            {
                "id": c.id,
                "manufacturer": c.manufacturer,
                "model": c.model,
                "description": c.description,
                "attributes": c.attributes,
                "synthetic": c.synthetic,
                "offers": [
                    {
                        "id": o.id,
                        "supplier": o.supplier,
                        "cost": str(o.cost),
                        "unit": o.unit,
                        "currency": o.currency,
                        "valid_until": o.valid_until,
                        "lead_time": o.lead_time,
                    }
                    for o in s.scalars(scoped(Offer).where(Offer.catalog_id == c.id))
                ],
            }
            for c in s.scalars(scoped(Catalog).order_by(Catalog.model))
        ]


@app.get("/api/quotes/{id}/preview")
def preview(id: str):
    with Session() as s:
        q = get(s, Quote, id)
        p = get(s, Project, q.project_id)
        return customer_snapshot(snapshot(s, q, p)) | {
            "approved": q.status == "approved",
            "checks": checks(s, q, p),
        }


@app.post("/api/quotes/{id}/exports")
def create_export(
    id: str,
    body: VersionRequest,
    format: Literal["pdf", "xlsx"] = "pdf",
    idempotency_key: str = Header(),
):
    with Session.begin() as s:
        q, p = lock_quote(s, id)

        def action():
            check_version(q, p, body.expected_version, body.expected_input_revision)
            approved = s.scalar(
                scoped(Approval).where(
                    Approval.quote_id == q.id,
                    Approval.version == q.version,
                    Approval.input_revision == p.input_revision,
                )
            )
            if q.status != "approved" or not approved or checks(s, q, p):
                fail(
                    "Approve this exact revision and resolve current checks before exporting."
                )
            v = s.scalar(
                scoped(Version).where(
                    Version.quote_id == q.id, Version.version == q.version
                )
            )
            data = customer_snapshot(v.snapshot)
            output = (pdf if format == "pdf" else xlsx)(data)
            blob, sha = persist_blob(output, "." + format)
            export = Export(
                quote_id=q.id, version_id=v.id, format=format, blob=blob, sha256=sha
            )
            s.add(export)
            s.flush()
            s.add(
                Event(
                    project_id=p.id,
                    summary=f"Exported approved revision {q.version} as {format.upper()}",
                    kind="export",
                )
            )
            return {
                "id": export.id,
                "url": "/api/exports/" + export.id,
                "version": q.version,
            }

        return idempotent(
            s,
            q.id + ":export:" + idempotency_key,
            {"format": format, **body.model_dump()},
            action,
        )


@app.get("/api/exports/{id}")
def download(id: str):
    with Session() as s:
        e = get(s, Export, id)
        q = get(s, Quote, e.quote_id)
        v = get(s, Version, e.version_id)
        return FileResponse(
            BLOBS / e.blob, filename=f"{q.number}-R{v.version:02d}.{e.format}"
        )


@app.get("/api/jobs/{id}")
def job(id: str):
    with Session() as s:
        j = get(s, Job, id)
        return {
            "id": j.id,
            "status": j.status,
            "kind": j.kind,
            "error": j.error,
            "attempts": j.attempts,
        }


# Engineering orders are first-class, versioned workspaces. Existing quote and
# project routes remain available for legacy bids and immutable source storage.
from backend.orders.api import router as orders_router

app.include_router(orders_router)

from backend.records.api import router as records_router

app.include_router(records_router)

from backend.email_api import router as email_router

app.include_router(email_router)
