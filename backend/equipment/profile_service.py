"""Owned evidence and immutable company review for equipment conventions.

Uploading a guide never approves a rule. A separate authenticated company
review creates a new version; an order pins that version explicitly. Every
delivery revalidates the original bytes and the current revocation ledger.
"""

import math
import re
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

from pydantic import ValidationError

from backend.domain.service import digest, fail, get, scoped
from backend.identity import current_actor, current_identity, current_org
from backend.orders import service as orders
from backend.storage.models import Document, Order, now

from . import service
from .profile_models import (
    ManufacturerProfileRevocation as Revocation,
)
from .profile_models import (
    ManufacturerProfileVersion as Version,
)
from .profile_models import (
    OrderManufacturerProfile as Binding,
)
from .profiles import ProfileError, evaluate, seal_profile


def require_admin():
    if current_identity().role != "org:admin":
        fail("A team administrator must review company design conventions.", 403)


def _seal(raw):
    try:
        return seal_profile(raw)
    except (ValidationError, ProfileError, ValueError, TypeError) as exc:
        fail("The company profile is invalid: " + str(exc)[:1200])


def _quote(value):
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def verify_sources(s, profile):
    """Check a precise quoted PDF page against owned immutable source bytes.

    This first version accepts PDF guides only. Native measurements and BOM
    observations are evaluated separately from the actual saved output files.
    """
    import pymupdf as fitz

    catalog = {}
    for anchor in profile.sources:
        document = get(s, Document, anchor.document_id)
        path = service.original_path(document)
        actual_hash = sha256(path.read_bytes()).hexdigest()
        if actual_hash != document.sha256 or actual_hash != anchor.sha256:
            fail("A company rule source changed. Review a fresh source version.", 409)
        order = s.scalar(scoped(Order).where(Order.project_id == document.project_id))
        if not order:
            fail("Company rules must cite a source registered to an owned order.")
        info = service.document_info(document, order)
        if not info["active"] or (document.meta or {}).get("active") is False:
            fail(
                "A company rule guide is inactive or superseded. Review its replacement before reuse.",
                409,
            )
        if (
            anchor.name != document.name
            or anchor.revision != info["revision"]
            or anchor.role != info["role"]
        ):
            fail(
                "A company rule source does not match its registered name, role or revision.",
                409,
            )
        location = anchor.location
        page = location.get("page")
        quote = location.get("text")
        if (
            Path(document.name).suffix.lower() != ".pdf"
            or isinstance(page, bool)
            or not isinstance(page, int)
            or page < 1
            or not isinstance(quote, str)
            or len(_quote(quote)) < 8
        ):
            fail("Company rules need an exact PDF page and a quoted source passage.")
        try:
            with fitz.open(path) as pdf:
                if pdf.needs_pass or page > len(pdf):
                    fail("The quoted company rule page is unavailable.", 409)
                text = pdf[page - 1].get_text()
                if _quote(quote) not in _quote(text):
                    fail(
                        "The quoted company rule was not found on its cited source page.",
                        409,
                    )
                if "bbox" in location:
                    box = location["bbox"]
                    if (
                        not isinstance(box, list)
                        or len(box) != 4
                        or any(
                            isinstance(v, bool)
                            or not isinstance(v, (int, float))
                            or not math.isfinite(v)
                            or not 0 <= v <= 1
                            for v in box
                        )
                        or box[0] >= box[2]
                        or box[1] >= box[3]
                    ):
                        fail(
                            "A company rule crop must use a valid normalized page rectangle."
                        )
                    original_page = pdf[page - 1]
                    rect = original_page.rect
                    crop = fitz.Rect(
                        box[0] * rect.width,
                        box[1] * rect.height,
                        box[2] * rect.width,
                        box[3] * rect.height,
                    )
                    crop = crop * original_page.derotation_matrix
                    if _quote(quote) not in _quote(original_page.get_text(clip=crop)):
                        fail(
                            "The company rule quote was not found inside its cited page crop.",
                            409,
                        )
        except (RuntimeError, ValueError):
            fail("The company rule PDF could not be read.", 409)
        catalog[document.id] = actual_hash
    return catalog


