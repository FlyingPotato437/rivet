"""Function-number controls are not included in real-document accuracy."""

from backend.equipment.tag_semantics import describe_tag, match_tag


def test_standard_number_knowledge_preserves_raw_tag_and_local_uncertainty():
    assert describe_tag("52-12")["device_class"] == "ac_circuit_breaker"
    assert describe_tag("BKR-12")["local_identifier"] == "12"
    assert describe_tag("FDR 12")["device_class"] == "feeder_circuit"
    assert describe_tag("86T")["device_class"] == "lockout_relay"
    assert describe_tag("1086M")["function_number"] is None
    assert describe_tag("FDR 12")["original_text"] == "FDR 12"


def test_matching_never_conflates_feeder_breaker_and_lockout_by_number():
    candidate = match_tag("52-12", "BKR-12")
    assert candidate["status"] == "candidate_needs_confirmation"
    assert candidate["matched"] is False
    assert match_tag("52-12", "FDR 12")["matched"] is False
    assert match_tag("86T", "1086M")["matched"] is False
    assert (
        match_tag("52-12", "BKR-12", explicit_aliases=[("52-12", "BKR-12")])["matched"]
        is True
    )
