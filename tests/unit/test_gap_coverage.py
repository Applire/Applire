# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-089 — the per-gap coverage record (``services/gap_coverage.py``).

Every function under test computes a FACT (ADR-062 clause 1): list membership,
a ledger status read, a count. The tests pin each clause's precedence with the
smallest shape that ONLY the rule under test decides, so a mutation of that
rule is killed by a named test:

* declined-before-covered (the #730 shape) — ``test_declared_denial_beats_a_direct_row``
* the #207 all-rows-direct veto — ``test_a_narrower_non_direct_row_vetoes_covered``
* RULING A-1 (an unstoried liability is open) — ``test_unstoried_liability_member_is_open``
  and ``test_liability_only_cluster_stays_askable``
* carry-forward orphan drop — ``test_refresh_drops_a_member_no_ledger_row_matches``
"""

import copy
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.services import gap_coverage as gc
from applire.services.gap_coverage import (
    AnswerScope,
    all_members,
    apply_turn_outcome,
    classify_members,
    derive_coverage,
    empty_outcome,
    initialise_cluster_record,
    is_askable,
    member_matches_ledger,
    member_statuses,
    record_turn_outcome,
    refresh_cluster_from_ledger,
    remaining_budget,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def row(concept, status, *, forms=None, sources=("required",), narrative=True):
    """One keyword-ledger row, shaped as ``build_keyword_ledger`` writes it."""
    return {
        "concept": concept,
        "surface_forms": list(forms) if forms is not None else [concept],
        "sources": list(sources),
        "fit_weight": 1.0 if "required" in sources else (0.5 if sources else 0.0),
        "status": status,
        "evidence": "vault says so" if status in ("direct", "partial") else "",
        "claimable": status in ("direct", "partial"),
        "narrative_backed": narrative,
    }


def cluster(cid="c1", gaps=("A",), *, outcome=None, coverage=None, category="C"):
    c = {
        "id": cid,
        "label": f"Label {cid}",
        "category": category,
        "gaps": list(gaps),
        "jd_skills": [],
        "jd_context": "context",
    }
    if outcome is not None:
        c["outcome"] = outcome
    if coverage is not None:
        c["coverage"] = coverage
    return c


def outcome(asked=0, covered=(), declined=(), session_ids=()):
    return {
        "asked": asked,
        "covered": list(covered),
        "declined": list(declined),
        "session_ids": list(session_ids),
    }


# ---------------------------------------------------------------------------
# AnswerScope / empty_outcome
# ---------------------------------------------------------------------------


def test_answer_scope_defaults_are_the_refresh_case():
    scope = AnswerScope()
    assert scope.cluster_ids == () and scope.answers == ()
    with pytest.raises(Exception):
        scope.cluster_ids = ("x",)  # frozen


def test_empty_outcome_is_a_fresh_dict_each_time():
    a = empty_outcome()
    a["covered"].append("X")
    assert empty_outcome() == {"asked": 0, "covered": [], "declined": [], "session_ids": []}


# ---------------------------------------------------------------------------
# all_members — the one full-vocabulary reader
# ---------------------------------------------------------------------------


def test_all_members_is_open_then_covered_then_declined():
    c = cluster(gaps=["Docker"], outcome=outcome(covered=["Python"], declined=["SAP"]))
    assert all_members(c) == ["Docker", "Python", "SAP"]


def test_all_members_dedupes_by_the_ledger_normaliser():
    c = cluster(gaps=["Docker", " docker "], outcome=outcome(covered=["DOCKER", "Go"]))
    assert all_members(c) == ["Docker", "Go"]


@pytest.mark.parametrize(
    "bad_outcome", [None, "x", [], {"covered": "Python"}, {"covered": [None, 3, " "]}]
)
def test_all_members_tolerates_legacy_and_malformed_outcomes(bad_outcome):
    c = cluster(gaps=["Docker", None, "", 7])
    c["outcome"] = bad_outcome
    assert all_members(c) == ["Docker"]


def test_all_members_of_a_non_dict_is_empty():
    assert all_members(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# classify_members — clause 2 precedence
# ---------------------------------------------------------------------------


def test_declared_denial_beats_a_direct_row():
    """#730 shape: the ledger still says `direct`, the candidate declined it."""
    ledger = [row("Agile methodologies", "direct")]
    denied = [{"concept": "Agile methodologies", "denial_level": "direct"}]
    assert classify_members(["Agile methodologies"], ledger, denied) == {
        "Agile methodologies": "declined"
    }


def test_denial_reaches_an_alias_of_the_member():
    denied = [{"concept": "Kubernetes"}]
    assert classify_members(["K8s"], [row("K8s", "gap")], denied) == {"K8s": "declined"}


def test_bare_string_denials_are_accepted():
    assert classify_members(["Docker"], [row("Docker", "direct")], ["Docker"]) == {
        "Docker": "declined"
    }


def test_containment_only_denial_does_not_decline_the_member():
    """SF-GAP.10 shape: a narrow denied compound is no denial of the broad term —
    the ledger (here: released, `direct`) decides."""
    ledger = [row("Produktion", "direct")]
    denied = [{"concept": "direkte Produktion für Lebensmittelkunden"}]
    assert classify_members(["Produktion"], ledger, denied) == {"Produktion": "covered"}


def test_own_row_already_denied_is_declined_without_a_denial_list():
    assert classify_members(["Docker"], [row("Docker", "denied")], None) == {
        "Docker": "declined"
    }


def test_a_substring_row_denied_does_not_decline_a_different_member():
    ledger = [row("Python", "direct"), row("Python async internals", "denied")]
    # "Python" matches both rows, but its OWN row is direct: not declined; the
    # narrower denied row vetoes covered (#207).
    assert classify_members(["Python"], ledger, None) == {"Python": "open"}


def test_single_direct_row_is_covered():
    assert classify_members(["Docker"], [row("Docker", "direct")], None) == {
        "Docker": "covered"
    }


def test_a_narrower_non_direct_row_vetoes_covered():
    """#207: `Python -> direct` must not silently cover a member whose
    narrower row `5+ years Python experience` is still a gap."""
    ledger = [row("Python", "direct"), row("5+ years Python experience", "gap")]
    facts = classify_members(["Python", "5+ years Python experience"], ledger, None)
    assert facts == {"Python": "open", "5+ years Python experience": "open"}


def test_member_is_matched_through_a_surface_form():
    ledger = [row("Kubernetes", "direct", forms=["Kubernetes", "Container-Orchestrierung"])]
    assert classify_members(["Container-Orchestrierung"], ledger, None) == {
        "Container-Orchestrierung": "covered"
    }


@pytest.mark.parametrize("status", ["partial", "gap"])
def test_partial_and_gap_rows_are_open(status):
    assert classify_members(["Docker"], [row("Docker", status)], None) == {"Docker": "open"}


def test_member_with_no_row_is_open_not_dropped():
    assert classify_members(["Rust"], [row("Docker", "direct")], None) == {"Rust": "open"}


def test_no_ledger_reads_open_but_still_honours_a_denial():
    facts = classify_members(["Docker", "SAP"], None, [{"concept": "SAP"}])
    assert facts == {"Docker": "open", "SAP": "declined"}


def test_blank_and_non_string_members_are_skipped():
    assert classify_members(["", "  ", None, "Docker"], [row("Docker", "direct")], None) == {  # type: ignore[list-item]
        "Docker": "covered"
    }


# RULING A-1 -----------------------------------------------------------------


def test_unstoried_liability_member_is_open():
    """A required, claimable concept with no story (#260) is not covered —
    its open question is the story."""
    ledger = [row("SAP PP", "direct", narrative=False)]
    assert classify_members(["SAP PP"], ledger, None) == {"SAP PP": "open"}


def test_a_storied_required_direct_member_is_covered():
    ledger = [row("SAP PP", "direct", narrative=True)]
    assert classify_members(["SAP PP"], ledger, None) == {"SAP PP": "covered"}


def test_an_unstoried_nice_to_have_is_not_a_liability():
    """`keyword_liabilities` scopes to required sources only — mirrored."""
    ledger = [row("Jira", "direct", sources=("nice_to_have",), narrative=False)]
    assert classify_members(["Jira"], ledger, None) == {"Jira": "covered"}


# ---------------------------------------------------------------------------
# member_statuses — RULING C-1 (the chip colour per requirement)
# ---------------------------------------------------------------------------

_C1_LEDGER = [
    row("Docker", "direct"),
    row("Helm", "partial"),
    row("Terraform", "gap"),
    row("SAP PP", "direct", narrative=False),  # unstoried liability → partial
    row("Ansible", "denied"),
    row("Python", "direct"),
    row("5+ years Python experience", "gap"),  # #207 veto on "Python"
]
_C1_MEMBERS = [
    "Docker", "Helm", "Terraform", "SAP PP", "Ansible", "Python", "Pulumi", "Kubernetes",
]
_C1_DENIED = [{"concept": "Kubernetes"}]


def test_member_statuses_four_way_precedence():
    c = cluster(gaps=_C1_MEMBERS)
    got = {m["member"]: m["status"] for m in member_statuses(c, _C1_LEDGER, _C1_DENIED)}
    assert got == {
        "Docker": "covered",
        "Helm": "partial",
        "Terraform": "gap",
        "SAP PP": "partial",
        "Ansible": "declined",
        "Python": "partial",
        "Pulumi": "gap",
        "Kubernetes": "declined",
    }


def test_member_statuses_agree_with_classify_members():
    """covered/declined agree; partial + gap is exactly `open`."""
    facts = classify_members(_C1_MEMBERS, _C1_LEDGER, _C1_DENIED)
    statuses = member_statuses(cluster(gaps=_C1_MEMBERS), _C1_LEDGER, _C1_DENIED)
    for item in statuses:
        expected = item["status"] if item["status"] in ("covered", "declined") else "open"
        assert facts[item["member"]] == expected, item


def test_member_statuses_cover_every_member_in_all_members_order():
    c = cluster(gaps=["Helm"], outcome=outcome(covered=["Docker"], declined=["Ansible"]))
    got = member_statuses(c, _C1_LEDGER)
    assert [m["member"] for m in got] == ["Helm", "Docker", "Ansible"]
    assert [m["status"] for m in got] == ["partial", "covered", "declined"]


def test_a_recorded_decline_stays_declined_whatever_the_row_says():
    c = cluster(gaps=[], outcome=outcome(declined=["Docker"]))
    assert member_statuses(c, [row("Docker", "direct")]) == [
        {"member": "Docker", "status": "declined"}
    ]


def test_member_statuses_of_a_non_dict_is_empty():
    assert member_statuses(None, _C1_LEDGER) == []  # type: ignore[arg-type]


def test_response_carries_member_statuses_and_budget_but_never_persists_them():
    from applire.schemas.gap_cluster import LLM_CLUSTER_KEYS, GapClusterSchema

    c = GapClusterSchema.model_validate(
        {**cluster(gaps=["Docker"]), "budget_remaining": 99, "member_statuses": [{"member": "X", "status": "gap"}]}
    )
    assert c.budget_remaining == 2, "derived, an input value is ignored"
    assert set(c.model_dump(include=LLM_CLUSTER_KEYS)) == LLM_CLUSTER_KEYS


# ---------------------------------------------------------------------------
# member_matches_ledger — orphan detection
# ---------------------------------------------------------------------------


def test_member_matches_by_concept_surface_form_and_substring():
    ledger = [row("Kubernetes", "gap", forms=["Kubernetes", "K8s"])]
    assert member_matches_ledger("Kubernetes", ledger)
    assert member_matches_ledger("k8s", ledger)
    assert member_matches_ledger("Kubernetes-Betrieb", ledger)  # member ⊃ row
    assert member_matches_ledger("Kube", ledger)  # member ⊂ row
    assert not member_matches_ledger("Terraform", ledger)


@pytest.mark.parametrize("ledger", [None, [], ["junk", None]])
def test_member_matches_nothing_without_rows(ledger):
    assert not member_matches_ledger("Docker", ledger)


# ---------------------------------------------------------------------------
# derive_coverage — clause 3
# ---------------------------------------------------------------------------


def test_all_gap_members_are_open():
    ledger = [row("Docker", "gap"), row("Helm", "gap")]
    assert derive_coverage(cluster(gaps=["Docker", "Helm"]), ledger) == "open"


def test_unmatched_members_are_open():
    assert derive_coverage(cluster(gaps=["Rust"]), [row("Docker", "direct")]) == "open"


def test_a_category_b_cluster_starts_partly_covered():
    ledger = [row("Docker", "partial"), row("Helm", "partial")]
    assert (
        derive_coverage(cluster(gaps=["Docker", "Helm"], category="B"), ledger)
        == "partly_covered"
    )


def test_one_covered_one_open_is_partly_covered():
    c = cluster(gaps=["Helm"], outcome=outcome(covered=["Docker"]))
    assert derive_coverage(c, [row("Docker", "direct"), row("Helm", "gap")]) == "partly_covered"


def test_one_declined_one_open_is_partly_covered():
    c = cluster(gaps=["Helm"], outcome=outcome(declined=["Docker"]))
    assert derive_coverage(c, [row("Docker", "denied"), row("Helm", "gap")]) == "partly_covered"


def test_every_member_covered_is_covered():
    c = cluster(gaps=[], outcome=outcome(covered=["Docker", "Helm"]))
    assert derive_coverage(c, [row("Docker", "direct"), row("Helm", "direct")]) == "covered"


def test_covered_plus_declined_is_covered():
    c = cluster(gaps=[], outcome=outcome(covered=["Docker"], declined=["Helm"]))
    assert derive_coverage(c, [row("Docker", "direct"), row("Helm", "denied")]) == "covered"


def test_every_member_declined_is_declined():
    c = cluster(gaps=[], outcome=outcome(declined=["Docker", "Helm"]))
    assert derive_coverage(c, None) == "declined"


def test_an_open_member_is_never_covered_even_on_a_direct_row():
    """The split is authoritative: coverage can never contradict `gaps`."""
    c = cluster(gaps=["Docker"], outcome=outcome(covered=["Helm"]))
    assert derive_coverage(c, [row("Docker", "direct"), row("Helm", "direct")]) == "partly_covered"


def test_liability_only_cluster_is_partly_covered_not_covered():
    ledger = [row("SAP PP", "direct", narrative=False)]
    assert derive_coverage(cluster(gaps=["SAP PP"]), ledger) == "partly_covered"


def test_memberless_cluster_is_open():
    assert derive_coverage(cluster(gaps=[]), None) == "open"


def test_legacy_cluster_without_ledger_is_open():
    assert derive_coverage(cluster(gaps=["Docker"]), None) == "open"


# ---------------------------------------------------------------------------
# remaining_budget / is_askable — clause 1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "asked,per_gap,expected", [(0, 2, 2), (1, 2, 1), (2, 2, 0), (5, 2, 0), (1, 3, 2)]
)
def test_remaining_budget(asked, per_gap, expected):
    assert remaining_budget(cluster(outcome=outcome(asked=asked)), per_gap) == expected