def _revocation(s, version):
    return s.scalar(
        scoped(Revocation).where(Revocation.profile_version_id == version.id)
    )


def public(s, version):
    revoked = _revocation(s, version)
    urls = {}
    for source in version.manifest.get("sources", []):
        document = s.scalar(scoped(Document).where(Document.id == source["document_id"]))
        if not document:
            continue
        order = s.scalar(scoped(Order).where(Order.project_id == document.project_id))
        if order:
            urls[document.id] = (
                f"/orders/{order.id}/equipment/files/{quote(document.id, safe='')}"
            )
    return {
        "id": version.id,
        "profile_key": version.profile_key,
        "version_hash": version.version_hash,
        "parent_id": version.parent_id,
        "manifest": version.manifest,
        "created_at": version.created_at.isoformat(),
        "created_by": version.created_by,
        "source_urls": urls,
        "revocation": {
            "reason": revoked.reason,
            "reviewed_by": revoked.reviewed_by,
            "created_at": revoked.created_at.isoformat(),
        }
        if revoked
        else None,
    }


def overview(s):
    return {
        "profiles": [
            public(s, v)
            for v in s.scalars(
                scoped(Version)
                .order_by(Version.created_at.desc(), Version.id)
                .limit(200)
            )
        ]
    }


def propose(s, raw, parent_id=None):
    require_admin()
    payload = deepcopy(raw)
    payload.pop("version", None)
    payload.update(tenant_id=current_org(), status="proposed", review=None)
    for kind in ("aliases", "rules"):
        entries = payload.get(kind, [])
        if not isinstance(entries, list) or not all(
            isinstance(entry, dict) for entry in entries
        ):
            fail("Company rules and aliases must be lists of typed entries.")
        for entry in entries:
            entry["status"] = "proposed"
    profile = _seal(payload)
    if len(profile.profile_id) > 100:
        fail("The company profile ID must be at most 100 characters.")
    if parent_id:
        parent = get(s, Version, parent_id)
        if parent.profile_key != profile.profile_id:
            fail("The parent belongs to a different company profile.")
    verify_sources(s, profile)
    existing = s.scalar(scoped(Version).where(Version.version_hash == profile.version))
    if existing:
        return public(s, existing)
    row = Version(
        profile_key=profile.profile_id,
        version_hash=profile.version,
        manifest=profile.model_dump(mode="json"),
        parent_id=parent_id,
        created_by=current_actor(),
    )
    s.add(row)
    s.flush()
    return public(s, row)


def review(s, version_id, expected_hash):
    require_admin()
    row = get(s, Version, version_id, lock=True)
    if row.version_hash != expected_hash:
        fail("The company profile changed. Refresh before reviewing.", 409)
    profile = _seal(row.manifest)
    if profile.status != "proposed" or _revocation(s, row):
        fail("Only a current proposed profile can be reviewed.", 409)
    verify_sources(s, profile)
    payload = profile.model_dump(mode="json", exclude={"version"})
    payload.update(
        status="approved",
        review={
            "reviewer_id": current_actor(),
            "reviewed_at": now().isoformat(),
        },
    )
    for kind in ("aliases", "rules"):
        for item in payload[kind]:
            item["status"] = "approved"
    approved = _seal(payload)
    existing = s.scalar(
        scoped(Version).where(
            Version.parent_id == row.id, Version.manifest["status"].astext == "approved"
        )
    )
    if existing:
        return public(s, existing)
    result = Version(
        profile_key=row.profile_key,
        version_hash=approved.version,
        manifest=approved.model_dump(mode="json"),
        parent_id=row.id,
        created_by=current_actor(),
    )
    s.add(result)
    s.flush()
    return public(s, result)


