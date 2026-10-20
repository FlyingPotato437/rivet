"""Human transcription labels, separate from engineering or workflow approval."""

import hashlib
import json


def correction_example(comment, document, sources, corrected_text, actor, at):
    """Retain the original input, human label and exact immutable source anchors.

    Labels remain inside the tenant's order record. Saving one never enables
    cross-company training, sends a reply, or marks engineering as reviewed.
    """
    selected = [s for s in sources if s["id"] in comment.get("source_ids", [])]
    if not selected or any(s["document_id"] != document.id for s in selected):
        raise ValueError("The transcription needs its original order source.")
    corrected = corrected_text.strip()
    if not corrected or corrected == comment.get("text", "").strip():
        raise ValueError("Enter a corrected transcription before saving.")
    original = comment.get("original_text") or comment.get("text", "")
    label = {
        "schema_version": "rivet.transcription-example.v1",
        "comment_id": comment["id"],
        "document_id": document.id,
        "source_sha256": document.sha256,
        "source_page": comment.get("source_page"),
        "source_locations": [
            {"source_id": s["id"], "location": s["location"]} for s in selected
        ],
        "original_extracted_text": original,
        "previous_text": comment.get("text", ""),
        "human_text": corrected,
        "labelled_by": actor,
        "labelled_at": at,
        "label_type": "human_transcription_correction",
        "engineering_approved": False,
        "cross_company_training_allowed": False,
    }
    label["id"] = hashlib.sha256(
        json.dumps(
            [comment["id"], document.sha256, label["previous_text"], corrected],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()[:32]
    return label