def test_remaining_budget_defaults_to_the_setting():
    assert remaining_budget(cluster()) == gc.INTERVIEW_MAX_QUESTIONS_PER_GAP


@pytest.mark.parametrize("asked", ["1", True, -3, None, 1.5])
def test_malformed_asked_counts_as_zero(asked):
    c = cluster(outcome={**outcome(), "asked": asked})
    assert remaining_budget(c, 2) == 2


def test_open_cluster_with_budget_is_askable():
    assert is_askable(cluster(gaps=["Docker"], coverage="open"), 2)


def test_spent_budget_is_not_askable():
    c = cluster(gaps=["Docker"], outcome=outcome(asked=2), coverage="partly_covered")
    assert not is_askable(c, 2)


@pytest.mark.parametrize("coverage", ["covered", "declined"])
def test_covered_or_declined_is_not_askable(coverage):
    assert not is_askable(cluster(gaps=["Docker"], coverage=coverage), 2)


def test_partly_covered_with_budget_is_askable():
    c = cluster(gaps=["Docker"], outcome=outcome(asked=1), coverage="partly_covered")
    assert is_askable(c, 2)


def test_legacy_cluster_without_coverage_is_askable():
    assert is_askable(cluster(gaps=["Docker"]), 2)


def test_unknown_stored_coverage_is_re_derived():
    c = cluster(gaps=[], outcome=outcome(covered=["Docker"]), coverage="bogus")
    assert not is_askable(c, 2)