def revoke(s, version_id, reason):
    require_admin()
    row = get(s, Version, version_id, lock=True)
    if not _revocation(s, row):
        s.add(
            Revocation(
                profile_version_id=row.id, reason=reason, reviewed_by=current_actor()
            )
        )
        s.flush()
    return public(s, row)


def context(s, order):
    """Return a pinned profile and its current evidence without concealing drift."""
    row = s.scalar(scoped(Binding).where(Binding.order_id == order.id))
    if not row or not row.profile_version_id:
        # Unbound legacy orders retain their existing plan/file fingerprints.
        return {
            "signature": None,
            "profile": None,
            "source_catalog": {},
            "issues": [],
            "binding_version": row.version if row else 0,
        }
    version = get(s, Version, row.profile_version_id)
    profile = _seal(version.manifest)
    revoked = _revocation(s, version)
    issues = []
    catalog = {}
    if profile.version != version.version_hash or profile.tenant_id != current_org():
        issues.append("The pinned company profile no longer matches its owned version.")
    if profile.status != "approved" or revoked:
        issues.append("The pinned company profile is unreviewed or revoked.")
    try:
        catalog = verify_sources(s, profile)
    except Exception as exc:
        from fastapi import HTTPException

        if not isinstance(exc, HTTPException):
            raise
        issues.append(str(exc.detail))
    return {
        "signature": digest(
            [
                version.id,
                version.version_hash,
                row.version,
                revoked.id if revoked else None,
                catalog,
                issues,
            ]
        ),
        "profile": profile,
        "profile_version_id": version.id,
        "source_catalog": catalog,
        "issues": issues,
        "binding_version": row.version,
    }


def binding_view(s, order):
    ctx = context(s, order)
    row = s.scalar(scoped(Binding).where(Binding.order_id == order.id))
    return {
        "order_id": order.id,
        "version": order.version,
        "binding_version": ctx["binding_version"],
        "profile_version_id": row.profile_version_id if row else None,
        "profile": public(s, get(s, Version, row.profile_version_id))
        if row and row.profile_version_id
        else None,
        "issues": ctx["issues"],
        "signature": ctx["signature"],
    }


def bind(s, order, expected_version, profile_version_id, reason):
    require_admin()
    orders.check_version(order, expected_version)
    if profile_version_id:
        version = get(s, Version, profile_version_id)
        profile = _seal(version.manifest)
        if profile.status != "approved" or _revocation(s, version):
            fail("Choose a current company-reviewed profile.", 409)
        verify_sources(s, profile)
    row = s.scalar(
        scoped(Binding).where(Binding.order_id == order.id).with_for_update()
    )
    if row:
        row.profile_version_id = profile_version_id
        row.version += 1
        row.confirmed_by, row.confirmed_at, row.reason = current_actor(), now(), reason
    else:
        s.add(
            Binding(
                order_id=order.id,
                profile_version_id=profile_version_id,
                confirmed_by=current_actor(),
                reason=reason,
            )
        )
    orders.commit(
        s,
        order,
        "Updated the company design conventions for this order.",
        "equipment_profile",
        {"profile_version_id": profile_version_id, "reason": reason},
    )
    s.flush()
    from . import workflow

    workflow.schedule(s, order)
    return binding_view(s, order)


def check_model(s, order, model, source_inputs):
    ctx = context(s, order)
    if ctx["profile"] is None:
        return None
    if ctx["issues"]:
        return {
            "status": "unknown",
            "active": False,
            "results": [],
            "blockers": ctx["issues"],
            "profile_version": ctx["profile"].version,
        }
    catalog = {**ctx["source_catalog"], **{v["id"]: v["sha256"] for v in source_inputs}}
    return evaluate(ctx["profile"], model, source_catalog=catalog).model_dump(
        mode="json"
    )
