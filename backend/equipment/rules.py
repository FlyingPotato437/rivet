"""Small, explicit data constraints on observed equipment properties.

These are consistency checks, not an electrical code or manufacturer approval.
No jurisdiction, protection setting, compatibility, or design choice is inferred.
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation


def evaluate(analysis):
    groups = defaultdict(lambda: defaultdict(list))
    for fact in analysis.get("facts", []):
        if fact.get("role") in {"drawing", "bom"}:
            groups[(fact["device_tag"], fact["role"])][fact["field"]].append(fact)
    checks = []

    def number(facts):
        values = {str(fact["value"]) for fact in facts}
        if len(values) != 1:
            return None
        try:
            value = Decimal(next(iter(values)))
            return value if value.is_finite() else None
        except (InvalidOperation, ValueError):
            return None

    def append(tag, role, key, status, reason, evidence):
        checks.append(
            {
                "id": f"{tag}:{role}:{key}",
                "device_tag": tag,
                "role": role,
                "rule_id": key,
                "status": status,
                "reason": reason,
                "evidence": evidence,
            }
        )

    for (tag, role), fields in sorted(groups.items()):
        if fields.get("trip_a") and fields.get("frame_a"):
            evidence = fields["trip_a"] + fields["frame_a"]
            trip, frame = number(fields["trip_a"]), number(fields["frame_a"])
            status = (
                "unknown"
                if trip is None or frame is None
                else "pass"
                if trip <= frame
                else "fail"
            )
            append(
                tag,
                role,
                "trip_within_frame",
                status,
                "Trip current must not exceed the declared frame current. Conflicting or nonnumeric values cannot be checked.",
                evidence,
            )
        for field in (
            "frame_a",
            "trip_a",
            "control_voltage_v",
            "interrupting_ka",
            "quantity",
        ):
            if not fields.get(field):
                continue
            value = number(fields[field])
            status = (
                "unknown"
                if value is None
                else "pass"
                if value > 0
                and (field != "quantity" or value == value.to_integral_value())
                else "fail"
            )
            append(
                tag,
                role,
                "positive_integer_quantity"
                if field == "quantity"
                else f"positive_{field}",
                status,
                "Quantity must be a positive integer."
                if field == "quantity"
                else "The declared equipment rating must be positive.",
                fields[field],
            )
    return {
        "checks": checks,
        "failed": sum(c["status"] == "fail" for c in checks),
        "unknown": sum(c["status"] == "unknown" for c in checks),
        "scope": "Observed data consistency only; no electrical-code, compatibility or manufacturer certification checks.",
    }