def test_a_cluster_without_open_members_is_not_askable():
    assert not is_askable(cluster(gaps=[], coverage="open"), 2)


@pytest.mark.parametrize("bad", [["covered"], {"x": 1}, 7, None, "bogus"])
def test_a_malformed_stored_coverage_never_raises_and_is_re_derived(bad):
    """A persisted-read path: a hand-edited or future row never takes a GET
    down (an unhashable value used to reach a frozenset membership test)."""
    from applire.schemas.gap_cluster import GapClusterSchema

    c = cluster(gaps=[], outcome=outcome(covered=["Docker"]), coverage=bad)
    assert not is_askable(c, 2)
    assert GapClusterSchema.model_validate(c).coverage is None


def test_non_dict_is_not_askable():
    assert not is_askable(None, 2)  # type: ignore[arg-type]


def test_liability_only_cluster_stays_askable():
    """RULING A-1: the #260 "tell the story" exits open a session on a cluster
    made only of unstoried liabilities; the 409/invalid_input guard must not
    refuse it."""
    ledger = [row("SAP PP", "direct", narrative=False), row("MES", "direct", narrative=False)]
    fresh = initialise_cluster_record(cluster(gaps=["SAP PP", "MES"]), ledger, None)
    assert fresh["gaps"] == ["SAP PP", "MES"]
    assert fresh["coverage"] in ("open", "partly_covered")
    assert is_askable(fresh, 2)


