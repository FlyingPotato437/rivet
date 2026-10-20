from scripts.panel_demo import build


def test_panel_demo_shows_added_scope_engineer_calls_and_prices(client):
    made = build()
    w = client.get(f"/api/orders/{made['order_id']}/record").json()
    kinds = {c["number"]: c["impact"]["kind"] for c in w["comments"]}
    assert kinds == {
        "1": "change",
        "2": "scope",
        "3": "question",
        "4": "change",
        "5": "scope",
        "6": "change",
        "7": "scope",
        "8": "no_action",
    }
    assert {d["revision_label"] for d in w["documents"] if "revision_label" in d} <= {
        "B"
    }
    summary = w["comment_impact"]
    assert summary["engineer"] == ["1", "3", "4", "5"]
    costs = {i["number"]: i["cost"] for i in summary["scope"]}
    assert (costs["2"]["tag"], costs["2"]["estimate"]) == ("BKR-20A-1P", "121.43")
    assert (costs["5"]["tag"], costs["5"]["billable"]) == (
        "SPD-1",
        "per accepted scope",
    )
    assert costs["5"]["estimate"] == "2013.89"
    assert costs["7"] == {"status": "needs_pricing"}
    assert summary["estimated_total"] == "2135.32" and summary["needs_pricing"] == 1
