"""Device-function context is not proof that two source tags are one device."""

import re
import unicodedata

FUNCTION_SOURCE = "https://www.eaton.com/content/dam/eaton/products/design-guides---consultant-audience/canada/cag/eaton-power-distribution-systems-consulting-application-guide-tb08104003e-tab-1-ca08104001e-ca.pdf"


def describe_tag(raw):
    original = str(raw)
    normalized = " ".join(unicodedata.normalize("NFKC", original).upper().split())
    normalized = normalized.translate(str.maketrans({"‐": "-", "‑": "-", "–": "-"}))
    result = {
        "original_text": original,
        "normalized_text": normalized,
        "device_class": "unknown",
        "function_number": None,
        "local_identifier": None,
        "knowledge_source": None,
        "identity_verified": False,
    }
    match = re.fullmatch(r"(52|86)(?:[-_ ]([A-Z0-9-]+)|([A-Z][A-Z0-9-]*))?", normalized)
    if match:
        result.update(
            device_class="ac_circuit_breaker" if match[1] == "52" else "lockout_relay",
            function_number=int(match[1]),
            local_identifier=match[2] or match[3],
            knowledge_source=FUNCTION_SOURCE,
        )
        return result
    match = re.fullmatch(r"(BKR|CB|FDR)[-_ ]?(\d+[A-Z]?)", normalized)
    if match:
        result.update(
            device_class="feeder_circuit" if match[1] == "FDR" else "circuit_breaker",
            local_identifier=match[2],
        )
    # 1086M contains 86 but is not the standard function number 86. A local
    # legend or explicit company alias is needed before assigning its meaning.
    return result


def match_tag(left, right, *, explicit_aliases=()):
    a, b = describe_tag(left), describe_tag(right)
    pair = {a["normalized_text"], b["normalized_text"]}
    if a["normalized_text"] == b["normalized_text"]:
        return {"status": "exact", "matched": True, "left": a, "right": b}
    for alias in explicit_aliases:
        if (
            len(alias) == 2
            and {describe_tag(t)["normalized_text"] for t in alias} == pair
        ):
            return {
                "status": "explicit_source_alias",
                "matched": True,
                "left": a,
                "right": b,
            }
    breaker = {"circuit_breaker", "ac_circuit_breaker"}
    compatible = a["device_class"] in breaker and b["device_class"] in breaker
    if (
        compatible
        and a["local_identifier"]
        and a["local_identifier"] == b["local_identifier"]
    ):
        return {
            "status": "candidate_needs_confirmation",
            "matched": False,
            "left": a,
            "right": b,
            "reason": "Compatible breaker names share a suffix; confirm the sheet, section and manufacturer legend before merging.",
        }
    return {"status": "distinct_or_unknown", "matched": False, "left": a, "right": b}