# ---------------------------------------------------------------------------
# apply_turn_outcome — the per-turn write (pure)
# ---------------------------------------------------------------------------


def test_turn_charges_records_session_and_splits():
    ledger = [row("Docker", "direct"), row("Helm", "gap")]
    c = cluster(gaps=["Docker", "Helm"])
    out = apply_turn_outcome(
        c, {"Docker": "covered", "Helm": "open"}, session_id="s1", keyword_ledger=ledger
    )
    assert out["gaps"] == ["Helm"]
    assert out["outcome"] == outcome(asked=1, covered=["Docker"], session_ids=["s1"])
    assert out["coverage"] == "partly_covered"
    for key in ("id", "label", "category", "jd_skills", "jd_context"):
        assert out[key] == c[key]


def test_turn_without_charge_keeps_the_count():
    c = cluster(gaps=["Docker"], outcome=outcome(asked=1, session_ids=["s1"]))
    out = apply_turn_outcome(c, {}, session_id="s2", keyword_ledger=None, charge=False)
    assert out["outcome"]["asked"] == 1
    assert out["outcome"]["session_ids"] == ["s1", "s2"]


def test_session_ids_are_deduplicated_and_blank_ids_ignored():
    c = cluster(gaps=["Docker"], outcome=outcome(asked=1, session_ids=["s1"]))
    out = apply_turn_outcome(c, {}, session_id="s1", keyword_ledger=None)
    assert out["outcome"]["session_ids"] == ["s1"]
    assert out["outcome"]["asked"] == 2
    out2 = apply_turn_outcome(c, {}, session_id="", keyword_ledger=None)
    assert out2["outcome"]["session_ids"] == ["s1"]


