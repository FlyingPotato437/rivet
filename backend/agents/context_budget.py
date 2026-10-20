"""Token-based request sizing and explicit, non-destructive observation compaction."""

import json
from functools import lru_cache

import tiktoken


@lru_cache(maxsize=16)
def encoder(model):
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        # New model names can precede tokenizer releases. Provider usage remains
        # authoritative; this uses a real BPE encoding rather than characters.
        return tiktoken.get_encoding("o200k_base")


def token_count(value, model=""):
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, default=str)
    )
    return len(encoder(model).encode(text, disallowed_special=()))


def compact_arguments(value):
    if isinstance(value, dict):
        return {k: compact_arguments(v) for k, v in list(value.items())[:30]}
    if isinstance(value, list):
        return [compact_arguments(v) for v in value[:8]]
    return value[:240] if isinstance(value, str) else value


def compact_observation(turn):
    """Keep retrieval identities and limitations; never invent an absent finding."""
    call = next((i for i in turn if i.get("type") == "function_call"), {})
    output = next((i for i in turn if i.get("type") == "function_call_output"), {})
    try:
        args = json.loads(call.get("arguments", "{}"))
        result = json.loads(output.get("output", "{}"))
    except (TypeError, ValueError):
        return {
            "tool": call.get("name"),
            "coverage": "Original observation retained in durable steps; re-read before relying on details.",
        }
    source_ids = set()

    def walk(v):
        if isinstance(v, dict):
            for k, item in v.items():
                if k in {"source_id", "id"} and isinstance(item, str):
                    source_ids.add(item)
                elif k in {"source_ids", "evidence_ids"} and isinstance(item, list):
                    source_ids.update(x for x in item if isinstance(x, str))
                else:
                    walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(result)
    return {
        "tool": call.get("name"),
        "arguments": compact_arguments(args),
        "source_ids": sorted(source_ids)[:32],
        "source_count": len(source_ids),
        "summary": str(result.get("summary", result.get("note", "")))[:1000]
        if isinstance(result, dict)
        else "",
        "coverage": {
            k: result[k]
            for k in (
                "coverage",
                "next_offset",
                "total",
                "error",
                "note",
                "limitations",
                "staged",
                "proposal_id",
            )
            if isinstance(result, dict) and k in result
        },
        "original_retained": True,
        "instruction": "Text was compacted, not rechecked. Read its source if exact contents are needed. Omission never proves a comment unresolved or a drawing missing.",
    }
