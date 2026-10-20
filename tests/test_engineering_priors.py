"""Synthetic provenance controls, not evidence of learned manufacturer accuracy."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest

from backend.engineering.configurator import configure
from backend.engineering.priors import PriorScopeError, derive_layout_priors


@pytest.fixture(autouse=True)
def database():
    yield


def job(order_id="past-1", section_type="iline-48-2000", *, org="company-A", version=3):
    model = {
        "model_id": order_id,
        "model_version": version,
        "manufacturer": "Schneider Electric",
        "family": "Square D Power-Style QED-2",
        "sections": [
            {
                "id": "S1",
                "kind": "main",
                "section_type_id": "individual-main",
                "positions": [],
            },
            {
                "id": "S2",
                "kind": "distribution",
                "section_type_id": section_type,
                "positions": [{"id": "spare-1", "status": "prepared_space"}],
                "spare_space_in": 9,
            },
            {
                "id": "S3",
                "kind": "metering",
                "section_type_id": "metering-fixture",
                "positions": [],
            },
        ],
        "devices": [
            {"id": "D1", "section_id": "S2", "frame": "LJ"},
            {"id": "D2", "section_id": "S2", "frame": "LJ"},
        ],
        "engineering_approved": False,
    }
    digest = sha256(json.dumps(model, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "order_id": order_id,
        "organization_id": org,
        "synthetic": False,
        "model_version": version,
        "selected_design": model,
        "engineering_approved": True,
        "approval": {
            "kind": "engineer_recorded_approval",
            "actor": "authenticated-engineer",
            "signer": "Engineer fixture",
            "reason": "Software provenance control only",
            "at": "2026-10-18T12:00:00+00:00",
            "model_version": version,
            "model_hash": digest,
        },
        "source_refs": [
            {
                "order_id": order_id,
                "organization_id": org,
                "revision_id": f"revision-{version}",
                "checksum": "a" * 64,
                "model_hash": digest,
                "model_version": version,
            }
        ],
    }


def derive(jobs, **kwargs):
    return derive_layout_priors(
        jobs,
        manufacturer="Schneider Electric",
        family="Square D Power-Style QED-2",
        **kwargs,
    )


def test_empty_history_does_not_invent_learning():
    assert derive([]) == []
    assert derive([], organization_id="company-A") == []


def test_cited_approval_produces_bounded_explainable_rank_only_prior():
    prior = derive([job()])[0]
    assert prior["preferred_section_type"] == "iline-48-2000"
    assert prior["weight"] == 1
    assert prior["counts"] == {
        "supporting_jobs": 1,
        "eligible_approved_jobs": 1,
        "approved_sections": 1,
    }
    assert (
        prior["source_ref"]["approved_models"][0]["model_hash"]
        == job()["approval"]["model_hash"]
    )
    assert prior["effect"] == "rank_only" and not prior["can_override_rules"]
    assert not prior["model_weight_training"]
    assert "1 of 1 distinct approved" in prior["explanation"]
    assert prior["observation_counts"] == {
        "same_frame_group": 1,
        "explicit_prepared_positions": 1,
        "explicit_spare_space": 1,
        "metering_position": 1,
    }
    assert all(
        observation["effect"] == "explanation_only"
        for observation in prior["layout_observations"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "synthetic",
        "unknown-synthetic",
        "unapproved",
        "not-recorded",
        "edited-model",
        "stale-version",
        "missing-citation",
        "foreign-citation",
        "catalog-only",
        "bad-hash",
        "no-signer",
        "no-timezone",
        "foreign-family",
        "foreign-maker",
    ],
)
def test_unverified_or_unrelated_snapshot_cannot_become_memory(mutation):
    item = job()
    if mutation == "synthetic":
        item["synthetic"] = True
    elif mutation == "unknown-synthetic":
        item.pop("synthetic")
    elif mutation == "unapproved":
        item["engineering_approved"] = False
    elif mutation == "not-recorded":
        item["approval"]["kind"] = "model_claimed_approval"
    elif mutation == "edited-model":
        item["selected_design"]["sections"][1]["section_type_id"] = "iline-54-3000"
    elif mutation == "stale-version":
        item["model_version"] = 4
    elif mutation == "missing-citation":
        item["source_refs"] = []
    elif mutation == "foreign-citation":
        item["source_refs"][0]["order_id"] = "other-job"
    elif mutation == "catalog-only":
        item["source_refs"] = [
            {"source_id": "manufacturer-catalog", "sha256": "b" * 64, "page": 54}
        ]
    elif mutation == "bad-hash":
        item["approval"]["model_hash"] = "b" * 64
    elif mutation == "no-signer":
        item["approval"].pop("signer")
    elif mutation == "no-timezone":
        item["approval"]["at"] = "2026-10-18T12:00:00"
    elif mutation == "foreign-family":
        item["selected_design"]["family"] = "Other product family"
    else:
        item["selected_design"]["manufacturer"] = "Other manufacturer"
    assert derive([item]) == []


def test_company_scope_is_enforced_before_aggregation():
    first, second = job(), job("past-B", org="company-B")
    with pytest.raises(PriorScopeError):
        derive([first, second])
    priors = derive([first, second], organization_id="company-A")
    assert priors[0]["counts"]["eligible_approved_jobs"] == 1
    assert priors[0]["organization_id"] == "company-A"


def test_each_job_counts_once_and_latest_recorded_approval_replaces_old_revision():
    first = job("same", section_type="iline-42-2000", version=1)
    latest = job("same", section_type="iline-48-2000", version=2)
    latest["approval"]["at"] = "2026-10-19T12:00:00+00:00"
    priors = derive(
        [first, latest, deepcopy(latest), job("other", section_type="iline-48-2000")]
    )
    assert len(priors) == 1
    assert priors[0]["counts"]["eligible_approved_jobs"] == 2
    assert priors[0]["counts"]["supporting_jobs"] == 2
    assert {ref["order_id"] for ref in priors[0]["source_ref"]["approved_models"]} == {
        "same",
        "other",
    }


def test_same_order_equal_time_conflicting_approvals_are_ambiguous():
    assert (
        derive(
            [
                job("same", section_type="iline-42-2000"),
                job("same", section_type="iline-48-2000"),
            ]
        )
        == []
    )


def test_section_frequency_is_by_job_not_number_of_repeated_sections():
    first = job("large")
    first["selected_design"]["sections"].extend(
        {
            "id": f"EXTRA-{index}",
            "kind": "distribution",
            "section_type_id": "iline-48-2000",
        }
        for index in range(10)
    )
    digest = sha256(
        json.dumps(first["selected_design"], sort_keys=True, default=str).encode()
    ).hexdigest()
    first["approval"]["model_hash"] = digest
    first["source_refs"][0]["model_hash"] = digest
    priors = derive([first, job("small", section_type="iline-42-2000")])
    assert {item["weight"] for item in priors} == {0.5}
    assert (
        next(
            item for item in priors if item["preferred_section_type"] == "iline-48-2000"
        )["counts"]["approved_sections"]
        == 11
    )


def test_existing_configurator_can_use_prior_but_not_override_width_limit():
    req = {
        "service_rating_a": 600,
        "voltage_v": 480,
        "current_type": "AC",
        "frequency_hz": 60,
        "phases": 3,
        "wires": 4,
        "metering": False,
        "spd": False,
        "confirmed": True,
        "main": {
            "kind": "circuit_breaker",
            "trip_a": 600,
            "poles": 3,
            "trip_type": "LI",
        },
        "feeders": [{"id": "F1", "trip_a": 225, "poles": 3, "trip_type": "LI"}],
        "constraints": {"enclosure": "NEMA1", "line_load_connection": "opposite_side"},
    }
    priors = derive([job(section_type="iline-48-2000")])
    result = configure(req, priors=priors)
    assert result["configurations"][0]["ranking"]["prior_bonus"] == 1
    assert any(
        note["effect"] == "rank_only"
        for note in result["configurations"][0]["ranking"]["priors"]
    )
    req["constraints"]["max_width_in"] = 72
    constrained = configure(req, priors=derive([job(section_type="iline-54-3000")]))
    assert all(
        design["sections"][1]["section_type_id"] != "iline-54-3000"
        for design in constrained["configurations"]
    )


def test_nested_engineering_snapshot_keeps_caller_wrapper_scope_and_sources():
    item = job()
    engineering = {
        key: item[key]
        for key in (
            "model_version",
            "selected_design",
            "approval",
            "engineering_approved",
        )
    }
    wrapped = {
        key: item[key]
        for key in ("order_id", "organization_id", "synthetic", "source_refs")
    }
    wrapped["state"] = {"engineering": engineering}
    assert derive([wrapped]) == derive([item])