def test_turn_never_mutates_its_input():
    c = cluster(gaps=["Docker", "Helm"], outcome=outcome(asked=1, session_ids=["s0"]))
    before = copy.deepcopy(c)
    apply_turn_outcome(c, {"Docker": "covered"}, session_id="s1", keyword_ledger=None)
    assert c == before


def test_unnamed_members_keep_their_previous_split():
    c = cluster(gaps=["Helm"], outcome=outcome(covered=["Docker"], declined=["SAP"]))
    out = apply_turn_outcome(c, {"Helm": "open"}, session_id="s1", keyword_ledger=None)
    assert out["gaps"] == ["Helm"]
    assert out["outcome"]["covered"] == ["Docker"]
    assert out["outcome"]["declined"] == ["SAP"]


def test_facts_match_members_by_normaliser_and_ignore_strangers():
    c = cluster(gaps=["Docker"])
    out = apply_turn_outcome(
        c, {" docker ": "covered", "Terraform": "covered", "Helm": "bogus"},
        session_id="s1", keyword_ledger=None,
    )
    assert out["outcome"]["covered"] == ["Docker"]
    assert out["gaps"] == []
    assert all_members(out) == ["Docker"], "a turn never adds a member"
    assert out["coverage"] == "covered"


def test_a_new_denial_moves_a_covered_member_to_declined():
    c = cluster(gaps=[], outcome=outcome(covered=["Docker"]), coverage="covered")
    out = apply_turn_outcome(c, {"Docker": "declined"}, session_id="s1", keyword_ledger=None)
    assert out["outcome"]["covered"] == [] and out["outcome"]["declined"] == ["Docker"]
    assert out["coverage"] == "declined"


