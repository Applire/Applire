# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""WP-Q2 (Sorbet quality-1) — the interview asks what the posting weighs, and the
letter carries the gap (ADR-069 cl. 3 / ADR-089 / ADR-070 cl. 2 / ADR-075 amended
2026-11-14; B8-12 second half).

(a) Every askable scope ledger row gets its own ``scope-<kind>`` cluster, carved out
    of the LLM clusters; its ONE question is built from the ledger facts (the
    posting's figure, the profile's values), carries no chips, and is asked once.
(e) A scope classification's ``stated_limit`` binds the candidate's own recorded
    statement of falling short (verified id → ``bar.recorded_limit``); the letter's
    G-1 ladder then names it as a SCOPE step.

Run:
    LLM_PROVIDER=mistral DATABASE_URL=sqlite+aiosqlite:///:memory: PYTHONPATH=backend \\
      python3 -m pytest tests/unit/test_q2_scope_question.py -q
"""

import copy
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from tests.support.profile_factory import make_master_profile, retire_live_profiles  # noqa: E402

_SCOPE = "Führungsspanne ~120 MA"
_DENIAL_ID = "dauerhafte Führung von 120 Mitarbeitenden"
_DENIAL_STATEMENT = (
    "Direkt geführt habe ich 38 Mitarbeitende. Ehrlich gesagt wäre die dauerhafte "
    "Spanne von 120 für mich der nächste Schritt."
)


def _scope_row(status="partial", *, values=True, attested=None, limit=None, weight=1.0):
    bar = {
        "kind": "team_size", "level": "required", "value": 120.0, "value_max": None,
        "comparator": "approx",
        "quote": "Gesamtverantwortung für Produktion (ca. 120 Mitarbeitende, Mehrschichtbetrieb).",
        "candidate_values": [
            {"value": 14, "entry": "Schichtleiter @ Rasselstein Umformtechnik GmbH"},
            {"value": 38, "entry": "Produktionsleiter @ Weberit Kunststofftechnik GmbH"},
        ] if values else [],
        "cited_entry": None, "attested": attested,
    }
    if limit:
        bar["recorded_limit"] = limit
    return {
        "concept": _SCOPE, "surface_forms": [], "sources": ["required"], "fit_weight": weight,
        "status": status, "evidence": "JD bar …", "claimable": status in ("direct", "partial"),
        "narrative_backed": True, "bar": bar,
    }


def _row(concept, status="gap", claimable=False):
    return {"concept": concept, "surface_forms": [concept], "sources": ["required"],
            "fit_weight": 1.0, "status": status, "evidence": "", "claimable": claimable}


def _llm_cluster(cid, members, category="B"):
    return {"id": cid, "label": cid, "category": category, "gaps": list(members),
            "jd_skills": list(members), "jd_context": "",
            "outcome": {"asked": 0, "covered": [], "declined": [], "session_ids": []},
            "coverage": "open"}


# ---------------------------------------------------------------------------
# (a) carving — one function, both seams
# ---------------------------------------------------------------------------


def test_scope_concept_leaves_the_llm_cluster_and_gets_its_own():
    from applire.services.interview.scope_question import carve_scope_clusters, is_scope_cluster

    ledger = [_scope_row(), _row("Schichtleiterentwicklung")]
    clusters = [_llm_cluster("cluster-fuehrung", ["Schichtleiterentwicklung", _SCOPE])]
    out = carve_scope_clusters(clusters, ledger, [])

    assert [c["id"] for c in out] == ["cluster-fuehrung", "scope-team-size"]
    assert out[0]["gaps"] == ["Schichtleiterentwicklung"]
    scope = out[1]
    assert is_scope_cluster(scope)
    assert scope["category"] == "B", "partial → B (ADR-069 cl. 3 status mapping)"
    assert scope["gaps"] == [_SCOPE]
    assert scope["label"] == _SCOPE
    assert scope["scope"]["value"] == 120.0 and scope["scope"]["comparator"] == "approx"
    assert scope["outcome"]["asked"] == 0


def test_a_gap_scope_row_is_category_c_and_a_scope_only_cluster_is_dropped():
    from applire.services.interview.scope_question import carve_scope_clusters

    out = carve_scope_clusters(
        [_llm_cluster("cluster-x", [_SCOPE], "C")], [_scope_row("gap", values=False)], []
    )
    assert [c["id"] for c in out] == ["scope-team-size"]
    assert out[0]["category"] == "C"


def test_carving_is_idempotent_and_keeps_the_per_gap_record():
    from applire.services.interview.scope_question import carve_scope_clusters

    ledger = [_scope_row(), _row("Schichtleiterentwicklung")]
    once = carve_scope_clusters(
        [_llm_cluster("cluster-fuehrung", ["Schichtleiterentwicklung", _SCOPE])], ledger, []
    )
    once[1]["outcome"]["asked"] = 1
    once[1]["outcome"]["session_ids"] = ["s1"]
    twice = carve_scope_clusters(copy.deepcopy(once), ledger, [])
    assert [c["id"] for c in twice] == [c["id"] for c in once]
    assert twice[1]["outcome"]["asked"] == 1, "a carried scope cluster keeps its record"


def test_a_met_or_unweighted_scope_row_gets_no_cluster():
    from applire.services.interview.scope_question import carve_scope_clusters

    clusters = [_llm_cluster("cluster-x", ["A"])]
    assert carve_scope_clusters(clusters, [_scope_row("direct"), _row("A")], []) == clusters
    assert carve_scope_clusters(clusters, [_scope_row(weight=0.0), _row("A")], []) == clusters


@pytest.mark.asyncio
async def test_the_gap_analysis_seam_carves_scope_clusters():
    """Seam: ``_run_analysis``'s clustering step goes through the carve (a revert of
    the call site in services/gap.py fails here)."""
    import inspect

    from applire.services import gap

    src = inspect.getsource(gap._run_analysis)
    assert "carve_scope_clusters(" in src and "record.gap_clusters = carve_scope_clusters" in src


# ---------------------------------------------------------------------------
# (a) the question — facts, no chips
# ---------------------------------------------------------------------------


def _scope_cluster(**kw):
    from applire.services.interview.scope_question import carve_scope_clusters

    return carve_scope_clusters([], [_scope_row(**kw)], [])[0]


def test_de_question_names_the_posting_figure_and_the_profile_values():
    from applire.services.interview.scope_question import scope_question

    q = scope_question(_scope_cluster(), "de")
    assert q["choices"] is None, "a scope chip would be a first-person claim about a span"
    text = q["question"]
    assert text.startswith("Die Stelle nennt eine Führungsspanne von ca. 120 Mitarbeitenden.")
    assert ("38 (Produktionsleiter, Weberit Kunststofftechnik GmbH) und "
            "14 (Schichtleiter, Rasselstein Umformtechnik GmbH)") in text, "largest first"
    assert "dauerhaft oder vertretungsweise" in text
    assert text.endswith("gegenüber den ca. 120 ein?")
    assert text.count("?") == 1, "one question"


def test_de_question_without_a_value_uses_the_attested_sentence_or_says_none():
    from applire.services.interview.scope_question import scope_question

    attested = {"quote": "Fachliche Anleitung von zwei Werkstudierenden.",
                "entry": "Financial Controller @ Schwarzwald Präzision GmbH", "unit": "x"}
    q = scope_question(_scope_cluster(values=False, attested=attested), "de")["question"]
    assert "„Fachliche Anleitung von zwei Werkstudierenden.“ (Financial Controller, Schwarzwald" in q
    q2 = scope_question(_scope_cluster(values=False), "de")["question"]
    assert "Dein Profil nennt dazu noch keine Zahl." in q2
    assert "Wie viele Personen hast du bisher selbst geführt oder verantwortet" in q2


def test_en_and_budget_variants():
    from applire.services.interview.scope_question import scope_question

    en = scope_question(_scope_cluster(), "en")["question"]
    assert en.startswith("The posting names a team scope of about 120 people.")
    assert "Your profile so far says: 38 (Produktionsleiter, Weberit Kunststofftechnik GmbH)" in en

    cluster = _scope_cluster()
    cluster["scope"] = {**cluster["scope"], "kind": "budget", "value": 5_000_000.0,
                        "currency": "€", "candidate_values": [{"value": "ca. 6 Mio. €", "entry": "PL @ W"}]}
    de = scope_question(cluster, "de")["question"]
    assert de.startswith("Die Stelle nennt eine Budgetverantwortung von ca. 5 Mio. €.")
    assert "ca. 6 Mio. € (PL, W)" in de and "das größte Budget" in de


@pytest.mark.asyncio
async def test_question_generator_builds_the_scope_question_without_the_model():
    from applire.services.interview_graph import question_generator_with_profile

    cluster = _scope_cluster()
    state = {"mode": "targeted", "critical_gaps": [cluster["id"]], "current_gap_index": 0,
             "gap_clusters_by_id": {cluster["id"]: cluster}, "messages": []}
    provider = MagicMock()
    provider.aparse_json = AsyncMock(side_effect=AssertionError("no LLM call"))
    provider.acomplete = AsyncMock(side_effect=AssertionError("no LLM call"))
    out = await question_generator_with_profile(state, {}, provider, lang="de")
    assert "ca. 120 Mitarbeitenden" in out["question"] and out["choices"] is None


def test_gap_detector_asks_the_scope_cluster_first_in_its_bucket():
    from applire.services.interview_graph import gap_detector

    scope = _scope_cluster()
    analysis = MagicMock()
    analysis.gap_clusters = [_llm_cluster("cluster-c", ["X"], "C"),
                             _llm_cluster("cluster-b", ["Y"], "B"), scope]
    analysis.keyword_ledger = []
    ids, _cats, _by = gap_detector(analysis, None)
    assert ids == ["cluster-c", scope["id"], "cluster-b"], "C before B kept; scope leads B"


def test_a_scope_cluster_budget_is_one():
    from applire.services.gap_coverage import remaining_budget

    scope = _scope_cluster()
    assert remaining_budget(scope, 2) == 1
    scope["outcome"]["asked"] = 1
    assert remaining_budget(scope, 2) == 0
    assert remaining_budget(_llm_cluster("c", ["X"]), 2) == 2, "other clusters unchanged"


# ---------------------------------------------------------------------------
# (a) the session seam — asked once, the turn advances
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db, clusters, ledger):
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis

    job = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Leiter Operations. Ihr Profil: ca. 120 Mitarbeitende, MES.",
        role_title="Leiter Operations", required_skills=["MES"], nice_to_have_skills=[],
        keywords=[], seniority_level="Senior", company_culture_signals=[],
        language_requirement="Deutsch", jd_language="de",
    )
    await retire_live_profiles(db)
    profile = make_master_profile(profile_json={
        "personal_info": {"name": "Stefan Test", "email": "stefan@example.de"},
        "skills": [], "work_experience": [
            {"company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
             "start_date": "2018-01", "team_size": 38}],
    })
    db.add_all([job, profile])
    await db.flush()
    row = GapAnalysis(
        job_analysis_id=job.id, profile_id=profile.id, match_score=0.5, critical_gaps=[],
        minor_gaps=[], strengths=[], keyword_gaps=[], category_a=[], category_b=[_SCOPE],
        category_c=["MES"], keyword_ledger=ledger, gap_clusters=clusters,
    )
    db.add(row)
    await db.commit()
    return job


def _bridge(addressed=True):
    from applire.schemas.profile import FieldChange
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    async def _run(db, *, profile_record, **_kwargs):
        pj = copy.deepcopy(profile_record.profile_json)
        return InterviewTurnResult(
            profile_dict=pj,
            changes=[FieldChange(section="work_experience", field="responsibilities",
                                 action="added", new_value="Vertretung 90 MA")] if addressed else [],
            addressed=addressed, denial_recorded=False, denied_concepts=[],
        )

    return AsyncMock(side_effect=_run)


@pytest.mark.parametrize("addressed", [True, False], ids=["vault-changed", "no-change"])
@pytest.mark.asyncio
async def test_the_scope_answer_advances_without_a_follow_up(db, addressed):
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session, send_message

    scope = _scope_cluster()
    mes = _llm_cluster("cluster-mes", ["MES"], "C")
    job = await _seed(db, [mes, scope], [_scope_row(), _row("MES")])

    asked: list[dict] = []

    async def _writer(state, profile, provider, **kwargs):
        asked.append({"gap": state["critical_gaps"][state["current_gap_index"]], **kwargs})
        return {"question": "Erzähl mir von MES.", "choices": None}

    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="unused")
    provider.aparse_json = AsyncMock(return_value={"approved": True})
    with patch("applire.services.session.question_generator_with_profile",
               new=AsyncMock(side_effect=_writer)):
        created = await create_session(
            SessionCreateRequest(job_id=job.id, mode="targeted", target_gap=scope["id"]),
            db, provider,
        )
    with (
        patch("applire.services.session.reconcile_interview_turn", new=_bridge(addressed)),
        patch("applire.services.session.question_generator_with_profile",
              new=AsyncMock(side_effect=_writer)),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        resp = await send_message(
            created.session_id,
            "Direkt 38, vertretungsweise rund 90. 120 wäre der nächste Schritt.",
            db, provider,
        )
    follow_ups = [a for a in asked if a["gap"] == scope["id"]
                  and (a.get("follow_up_focus") or a.get("follow_up_hint") or a.get("retry_focus"))]
    assert follow_ups == [], "no follow-up, retry or probe stays on a scope cluster"
    assert resp.complete is True, "the micro-session on the scope gap completes after one answer"
    assert resp.cluster_coverage.budget_remaining == 0


# ---------------------------------------------------------------------------
# (e) the stated limit — bound, verified, named
# ---------------------------------------------------------------------------


def _profile_with_denial():
    return {"metadata": {"denied_concepts": [
        {"concept": _DENIAL_ID, "statement": _DENIAL_STATEMENT, "source": "interview",
         "date": "2026-11-14", "denial_level": "direct"}]}, "work_experience": []}


def test_a_cited_live_denial_binds_and_an_unknown_id_binds_nothing():
    from applire.services.scope_requirements import verified_stated_limit

    profile = _profile_with_denial()
    assert verified_stated_limit(f"  {_DENIAL_ID.upper()} ", profile) == {
        "id": _DENIAL_ID, "statement": _DENIAL_STATEMENT}
    assert verified_stated_limit("keine IFS-Erfahrung", profile) is None
    assert verified_stated_limit(_DENIAL_ID, {"metadata": {}}) is None
    assert verified_stated_limit(None, profile) is None


def test_the_scope_ledger_row_carries_the_bound_limit_and_keeps_its_status():
    from applire.services.scope_requirements import build_scope_ledger_entries

    block = [{"concept": _SCOPE, "kind": "team_size", "jd_value": 120.0, "jd_value_max": None,
              "comparator": "approx", "jd_quote": "ca. 120 Mitarbeitende", "level": "required",
              "candidate_values": [{"value": 38, "entry": "PL @ W", "semantics": "team"}]}]
    cls = [{"concept": _SCOPE, "status": "partial", "reason": "38 < 120",
            "stated_limit": _DENIAL_ID}]
    row = build_scope_ledger_entries(block, cls, _profile_with_denial())[0]
    assert row["status"] == "partial", "a stated shortfall never moves the status"
    assert row["bar"]["recorded_limit"] == {"id": _DENIAL_ID, "statement": _DENIAL_STATEMENT}
    cls[0]["stated_limit"] = "something else"
    row2 = build_scope_ledger_entries(block, cls, _profile_with_denial())[0]
    assert "recorded_limit" not in row2["bar"]


def test_the_letter_ladder_names_a_recorded_scope_step():
    from applire.services.cross_document import (
        letter_constraint_only_limits,
        letter_gap_ladder,
        render_gap_ladder_block,
    )

    limit = {"id": _DENIAL_ID, "statement": _DENIAL_STATEMENT}
    denials = _profile_with_denial()["metadata"]["denied_concepts"]
    jd = "Ihr Profil: Gesamtverantwortung für Produktion (ca. 120 Mitarbeitende, Mehrschichtbetrieb)."
    gaps, named = letter_gap_ladder([_scope_row(limit=limit)], denials, jd)
    assert len(named) == 1 and named[0].scope is True
    assert named[0].label == _DENIAL_ID and named[0].statement == _DENIAL_STATEMENT
    assert named[0].concepts == (_SCOPE,)
    block = render_gap_ladder_block(gaps, named, "de")
    assert "a SCOPE step" in block and "the step ahead" in block
    assert letter_constraint_only_limits(denials, named) == [], "named → not constraint-only"


def test_a_scope_row_without_a_recorded_limit_is_never_named():
    from applire.services.cross_document import letter_gap_ladder

    for row in (_scope_row(), _scope_row("gap", values=False)):
        gaps, named = letter_gap_ladder([row], [], "ca. 120 Mitarbeitende")
        assert gaps == [] and named == [], "L-1/L-2 = B applied to scope"


def test_a_recorded_scope_gap_row_survives_the_recorded_rows_filter():
    from applire.services.cross_document import letter_gap_ladder

    limit = {"id": _DENIAL_ID, "statement": _DENIAL_STATEMENT}
    _gaps, named = letter_gap_ladder([_scope_row("gap", values=False, limit=limit)], [], "x")
    assert [g.scope for g in named] == [True]


def test_the_gap_prompt_asks_for_the_stated_limit_and_keeps_the_status_rule():
    from applire.prompts.gap_analysis import SYSTEM_PROMPT

    assert '"stated_limit"' in SYSTEM_PROMPT
    assert "It does NOT\n    change \"status\"" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Founder ruling Q-2 (COORD Q1→Q2) — the leadership-kind ask in the planner
# ---------------------------------------------------------------------------

_KIND_PROFILE = {"work_experience": [
    {"id": "w1", "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter", "team_size": 38},
    {"id": "w2", "company": "Rasselstein Umformtechnik GmbH", "role": "Schichtleiter", "team_size": 14},
    {"id": "w3", "company": "Kanzlei", "role": "Assistenz"},
]}


def test_the_kind_ask_follows_the_team_size_scope_question():
    from applire.services.interview.scope_question import LEADERSHIP_KIND_ID, plan_leadership_kind

    scope = _scope_cluster()
    ids, cats, by_id = plan_leadership_kind(
        ["cluster-c", scope["id"], "cluster-b"], {"cluster-c": "C", scope["id"]: "B", "cluster-b": "B"},
        {"cluster-c": {}, scope["id"]: scope, "cluster-b": {}}, _KIND_PROFILE,
        posting_weighs_leadership=False,
    )
    assert ids == ["cluster-c", scope["id"], LEADERSHIP_KIND_ID, "cluster-b"]
    assert [e["team_size"] for e in by_id[LEADERSHIP_KIND_ID]["entries"]] == [38, 14]
    again = plan_leadership_kind(ids, cats, by_id, _KIND_PROFILE, posting_weighs_leadership=True)
    assert again[0].count(LEADERSHIP_KIND_ID) == 1, "at most once"


def test_the_kind_ask_without_a_scope_cluster_needs_a_leadership_posting_and_goes_last():
    from applire.services.interview.scope_question import LEADERSHIP_KIND_ID, plan_leadership_kind

    base = (["a", "b"], {"a": "C", "b": "B"}, {"a": {}, "b": {}})
    assert plan_leadership_kind(*base, _KIND_PROFILE, posting_weighs_leadership=False)[0] == ["a", "b"]
    assert plan_leadership_kind(*base, _KIND_PROFILE, posting_weighs_leadership=True)[0] == [
        "a", "b", LEADERSHIP_KIND_ID]
    no_sizes = {"work_experience": [{"company": "X", "role": "Y"}]}
    assert plan_leadership_kind(*base, no_sizes, posting_weighs_leadership=True)[0] == ["a", "b"]


def test_the_kind_question_wording_is_separate_and_has_no_chips():
    from applire.services.interview.scope_question import (
        LEADERSHIP_KIND_ID,
        leadership_kind_question,
        plan_leadership_kind,
    )

    _ids, _c, by_id = plan_leadership_kind(["a"], {"a": "B"}, {"a": {}}, _KIND_PROFILE,
                                          posting_weighs_leadership=True)
    q = leadership_kind_question(by_id[LEADERSHIP_KIND_ID], "de")
    assert q == {"question": "Bei Weberit Kunststofftechnik GmbH steht eine Teamgröße von 38, bei "
                             "Rasselstein Umformtechnik GmbH von 14. War das jeweils disziplinarische "
                             "Führung (Personalverantwortung) oder fachliche Anleitung?",
                 "choices": None}
    one = {**by_id[LEADERSHIP_KIND_ID], "entries": by_id[LEADERSHIP_KIND_ID]["entries"][:1]}
    assert leadership_kind_question(one, "de")["question"] == (
        "Bei Weberit Kunststofftechnik GmbH steht eine Teamgröße von 38. War das disziplinarische "
        "Führung (Personalverantwortung) oder fachliche Anleitung?")
    assert "120" not in q["question"], "never folded into the scope question (RULING Q2-1)"


@pytest.mark.asyncio
async def test_a_full_interview_asks_scope_then_kind_and_each_turn_advances(db):
    """Seam: ``_create_targeted_session`` plans the kind ask after the scope cluster;
    the real generator builds both questions; each answer advances (no follow-up)."""
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session, send_message

    scope = _scope_cluster()
    job = await _seed(db, [scope], [_scope_row()])
    provider = MagicMock()
    provider.acomplete = AsyncMock(side_effect=AssertionError("no LLM question call"))
    provider.aparse_json = AsyncMock(side_effect=AssertionError("no LLM question call"))
    created = await create_session(SessionCreateRequest(job_id=job.id, mode="targeted"), db, provider)
    assert "ca. 120 Mitarbeitenden" in created.question
    with (
        patch("applire.services.session.reconcile_interview_turn", new=_bridge(False)),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        r1 = await send_message(created.session_id, "Direkt 38, vertretungsweise 90.", db, provider)
        assert r1.complete is False
        assert r1.question.startswith("Bei Weberit Kunststofftechnik GmbH steht eine Teamgröße von 38.")
        r2 = await send_message(created.session_id, "Disziplinarisch, über drei Schichtleiter.", db, provider)
    assert r2.complete is True
