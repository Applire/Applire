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
                        "currency": "EUR", "candidate_values": [{"value": "ca. 6 Mio. €", "entry": "PL @ W"}]}
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


async def _seed(db, clusters, ledger, *, work_id=None):
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
            {**({"id": work_id} if work_id else {}),
             "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
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


def test_a_denial_already_cited_for_a_requirements_row_binds_no_scope_bar():
    """Luna after-run, Stefan rep 1: the 120-span bar was bound to the investment
    denial the same response cited as ``denied_by`` for Investitionsplanung."""
    from applire.services.scope_requirements import (
        build_scope_ledger_entries,
        verified_stated_limit,
    )

    profile = _profile_with_denial()
    assert verified_stated_limit(_DENIAL_ID, profile, taken=[f" {_DENIAL_ID.upper()}"]) is None
    assert verified_stated_limit(_DENIAL_ID, profile, taken=["IFS"]) == {
        "id": _DENIAL_ID, "statement": _DENIAL_STATEMENT}
    block = [{"concept": _SCOPE, "kind": "team_size", "jd_value": 120.0, "jd_value_max": None,
              "comparator": "approx", "jd_quote": "ca. 120 Mitarbeitende", "level": "required",
              "candidate_values": [{"value": 38, "entry": "PL @ W", "semantics": "team"}]}]
    cls = [{"concept": _SCOPE, "status": "partial", "reason": "38 < 120",
            "stated_limit": _DENIAL_ID}]
    row = build_scope_ledger_entries(block, cls, profile, denials_taken=[_DENIAL_ID])[0]
    assert "recorded_limit" not in row["bar"]


def test_the_gap_analysis_seam_passes_the_requirements_denials_as_taken():
    """Seam: ``_run_analysis`` hands the response's REQUIREMENTS ``denied_by`` ids to
    the scope builder (a revert of the call site fails here)."""
    import inspect

    from applire.services import gap

    src = inspect.getsource(gap._run_analysis)
    call = src[src.index("build_scope_ledger_entries("):]
    call = call[: call.index("\n    )")]
    assert "denials_taken=" in call and 'data.get("classifications")' in call
    assert 'c.get("denied_by")' in call


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
    assert "Keep that step in the candidate's OWN recorded wording" in block, (
        "MD-Q5 (2): the writer keeps the step in the person's words (the rail reads them)")
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
    second = f"{LEADERSHIP_KIND_ID}-2"
    assert ids == ["cluster-c", scope["id"], LEADERSHIP_KIND_ID, second, "cluster-b"], (
        "MD-Q5 (1): one question per role")
    assert [by_id[c]["entry"]["team_size"] for c in (LEADERSHIP_KIND_ID, second)] == [38, 14]
    assert cats[second] == "B"
    again = plan_leadership_kind(ids, cats, by_id, _KIND_PROFILE, posting_weighs_leadership=True)
    assert again[0] == ids, "at most once"


def test_the_kind_ask_without_a_scope_cluster_needs_a_leadership_posting_and_goes_last():
    from applire.services.interview.scope_question import LEADERSHIP_KIND_ID, plan_leadership_kind

    base = (["a", "b"], {"a": "C", "b": "B"}, {"a": {}, "b": {}})
    assert plan_leadership_kind(*base, _KIND_PROFILE, posting_weighs_leadership=False)[0] == ["a", "b"]
    assert plan_leadership_kind(*base, _KIND_PROFILE, posting_weighs_leadership=True)[0] == [
        "a", "b", LEADERSHIP_KIND_ID, f"{LEADERSHIP_KIND_ID}-2"]
    no_sizes = {"work_experience": [{"company": "X", "role": "Y"}]}
    assert plan_leadership_kind(*base, no_sizes, posting_weighs_leadership=True)[0] == ["a", "b"]


def test_md_q5_the_kind_question_is_per_role_with_the_fixed_options():
    from applire.services.interview.scope_question import (
        LEADERSHIP_KIND_ID,
        leadership_kind_question,
        plan_leadership_kind,
    )
    from applire.services.profile.leadership_kind import OPTION_LABELS

    _ids, _c, by_id = plan_leadership_kind(["a"], {"a": "B"}, {"a": {}}, _KIND_PROFILE,
                                          posting_weighs_leadership=True)
    q = leadership_kind_question(by_id[LEADERSHIP_KIND_ID], "de")
    assert q == {"question": "Bei Weberit Kunststofftechnik GmbH steht eine Teamgröße von 38. War das "
                             "disziplinarische Führung (Personalverantwortung) oder fachliche Anleitung?",
                 "choices": ["Disziplinarisch (Personalverantwortung)", "Fachliche Anleitung", "Beides"]}
    assert leadership_kind_question(by_id[f"{LEADERSHIP_KIND_ID}-2"], "de")["question"].startswith(
        "Bei Rasselstein Umformtechnik GmbH steht eine Teamgröße von 14.")
    en = leadership_kind_question(by_id[LEADERSHIP_KIND_ID], "en")
    assert en["choices"] == [OPTION_LABELS["en"][o] for o in ("disciplinary", "functional", "both")]
    assert en["choices"] == ["Disciplinary (line management)", "Functional guidance", "Both"]
    assert "120" not in q["question"], "never folded into the scope question (RULING Q2-1)"


def test_md_q5_the_kind_question_quotes_the_persons_bullet():
    from applire.services.interview.scope_question import leadership_kind_question

    said = "Führung von 38 Mitarbeitenden über drei Schichtleiter"
    cluster = {"id": "leadership-kind", "kind": "leadership_kind",
               "entry": {"entry_id": "w1", "company": "Weberit", "team_size": 38, "said": f" {said} "}}
    assert leadership_kind_question(cluster, "de")["question"] == (
        f"Du schreibst: »{said}«. War das disziplinarische Führung (Personalverantwortung) "
        "oder fachliche Anleitung?")
    assert leadership_kind_question(cluster, "en")["question"].startswith(f"You write: “{said}”.")


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


async def _kind_turn(db, answer, *, reconcile, work_id=None):
    """One leadership-kind turn (the seeded role) answered with ``answer``."""
    from applire.models.profile import MasterProfile
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session, send_message

    job = await _seed(db, [_scope_cluster()], [_scope_row()], work_id=work_id)
    provider = MagicMock()
    provider.acomplete = AsyncMock(side_effect=AssertionError("no LLM question call"))
    provider.aparse_json = AsyncMock(side_effect=AssertionError("no LLM question call"))
    created = await create_session(SessionCreateRequest(job_id=job.id, mode="targeted"), db, provider)
    with (
        patch("applire.services.session.reconcile_interview_turn", new=_bridge(False)),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        first = await send_message(created.session_id, "Direkt 38.", db, provider)
    assert first.choices == ["Disziplinarisch (Personalverantwortung)", "Fachliche Anleitung", "Beides"]
    with (
        patch("applire.services.session.reconcile_interview_turn", new=reconcile),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        resp = await send_message(created.session_id, answer, db, provider)
    from applire.models.session import InterviewSession

    session_row = await db.get(InterviewSession, created.session_id)
    prof = await db.get(MasterProfile, session_row.profile_id)
    await db.refresh(prof)
    return resp, prof.profile_json["work_experience"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer", "kind", "option", "reconciled"), [
    ("Beides", "disciplinary", "both", False),
    ("Disziplinarisch (Personalverantwortung)", "disciplinary", "disciplinary", False),
    ("Fachliche Anleitung", "functional", "functional", False),
    ("Disziplinarisch, über drei Schichtleiter.", None, "own_words", True),
])
async def test_md_q5_the_kind_answer_is_written_through_the_q1_writer(db, answer, kind, option, reconciled):
    """An option stores its kind (Beides → disciplinary) without a reconcile call;
    own words store no kind, mark the role asked, AND are reconciled."""
    bridge = _bridge(False)
    spy = AsyncMock(side_effect=bridge)
    resp, entry = await _kind_turn(db, answer, reconcile=spy)
    assert entry.get("leadership_kind") == kind
    assert entry["leadership_kind_record"]["option"] == option
    assert entry["leadership_kind_record"]["answer"] == answer
    assert spy.await_count == (1 if reconciled else 0)
    assert resp.complete is True


def test_the_leadership_kind_contract_shape():
    """The shape WP-Q2 consumes from Q1's ``entries_needing_leadership_kind``
    (COORD Q1→Q2): a list of dicts with exactly these keys, ``team_size`` an int.
    A mismatch after the merge fails here, not silently in the planner."""
    from applire.services.profile.role_facts import entries_needing_leadership_kind

    out = entries_needing_leadership_kind(_KIND_PROFILE)
    assert isinstance(out, list) and out, "the two sized roles need a kind"
    for item in out:
        assert isinstance(item, dict)
        assert {"entry_id", "company", "role", "team_size", "said"} <= set(item)
        assert isinstance(item["said"], str)
        assert isinstance(item["team_size"], int) and item["team_size"] > 0
    assert sorted(i["team_size"] for i in out) == [14, 38]


# ===========================================================================
# MD-Q3 — the adversarial round on the merged branch (adv/findings.md).
# Each test is built from the finding's own probe input.
# ===========================================================================

_SPAN_STMT = "Eine größere Führungsspanne wäre für mich der nächste Schritt."
_STEP_SENTENCE = (
    "Aktuell verantworte ich 38 Mitarbeitende in der Produktion; die in Ihrer Anzeige "
    "genannten 120 Mitarbeitende sind für mich der nächste Schritt."
)


def _h2_profile(statement=_SPAN_STMT, concept="grössere Spanne"):
    return {
        "metadata": {"denied_concepts": [{"concept": concept, "statement": statement,
                                          "status": "active", "source": "interview",
                                          "date": "2026-11-01"}]},
        "work_experience": [{"id": "w", "company": "A GmbH", "role": "Teamleiter",
                             "start_date": "2018-01", "is_current": True, "team_size": 38,
                             "responsibilities": ["Fachliche und disziplinarische Leitung von "
                                                  "38 Mitarbeitenden in der Produktion."]}],
    }


def _h2_ledger(limit=("grössere Spanne", _SPAN_STMT), weight=0.9):
    bar = {"kind": "team_size", "value": 120.0, "comparator": "approx",
           "quote": "ca. 120 Mitarbeitende",
           "candidate_values": [{"value": 38, "entry": "Teamleiter @ A GmbH"}]}
    if limit:
        bar["recorded_limit"] = {"id": limit[0], "statement": limit[1]}
    return [{"concept": "Führungsspanne ~120 MA", "status": "partial", "claimable": True,
             "fit_weight": weight, "sources": ["required"], "bar": bar}]


async def _verdicts(text, profile=None, ledger=None, kind="cover_letter"):
    from applire.services.oracle import audit_document

    report = await audit_document(
        kind, profile or _h2_profile(), text=text,
        keyword_ledger=_h2_ledger() if ledger is None else ledger, document_language="de",
    )
    return [c.verdict.verdict for c in report.claims]


_OWN_STEP = "Eine deutlich größere Führungsspanne von 120 Mitarbeitenden wäre für mich der nächste Schritt."


@pytest.mark.asyncio
async def test_h2_n4_the_step_in_the_persons_own_words_is_grounded_by_the_bound_limit():
    """MD-Q5 (2): the posting's figure is accepted in a clause that restates the
    bound limit (#422 overlap measure, 0.6) framed as a step not yet taken."""
    reviewer_limit = "Eine deutlich größere Führungsspanne wäre für mich der nächste Schritt."
    profile = _h2_profile(reviewer_limit)
    ledger = _h2_ledger(("grössere Spanne", reviewer_limit))
    assert await _verdicts(_OWN_STEP, profile, ledger) == ["grounded"]
    assert await _verdicts(
        "Aktuell verantworte ich 38 Mitarbeitende; " + _OWN_STEP[0].lower() + _OWN_STEP[1:],
        profile, ledger) == ["grounded"], "the person's real figure plus the step"
    # The 2026-11-14 Luna after-run (Stefan rep 3): the clause as the writer wrote it.
    stmt = ("Direkt geführt habe ich bei Weberit 38 Mitarbeitende. Ehrlich gesagt wäre die "
            "dauerhafte Spanne von 120 für mich der nächste Schritt.")
    assert await _verdicts(
        "Die dauerhafte Führungsspanne von 120 Mitarbeitenden wäre für mich der nächste Schritt.",
        _h2_profile(stmt, "dauerhafte Führungsspanne von 120 Mitarbeitenden"),
        _h2_ledger(("dauerhafte Führungsspanne von 120 Mitarbeitenden", stmt)),
    ) == ["grounded"]
    # The limit's recorded NAME (the id, shown to the writer on the ladder line) is
    # the person's words too: statement alone 0.5, with the id 1.0.
    short = "Eine größere Spanne wäre der nächste Schritt."
    named = "dauerhafte Führungsspanne von 120 Mitarbeitenden"
    assert await _verdicts(
        "Eine dauerhafte Führungsspanne von 120 Mitarbeitenden wäre der nächste Schritt.",
        _h2_profile(short, named), _h2_ledger((named, short)),
    ) == ["grounded"]


@pytest.mark.asyncio
async def test_n4_a_frame_word_without_the_persons_words_stays_red():
    """Re-check N4 (q2.py), with the reviewer's live bound limit."""
    reviewer_limit = "Eine deutlich größere Führungsspanne wäre für mich der nächste Schritt."
    profile = _h2_profile(reviewer_limit)
    ledger = _h2_ledger(("grössere Spanne", reviewer_limit))
    for text in (
        "Ich führte 120 Mitarbeitende und hatte nie Probleme.",
        "Ohne mich wäre das Team mit 120 Mitarbeitenden nicht erfolgreich gewesen.",
        "Die 120 Mitarbeitenden, die ich nie aus den Augen verlor, prägten mich.",
        "Ich verantwortete 120 Mitarbeitende, ein Ziel, das ich erreicht habe.",
        "I led 120 employees, which I would never trade.",
        _STEP_SENTENCE,  # p8's paraphrase: frame yes, the person's words no (0.33)
        # A frame word, some of the person's words (0.375 < 0.6), another subject.
        "Eine Spanne von 120 Mitarbeitenden wäre für das Werk ein großer Gewinn.",
        # The person's words (overlap 0.67) but no step frame: a claim of having done it.
        "Eine deutlich größere Führungsspanne von 120 hatte ich.",
        # Bare "nie" is no frame (MD-Q5 (2)) even in the person's words; a limit that
        # itself names the figure goes the #422 denial rail instead.
        "Eine deutlich größere Führungsspanne von 120 hatte ich nie.",
    ):
        assert await _verdicts(text, profile, ledger) == ["unbacked"], text


@pytest.mark.asyncio
async def test_h2_the_posting_figure_stays_red_without_the_step_frame_or_the_bound_limit():
    claimed = "Bei der A GmbH verantwortete ich 120 Mitarbeitende in der Produktion."
    assert await _verdicts(claimed) == ["unbacked"], "'verantwortete 120' stays red"
    assert await _verdicts("Ich führte 120 Mitarbeitende, mehr wäre der nächste Schritt.") == [
        "unbacked"], "an appended step tail buys nothing (claim-side clause split)"
    assert await _verdicts("Ich führte 120 Mitarbeitende nicht nur fachlich.") == [
        "unbacked"], "a bare negation of something else is no step frame"
    # Round 3: each assertion below fails ONE condition only — the same sentence in
    # the limit's own words is green with the live, bound limit (the control).
    own = "Eine größere Führungsspanne von 120 Mitarbeitenden wäre für mich der nächste Schritt."
    assert await _verdicts(own) == ["grounded"], "control"
    assert await _verdicts(own, ledger=_h2_ledger(limit=None)) == ["unbacked"], "no bound limit"
    sap = "Mit SAP habe ich noch nie gearbeitet."
    assert await _verdicts(
        own, _h2_profile(sap, "SAP-Erfahrung"), _h2_ledger(("SAP-Erfahrung", sap))
    ) == ["unbacked"], "a limit about something else backs no scope figure"
    assert await _verdicts(own, _h2_profile("Anderes.", "x")) == [
        "unbacked"], "a limit no longer live in the vault backs nothing"
    assert await _verdicts(
        "Eine größere Führungsspanne von 150 Mitarbeitenden wäre für mich der nächste Schritt."
    ) == ["unbacked"], "not the bar's figure"
    assert await _verdicts(own, kind="cv") == ["unbacked"], "a CV never names the step"


def test_m1_n5_a_limit_binds_only_structurally():
    """MD-Q5 (2): (a) recorded on that bar's scope-question turn, or (b) naming the
    bar's exact figure with a unit of its kind. p6.py's SAP limit and the
    re-check's N5 inputs (q3.py) bind nothing."""
    from applire.services.scope_requirements import (
        build_scope_ledger_entries,
        verified_stated_limit,
    )

    span = {"kind": "team_size", "value": 120.0, "value_max": None}
    budget = {"kind": "budget", "value": 5_000_000.0, "value_max": None}
    texts = {
        "SAP-Erfahrung": "Mit SAP habe ich noch nie gearbeitet.",
        "Mitarbeitergespräche": "Mitarbeitergespräche habe ich noch nie geführt.",
        "SAP-Berechtigungen": "Ich kenne SAP-Berechtigungen für Mitarbeiter nicht.",
        "disziplinarische Führung": "Disziplinarische Führung hatte ich nie, nur fachliche Anleitung von 3 Personen.",
        "Tickets": "Ich habe 120 Tickets nie gesehen.",
        "Budgetplanung": "Ich kenne mich nicht mit Budgetplanung in SAP aus.",
        "Euro-Umstellung": "Mit Euro-Umstellung hatte ich nichts zu tun.",
        "Mio-Software": "Kenne die Mio-Software nicht.",
        "Lieferanten-Audits": "Keine Erfahrung mit Kostenverantwortung für Lieferanten-Audits.",
        "Spanne": "Eine größere Führungsspanne wäre für mich der nächste Schritt.",
        "120 MA": "120 Mitarbeitende hatte ich noch nicht.",
        "Budget": "Ein Budget von 5 Mio. € habe ich nie verantwortet.",
        "Datensätze": "5 Mio. Datensätze habe ich nie migriert.",
        "38 MA": "Mehr als 38 Mitarbeitende hatte ich nie.",
    }
    profile = {"metadata": {"denied_concepts": [
        {"concept": k, "statement": v} for k, v in texts.items()]}}
    for cid in ("SAP-Erfahrung", "Mitarbeitergespräche", "SAP-Berechtigungen",
                "disziplinarische Führung", "Tickets", "Spanne", "38 MA"):
        assert verified_stated_limit(cid, profile, bar=span) is None, cid
    for cid in ("Budgetplanung", "Euro-Umstellung", "Mio-Software", "Lieferanten-Audits",
                "Datensätze"):
        assert verified_stated_limit(cid, profile, bar=budget) is None, cid
    assert verified_stated_limit("120 MA", profile, bar=span)["id"] == "120 MA", "(b) figure + people word"
    assert verified_stated_limit("Budget", profile, bar=budget)["id"] == "Budget", "(b) figure + currency"
    assert verified_stated_limit("Budget", profile, bar=span) is None
    assert verified_stated_limit("Spanne", profile, bar=span, asked_for=[" spanne "])["id"] == "Spanne", (
        "(a) recorded on that bar's scope-question turn")
    block = [{"concept": _SCOPE, "kind": "team_size", "jd_value": 120.0, "jd_value_max": None,
              "comparator": "approx", "jd_quote": "ca. 120 Mitarbeitende", "level": "required",
              "candidate_values": [{"value": 38, "entry": "PL @ W", "semantics": "team"}]}]
    cls = [{"concept": _SCOPE, "status": "partial", "reason": "38 < 120", "stated_limit": "Spanne"}]
    assert "recorded_limit" not in build_scope_ledger_entries(block, cls, profile)[0]["bar"]
    row = build_scope_ledger_entries(block, cls, profile, turn_limits={_SCOPE.casefold(): {"Spanne"}})[0]
    assert row["bar"]["recorded_limit"]["id"] == "Spanne"


def test_n5_seam_the_analysis_binds_with_the_previous_rows_turn_limits():
    import inspect

    from applire.services import gap

    src = inspect.getsource(gap._run_analysis)
    assert "turn_limits=turn_bound_limits(previous.gap_clusters" in src


def test_n5_the_scope_turn_records_its_limits_and_the_analysis_reads_them():
    from applire.services.interview.scope_question import RECORDED_LIMITS_KEY, turn_bound_limits

    scope = {**_scope_cluster(), RECORDED_LIMITS_KEY: ["Spanne"]}
    other = {**_llm_cluster("cluster-sap", ["SAP"]), RECORDED_LIMITS_KEY: ["SAP-Erfahrung"]}
    assert turn_bound_limits([scope, other]) == {_SCOPE.casefold(): {"Spanne"}}, (
        "only a SCOPE cluster's turn binds")


@pytest.mark.asyncio
async def test_n5_end_to_end_a_limit_stated_on_the_scope_turn_is_kept_on_the_cluster(db):
    from applire.models.gap import GapAnalysis
    from applire.schemas.session import SessionCreateRequest
    from applire.services.interview.scope_question import RECORDED_LIMITS_KEY
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult
    from applire.services.session import create_session, send_message
    from sqlalchemy import select

    scope = _scope_cluster()
    job = await _seed(db, [scope], [_scope_row()])
    provider = MagicMock()
    provider.acomplete = AsyncMock(side_effect=AssertionError("no LLM question call"))
    provider.aparse_json = AsyncMock(side_effect=AssertionError("no LLM question call"))
    created = await create_session(
        SessionCreateRequest(job_id=job.id, mode="targeted", target_gap=scope["id"]), db, provider)

    async def _denies(db, *, profile_record, **_kw):
        return InterviewTurnResult(profile_dict=copy.deepcopy(profile_record.profile_json),
                                   changes=[], addressed=False, denial_recorded=True,
                                   denied_concepts=["größere Führungsspanne"])

    with (
        patch("applire.services.session.reconcile_interview_turn", new=AsyncMock(side_effect=_denies)),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        await send_message(created.session_id, "Eine größere Spanne wäre der nächste Schritt.", db, provider)
    row = (await db.execute(select(GapAnalysis).where(GapAnalysis.job_analysis_id == job.id))).scalar_one()
    kept = next(c for c in row.gap_clusters if c.get("id") == scope["id"])
    assert kept[RECORDED_LIMITS_KEY] == ["größere Führungsspanne"]


def test_m1_a_scope_row_never_joins_a_requirement_rows_denial_group():
    """p6.py: the scope row and the SAP row shared the key ``s:<statement>`` and the
    group was flagged ``scope=True`` — the ladder printed a skill denial as a step."""
    from applire.services.cross_document import letter_gap_ladder

    sap = "Mit SAP habe ich noch nie gearbeitet."
    denials = [{"concept": "SAP-Erfahrung", "statement": sap}]
    skill = {**_row("SAP", status="denied"), "denied_by": "SAP-Erfahrung"}
    scope = _scope_row(limit={"id": "SAP-Erfahrung", "statement": sap})
    gaps, named = letter_gap_ladder([scope, skill], denials, "Ihr Profil: SAP, ca. 120 Mitarbeitende.")
    assert [g.scope for g in gaps] == [False], "one gap, a lack, not a step"
    assert gaps[0].concepts == ("SAP",) and _SCOPE not in gaps[0].concepts


def test_m1_the_gap_seam_counts_the_ledger_floor_denials_as_taken():
    import inspect

    from applire.services import gap

    src = inspect.getsource(gap._run_analysis)
    call = src[src.index("build_scope_ledger_entries("):]
    call = call[: call.index("\n    )")]
    assert "*keyword_ledger" in call, "the ledger floor's denied_by binds are taken too"


def _gap(label, *, scope=False, figures=True):
    from applire.services.cross_document import KeyGap

    return KeyGap(label=label, concepts=(label,), statement=f"{label} statement", weight=1.0,
                  mentions=1, first_position=0, in_requirements=True, scope=scope,
                  scope_figures=figures)


def test_m2_a_scope_step_never_crowds_a_recorded_denial_out():
    """M2: two named denials + one scope step made three gaps and named ONE."""
    from applire.services.cross_document import gaps_to_name

    d1, d2, d3, s1, s2 = _gap("D1"), _gap("D2"), _gap("D3"), _gap("S1", scope=True), _gap("S2", scope=True)
    names = lambda gs: [g.label for g in gaps_to_name(gs)]  # noqa: E731
    assert names([s1, d1, d2]) == ["D1", "D2"], "both denials stay named; no room for the step"
    assert names([s1, d1]) == ["D1", "S1"], "denials first, then the step in the room left"
    assert names([s1, d1, d2, d3]) == ["D1", "S1"], "G-1 among denials (3+ → one), step fills room"
    assert names([s1]) == ["S1"]
    assert names([s1, s2]) == ["S1", "S2"]
    assert names([d1, d2, d3]) == ["D1"], "no scope step: G-1 unchanged"


def test_m2_the_writer_block_head_matches_the_named_set():
    from applire.services.cross_document import render_gap_ladder_block

    gaps = [_gap("S1", scope=True), _gap("D1"), _gap("D2"), _gap("D3")]
    block = render_gap_ladder_block(gaps, [gaps[1], gaps[0]], "de")
    assert "ONLY the ones below are named." in block and "From three on" not in block
    assert "Each is named below." in render_gap_ladder_block(gaps[:2], gaps[:2], "de")


def test_l6_a_gap_scope_step_says_no_figure_is_on_file():
    from applire.services.cross_document import letter_gap_ladder, render_gap_ladder_block

    limit = {"id": _DENIAL_ID, "statement": _DENIAL_STATEMENT}
    gaps, named = letter_gap_ladder([_scope_row("gap", values=False, limit=limit)], [], "x")
    block = render_gap_ladder_block(gaps, named, "de")
    assert "a SCOPE step (no figure of the candidate's on file)" in block
    assert "a step marked \"no figure of the candidate's on file\" states none" in block
    gaps2, named2 = letter_gap_ladder([_scope_row(limit=limit)], [], "x")
    assert "a SCOPE step; the candidate's own words" in render_gap_ladder_block(gaps2, named2, "de")


def test_m3_the_question_reads_todays_profile_not_the_analysis_snapshot():
    from applire.services.interview.scope_question import scope_question

    cluster = _scope_cluster()  # carved when the profile said 38 and 14
    today = {"work_experience": [
        {"company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter", "team_size": 45}]}
    q = scope_question(cluster, "de", today)["question"]
    assert "In deinem Profil steht bisher: 45 (Produktionsleiter, Weberit Kunststofftechnik GmbH)." in q
    assert "38" not in q and "14" not in q
    retired = scope_question(_scope_cluster(values=False, attested={
        "quote": "Fachliche Anleitung von zwei Werkstudierenden.", "entry": "FC @ S", "unit": "x"}),
        "de", {"work_experience": []})["question"]
    assert "Dein Profil nennt dazu noch keine Zahl." in retired, "a retired attestation is not quoted"


@pytest.mark.asyncio
async def test_m3_end_to_end_the_first_question_carries_the_current_vault(db):
    """The cluster was carved with 38 and 14; the vault at session start holds only
    the 38 role — the question names 38 and never the retired 14."""
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session

    scope = _scope_cluster()
    assert [v["value"] for v in scope["scope"]["candidate_values"]] == [14, 38]
    job = await _seed(db, [scope], [_scope_row()])
    provider = MagicMock()
    provider.acomplete = AsyncMock(side_effect=AssertionError("no LLM question call"))
    provider.aparse_json = AsyncMock(side_effect=AssertionError("no LLM question call"))
    created = await create_session(SessionCreateRequest(job_id=job.id, mode="targeted"), db, provider)
    assert "In deinem Profil steht bisher: 38 (Produktionsleiter, Weberit Kunststofftechnik GmbH)." in (
        created.question)
    assert "Rasselstein" not in created.question


def test_m4_duplicate_scope_rows_give_one_cluster_and_one_question():
    """p5.py: the same bar quoted twice gave ``scope-team-size`` and
    ``scope-team-size-2``; the carry-forward carve gave two clusters with one id."""
    from applire.services.interview.scope_question import carve_scope_clusters

    def row(quote, concept=_SCOPE):
        r = _scope_row()
        r["concept"] = concept
        r["bar"] = {**r["bar"], "quote": quote}
        return r

    ledger = [row("ca. 120 Mitarbeitende"), row("Führung eines Teams von ca. 120 Mitarbeitenden")]
    first = carve_scope_clusters([], ledger, [])
    assert [c["id"] for c in first] == ["scope-team-size"]
    second = carve_scope_clusters(first, ledger, [])
    assert [c["id"] for c in second] == ["scope-team-size"]
    two_names = [row("ca. 120 Mitarbeitende"), row("ca. 120 MA", concept="Personalverantwortung 120 MA")]
    one = carve_scope_clusters([], two_names, [])
    assert len(one) == 1 and one[0]["gaps"] == [_SCOPE, "Personalverantwortung 120 MA"]
    legacy = [{**first[0]}, {**first[0], "id": "scope-team-size-2"}]
    assert [c["id"] for c in carve_scope_clusters(legacy, ledger, [])] == ["scope-team-size"]


def _budget_q(quote, value=5_000_000.0, comparator="approx", lang="de", kind="budget"):
    from applire.services.interview.scope_question import carve_scope_clusters, scope_question

    r = _scope_row()
    r["bar"] = {**r["bar"], "kind": kind, "value": value, "comparator": comparator, "quote": quote,
                "candidate_values": []}
    clusters = carve_scope_clusters([], [r], [])
    return scope_question(clusters[0], lang)["question"] if clusters else None


def test_m5_the_unit_and_qualifier_come_from_the_bars_own_figure():
    """p5.py: "EUROPA" printed EUR; "Schweizer Franken" printed no unit; EN "5M €";
    "bis zu 120" printed "ca. 120"."""
    eu = _budget_q("Budget 5 Mio. in der EUROPA-Region, davon 2 Mio. CHF")
    assert eu.startswith("Die Stelle nennt eine Budgetverantwortung von 5 Mio. Dein"), eu
    assert "EUR" not in eu and "€" not in eu and "ca." not in eu.split("Dein")[0]
    assert _budget_q("Budget 5 Mio. in der EUROPA-Region, davon 2 Mio. CHF", 2_000_000.0).startswith(
        "Die Stelle nennt eine Budgetverantwortung von 2 Mio. CHF.")
    assert _budget_q("Budget von 5 Millionen Schweizer Franken").startswith(
        "Die Stelle nennt eine Budgetverantwortung von 5 Mio. CHF.")
    assert _budget_q("ca. 5 Mio. € Budget", lang="en").startswith(
        "The posting names a budget responsibility of about €5M.")
    assert _budget_q("Budgetverantwortung von bis zu 5 Mio. €").startswith(
        "Die Stelle nennt eine Budgetverantwortung von bis zu 5 Mio. €.")
    assert _budget_q("Budget 5 Mio. €", value=5.0) is None, "a value its quote does not state is not asked"
    span = lambda quote, comparator="approx", lang="de": _budget_q(  # noqa: E731
        quote, 120.0, comparator, lang, "team_size").split(". ")[0]
    assert span("bis zu 120 Mitarbeitende") == "Die Stelle nennt eine Führungsspanne von bis zu 120 Mitarbeitenden"
    assert span("bis zu 120 Mitarbeitende", lang="en") == "The posting names a team scope of up to 120 people"
    assert span("mindestens 120 Mitarbeitende") == "Die Stelle nennt eine Führungsspanne von mindestens 120 Mitarbeitenden"
    assert span("Führung von 120 Mitarbeitenden") == "Die Stelle nennt eine Führungsspanne von 120 Mitarbeitenden", (
        "a model 'approx' with no 'ca.' in the quote prints no qualifier")


def test_l2_budget_values_are_ordered_by_amount_and_no_unit_is_invented():
    from applire.services.interview.scope_question import scope_question

    cluster = _scope_cluster()
    cluster["scope"] = {**cluster["scope"], "kind": "budget", "value": 5_000_000.0,
                        "currency": "EUR", "qualifier": "approx",
                        "candidate_values": [{"value": "800000", "entry": "Z @ Q"},
                                             {"value": "ca. 6 Mio. EUR", "entry": "Leiter @ Y"}]}
    q = scope_question(cluster, "de")["question"]
    assert "In deinem Profil steht bisher: ca. 6 Mio. EUR (Leiter, Y) und 800 Tsd. (Z, Q)." in q


def test_l3_a_bar_without_a_number_is_never_printed_as_none():
    from applire.services.interview.scope_question import carve_scope_clusters, scope_question

    row = _scope_row()
    row["bar"] = {**row["bar"], "value": None}
    assert carve_scope_clusters([], [row], []) == []
    for lang in ("de", "en"):
        q = scope_question({"scope": {}}, lang)["question"]
        assert "None" not in q and "ca. " not in q and "about " not in q


def test_l5_a_role_already_asked_is_not_asked_again():
    from applire.services.interview.scope_question import (
        LEADERSHIP_KIND_ID,
        kind_entry_key,
        plan_leadership_kind,
    )

    scope = _scope_cluster()
    args = (["a", scope["id"]], {"a": "C", scope["id"]: "B"}, {"a": {}, scope["id"]: scope}, _KIND_PROFILE)
    _ids, _c, by_id = plan_leadership_kind(*args, posting_weighs_leadership=True,
                                          already_asked={"id:w1"})
    assert [e["team_size"] for e in by_id[LEADERSHIP_KIND_ID]["entries"]] == [14]
    ids, _c, _b = plan_leadership_kind(*args, posting_weighs_leadership=True,
                                       already_asked={"id:w1", "id:w2"})
    assert LEADERSHIP_KIND_ID not in ids
    assert kind_entry_key({"entry_id": "", "company": " Weberit ", "role": "PL"}) == "cr:weberit|pl"


@pytest.mark.asyncio
async def test_l5_end_to_end_an_unresolved_kind_answer_is_not_re_asked(db):
    """The answer "beides" leaves the kind unknown; a later full interview of the
    same person (another posting) does not put the ask to that role again."""
    from applire.schemas.session import SessionCreateRequest
    from applire.services.interview.scope_question import KIND_ASKED_STATE_KEY, LEADERSHIP_KIND_ID
    from applire.services.session import create_session, send_message
    from applire.models.session import InterviewSession

    provider = MagicMock()
    provider.acomplete = AsyncMock(side_effect=AssertionError("no LLM question call"))
    provider.aparse_json = AsyncMock(side_effect=AssertionError("no LLM question call"))
    job = await _seed(db, [_scope_cluster()], [_scope_row()])
    created = await create_session(SessionCreateRequest(job_id=job.id, mode="targeted"), db, provider)
    with (
        patch("applire.services.session.reconcile_interview_turn", new=_bridge(False)),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        await send_message(created.session_id, "Direkt 38.", db, provider)
        await send_message(created.session_id, "beides", db, provider)
    first = await db.get(InterviewSession, created.session_id)
    assert first.state[KIND_ASKED_STATE_KEY] == ["cr:weberit kunststofftechnik gmbh|produktionsleiter"]

    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from sqlalchemy import select

    profile_id = first.profile_id
    job2 = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex, raw_text="Werkleiter. ca. 120 Mitarbeitende.",
        role_title="Werkleiter", required_skills=["MES"], nice_to_have_skills=[], keywords=[],
        seniority_level="Senior", company_culture_signals=[], language_requirement="Deutsch",
        jd_language="de",
    )
    db.add(job2)
    await db.flush()
    db.add(GapAnalysis(
        job_analysis_id=job2.id, profile_id=profile_id, match_score=0.5, critical_gaps=[],
        minor_gaps=[], strengths=[], keyword_gaps=[], category_a=[], category_b=[_SCOPE],
        category_c=[], keyword_ledger=[_scope_row()], gap_clusters=[_scope_cluster()],
    ))
    await db.commit()
    second = await create_session(SessionCreateRequest(job_id=job2.id, mode="targeted"), db, provider)
    row = (await db.execute(select(InterviewSession).where(
        InterviewSession.id == second.session_id))).scalar_one()
    assert LEADERSHIP_KIND_ID not in row.state["critical_gaps"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["Disziplinarisch, über drei Schichtleiter.", "Beides"])
async def test_md_q5_seam_only_the_turns_kind_op_writes_the_kind(db, answer):
    """Seam with Q1's committer guard (73445cef): the kind turn's OWN batch with
    ``leadership_kind_op`` stores what the person picked; the same turn's
    reconciler batch writing ``leadership_kind`` without that op is refused
    (``leadership_kind_unasked``) — the model never sets the kind."""
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult
    from applire.services.profile.reconcile.ops import SetField

    receipts: list = []

    async def _model_writes_the_kind(db, *, profile_record, **_kw):
        result = await commit_ops(
            db, [SetField(target="w1", field="leadership_kind", value="functional")],
            CommitProvenance(source="interview", intake="interview_answer", actor="candidate"),
            record=profile_record, grounding=None, snapshot=None,
        )
        receipts.extend(getattr(i, "reason", None) or i.get("reason") for i in result.enrichment_record.not_applied or [])
        return InterviewTurnResult(profile_dict=copy.deepcopy(profile_record.profile_json),
                                   changes=list(result.changes), addressed=False)

    if answer == "Beides":
        # An option answer never runs the reconciler: the turn's own batch (by
        # entry id) is the only write, and it stores the kind.
        resp, entry = await _kind_turn(db, answer, work_id="w1", reconcile=AsyncMock(
            side_effect=AssertionError("an option is not reconciled")))
        assert entry["leadership_kind"] == "disciplinary"
        assert entry["leadership_kind_record"]["option"] == "both"
        return
    resp, entry = await _kind_turn(db, answer, work_id="w1",
                                   reconcile=AsyncMock(side_effect=_model_writes_the_kind))
    assert receipts == ["leadership_kind_unasked"], "the reconciler's write is refused"
    assert entry.get("leadership_kind") is None, "own words store no kind"
    assert entry["leadership_kind_record"]["option"] == "own_words", "the turn's own op still lands"