def test_turn_on_a_legacy_cluster_initialises_the_record():
    c = cluster(gaps=["Docker", "Helm"])  # no outcome, no coverage
    out = apply_turn_outcome(
        c, {"Docker": "covered"}, session_id="s1",
        keyword_ledger=[row("Docker", "direct"), row("Helm", "partial")],
    )
    assert out["outcome"]["asked"] == 1
    assert out["coverage"] == "partly_covered"


# ---------------------------------------------------------------------------
# refresh_cluster_from_ledger — clause 4 carry-forward (pure)
# ---------------------------------------------------------------------------


def test_refresh_drops_a_member_no_ledger_row_matches():
    """Else the orphan stays open forever while the same requirement, reworded,
    is clustered again and asked twice (ADR-089 clause 4)."""
    c = cluster(gaps=["Docker", "Kubernetes-Operatoren"])
    out = refresh_cluster_from_ledger(c, [row("Docker", "gap"), row("Helm", "gap")], None)
    assert out is not None
    assert all_members(out) == ["Docker"]


def test_refresh_drops_a_cluster_with_no_surviving_member():
    c = cluster(gaps=["Rust"], outcome=outcome(covered=["Go"]))
    assert refresh_cluster_from_ledger(c, [row("Docker", "gap")], None) is None


def test_refresh_keeps_asked_and_session_ids_and_resplits():
    c = cluster(
        gaps=["Docker"],
        outcome=outcome(asked=2, covered=["Helm"], session_ids=["s1", "s2"]),
    )
    ledger = [row("Docker", "direct"), row("Helm", "partial")]
    out = refresh_cluster_from_ledger(c, ledger, None)
    assert out["outcome"]["asked"] == 2
    assert out["outcome"]["session_ids"] == ["s1", "s2"]
    # Docker became direct → covered; Helm regressed to partial → open again.
    assert out["outcome"]["covered"] == ["Docker"]
    assert out["gaps"] == ["Helm"]
    assert out["coverage"] == "partly_covered"


def test_refresh_moves_a_member_that_became_claimable_to_covered_not_away():
    c = cluster(gaps=["Docker"])
    out = refresh_cluster_from_ledger(c, [row("Docker", "direct")], None)
    assert out["gaps"] == [] and out["outcome"]["covered"] == ["Docker"]
    assert out["coverage"] == "covered", "a worked cluster stays listed with its coverage"


def test_refresh_reads_declines_from_the_denial_list():
    c = cluster(gaps=["Docker", "Helm"])
    out = refresh_cluster_from_ledger(
        c, [row("Docker", "denied"), row("Helm", "gap")], [{"concept": "Docker"}]
    )
    assert out["outcome"]["declined"] == ["Docker"] and out["gaps"] == ["Helm"]


@pytest.mark.parametrize("ledger", [None, []])
def test_refresh_without_a_ledger_drops_nothing(ledger):
    c = cluster(gaps=["Docker", "Helm"])
    out = refresh_cluster_from_ledger(c, ledger, None)
    assert all_members(out) == ["Docker", "Helm"]


def test_refresh_never_mutates_its_input():
    c = cluster(gaps=["Docker", "Rust"], outcome=outcome(asked=1))
    before = copy.deepcopy(c)
    refresh_cluster_from_ledger(c, [row("Docker", "direct")], None)
    assert c == before


# ---------------------------------------------------------------------------
# initialise_cluster_record — first analysis / appended clusters
# ---------------------------------------------------------------------------


def test_initialise_splits_but_never_drops():
    ledger = [row("Docker", "direct"), row("Helm", "gap")]
    out = initialise_cluster_record(cluster(gaps=["Docker", "Helm", "Rust"]), ledger, None)
    assert out["outcome"] == outcome(covered=["Docker"])
    assert out["gaps"] == ["Helm", "Rust"], "an unmatched fresh member is kept (clustering's call)"
    assert out["coverage"] == "partly_covered"


def test_initialise_discards_any_incoming_outcome():
    c = cluster(gaps=["Docker"], outcome=outcome(asked=9))
    assert initialise_cluster_record(c, [row("Docker", "gap")], None)["outcome"]["asked"] == 0


def test_initialise_memberless_cluster_is_kept_open():
    out = initialise_cluster_record(cluster(gaps=[]), None, None)
    assert out["gaps"] == [] and out["coverage"] == "open"


# ---------------------------------------------------------------------------
# record_turn_outcome — the persisted write (flush, never commit)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.uploads  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db, *row_specs):
    """A job + profile and one GapAnalysis per spec (oldest first):
    spec = (clusters, deleted?)."""
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"h-{uuid.uuid4()}",
        raw_text="JD",
        role_title="Engineer",
        required_skills=["Docker", "Helm"],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="EN",
    )
    profile = make_master_profile(id=uuid.uuid4(), profile_json={"skills": []})
    db.add_all([job, profile])
    await db.commit()
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = []
    for i, (clusters, deleted) in enumerate(row_specs):
        ga = GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            match_score=0.5,
            keyword_ledger=[row("Docker", "direct"), row("Helm", "gap")],
            gap_clusters=clusters,
            created_at=base + timedelta(minutes=i),
            deleted_at=(base if deleted else None),
        )
        db.add(ga)
        rows.append(ga)
    await db.commit()
    return job, rows


async def _clusters_of(db, row_id):
    from applire.models.gap import GapAnalysis

    return (
        await db.execute(
            select(GapAnalysis)
            .where(GapAnalysis.id == row_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one().gap_clusters


@pytest.mark.asyncio
async def test_record_writes_the_latest_row_that_carries_the_cluster(db):
    other = cluster("c2", gaps=["Helm"])
    job, (old, new) = await _seed(
        db,
        ([cluster("c1", gaps=["Docker", "Helm"]), other], False),
        ([cluster("c1", gaps=["Docker", "Helm"]), other], False),
    )
    updated = await record_turn_outcome(
        db, job_id=job.id, fallback_gap_analysis_id=old.id, cluster_id="c1",
        member_facts={"Docker": "covered", "Helm": "open"}, session_id="s1",
    )
    await db.commit()
    assert updated["outcome"]["covered"] == ["Docker"]
    persisted = await _clusters_of(db, new.id)
    assert persisted[0]["outcome"] == outcome(asked=1, covered=["Docker"], session_ids=["s1"])
    assert persisted[0]["gaps"] == ["Helm"]
    assert persisted[0]["coverage"] == "partly_covered"
    assert persisted[1] == other, "sibling clusters are untouched"
    assert "outcome" not in (await _clusters_of(db, old.id))[0], "only ONE row is written"


@pytest.mark.asyncio
async def test_record_skips_a_newer_row_without_the_cluster(db):
    """A JD change re-clustered: the newest row has new ids; the session's
    cluster lives on the older row."""
    job, (old, new) = await _seed(
        db,
        ([cluster("c1", gaps=["Docker"])], False),
        ([cluster("c9", gaps=["Docker"])], False),
    )
    updated = await record_turn_outcome(
        db, job_id=job.id, fallback_gap_analysis_id=None, cluster_id="c1",
        member_facts={"Docker": "covered"}, session_id="s1",
    )
    await db.commit()
    assert updated is not None
    assert (await _clusters_of(db, old.id))[0]["outcome"]["asked"] == 1
    assert "outcome" not in (await _clusters_of(db, new.id))[0]


@pytest.mark.asyncio
async def test_record_ignores_deleted_rows_and_falls_back(db):
    job, (fallback, deleted) = await _seed(
        db,
        ([cluster("c1", gaps=["Docker"])], False),
        ([cluster("c1", gaps=["Docker"])], True),
    )
    await record_turn_outcome(
        db, job_id=job.id, fallback_gap_analysis_id=fallback.id, cluster_id="c1",
        member_facts={}, session_id="s1",
    )
    await db.commit()
    assert (await _clusters_of(db, fallback.id))[0]["outcome"]["asked"] == 1
    assert "outcome" not in (await _clusters_of(db, deleted.id))[0]


@pytest.mark.asyncio
async def test_record_returns_none_when_no_row_carries_the_cluster(db):
    job, (only,) = await _seed(db, ([cluster("c1", gaps=["Docker"])], False))
    result = await record_turn_outcome(
        db, job_id=job.id, fallback_gap_analysis_id=only.id, cluster_id="nope",
        member_facts={}, session_id="s1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_record_flushes_but_never_commits(db):
    """The caller's turn owns the transaction (#179): a rollback after the
    call leaves the row exactly as it was."""
    job, (only,) = await _seed(db, ([cluster("c1", gaps=["Docker"])], False))
    job_id, row_id = job.id, only.id  # a rollback expires the ORM objects
    await record_turn_outcome(
        db, job_id=job_id, fallback_gap_analysis_id=None, cluster_id="c1",
        member_facts={"Docker": "covered"}, session_id="s1",
    )
    await db.rollback()
    assert "outcome" not in (await _clusters_of(db, row_id))[0]


@pytest.mark.asyncio
async def test_two_turns_accumulate_across_sessions(db):
    job, (only,) = await _seed(db, ([cluster("c1", gaps=["Docker", "Helm"])], False))
    for sid in ("s1", "s2"):
        await record_turn_outcome(
            db, job_id=job.id, fallback_gap_analysis_id=None, cluster_id="c1",
            member_facts={"Helm": "open"}, session_id=sid,
        )
        await db.commit()
    persisted = (await _clusters_of(db, only.id))[0]
    assert persisted["outcome"]["asked"] == 2
    assert persisted["outcome"]["session_ids"] == ["s1", "s2"]
    assert remaining_budget(persisted, 2) == 0
    assert not is_askable(persisted, 2)
