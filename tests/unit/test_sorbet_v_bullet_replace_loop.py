# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sorbet invite-gate WP-V — the bullet replace for a REWORDED bullet (rulings
Q9-1 … Q9-7, the GARH loop; FOUNDER-QUESTION V-1 option A).

Layers, each pinned where it can silently break:

1. ``replace_loop.check_proposal`` — the deterministic check on what the
   author KEPT and what it ADDED (q9 hole 1: the author was an unchecked second
   writer); ``verify_evidence`` — a reviewer confirm counts only with quotes.
2. ``replace_loop.run_replace_loop`` — author → check → (hard: author | soft:
   reviewer) → bound → unresolved; withdraw; fail-open.
3. ``restatement`` hands the blocked candidates on; ``still_held`` filters them
   against the committed vault; the bridge returns the proposals.
4. ``scope_question`` — one closed question per proposal, queued once, exact
   chip only; ``session`` writes the replace only on "Ersetzen"/"Replace".
5. ``bullet_replace.apply_replace`` with ``absorbed`` — the turn's own bullet
   goes with the replace; a held bullet gone meanwhile changes nothing.

Synthetic data only (the delivery runs' invented personas).
"""
from __future__ import annotations

import copy
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.prompts.profile_replace import REPLACE_AUTHOR_SYSTEM, REPLACE_REVIEWER_SYSTEM  # noqa: E402
from applire.prompts.restatement import RESTATEMENT_SYSTEM_PROMPT  # noqa: E402
from applire.schemas.profile import MasterProfileData  # noqa: E402
from applire.services.interview.scope_question import (  # noqa: E402
    BULLET_REPLACE,
    bullet_replace_decision,
    bullet_replace_question,
    is_fact_built_cluster,
    queue_bullet_replace_asks,
)
from applire.services.profile.reconcile.apply import apply_ops  # noqa: E402
from applire.services.profile.reconcile.ops import ReplaceBullet  # noqa: E402
from applire.services.profile.replace_loop import (  # noqa: E402
    check_proposal,
    run_replace_loop,
    still_held,
    verify_evidence,
)
from tests.support.profile_factory import make_master_profile, retire_live_profiles  # noqa: E402

HELD = "Fachliche Anleitung von zwei Werkstudierenden."
NEW = "Seit zwei Jahren zwei Werkstudierende fachlich angeleitet, inklusive Review ihrer Reports."
GOOD = "Fachliche Anleitung von zwei Werkstudierenden seit zwei Jahren, inklusive Review ihrer Reports."


def _kinds(findings) -> set[str]:
    return {f.kind for f in findings}


# ── 1. the check ─────────────────────────────────────────────────────────────


def test_a_faithful_merge_has_no_hard_finding():
    assert not [f for f in check_proposal(HELD, NEW, GOOD) if f.hard]


def test_a_clean_merge_passes_without_a_reviewer():
    held, new = "Einführung von KVP-Routinen", "Einführung von KVP-Routinen in drei Werken"
    assert check_proposal(held, new, "Einführung von KVP-Routinen in drei Werken") == []


@pytest.mark.parametrize(("proposal", "kind"), [
    (HELD, "unchanged"),
    ("Keine fachliche Anleitung von zwei Werkstudierenden seit zwei Jahren.", "added_negation"),
    ("Ich leite seit zwei Jahren zwei Werkstudierende fachlich an.", "first_person"),
    ("Fachliche Anleitung von zwei Werkstudierenden; eine Teamleitung wäre der nächste Schritt.", "wish"),
    ("Fachliche Anleitung von zwei Werkstudierenden seit drei Jahren, 40 Reports.", "figure_unbacked"),
])
def test_hard_findings(proposal, kind):
    found = check_proposal(HELD, NEW, proposal)
    assert kind in _kinds(found)
    assert any(f.hard for f in found if f.kind == kind)


def test_polarity_is_the_held_bullets_even_when_new_carries_the_negation():
    """q9 adversarial "added keine": the reviewer confirmed it; the check must not."""
    held, new = "Budgetverantwortung von 2 Mio EUR", "Keine Budgetverantwortung von 2 Mio EUR, nur Anteil an der Planung"
    found = check_proposal(held, new, "Keine Budgetverantwortung für 2 Mio. EUR, sondern lediglich Anteil an der Planung.")
    assert "added_negation" in _kinds(found)


def test_a_dropped_held_negation_is_hard():
    found = check_proposal("Nie Lieferverzug im Werk", "Lieferverzug im Werk um 20 % reduziert",
                           "Lieferverzug im Werk um 20 % reduziert")
    assert "dropped_negation" in _kinds(found)
    assert any(f.hard for f in found if f.kind == "dropped_negation")


def test_soft_findings_name_what_was_lost_or_added():
    found = check_proposal(HELD, NEW, "Betreuung von Werkstudierenden mit Coaching.")
    kinds = _kinds(found)
    assert {"held_order", "new_missing", "unbacked_words"} <= kinds
    assert not any(f.hard for f in found)


def test_evidence_must_quote_the_proposal_and_cover_both_bullets():
    ev = [{"part": "Fachliche Anleitung von zwei Werkstudierenden.", "quote": "Fachliche Anleitung von zwei Werkstudierenden"},
          {"part": "Seit zwei Jahren zwei Werkstudierende fachlich angeleitet,", "quote": "seit zwei Jahren"},
          {"part": "inklusive Review ihrer Reports.", "quote": "inklusive Review ihrer Reports."}]
    assert verify_evidence(HELD, NEW, GOOD, ev) is None
    assert verify_evidence(HELD, NEW, GOOD, ev[:2]) == "a word of HELD or NEW is not covered"
    bad = [*ev[:2], {"part": "inklusive Review ihrer Reports.", "quote": "inklusive Review der Reports"}]
    assert verify_evidence(HELD, NEW, GOOD, bad) == "a quote is not verbatim in the proposal"
    assert verify_evidence(HELD, NEW, GOOD, []) == "no evidence"


# ── 2. the loop ──────────────────────────────────────────────────────────────


class _Script:
    """Answers by system prompt, from per-role queues; records every call."""

    def __init__(self, author=(), reviewer=(), restatement=None, reconcile=None):
        self.author, self.reviewer = list(author), list(reviewer)
        self.restatement, self.reconcile = restatement, reconcile
        self.calls: list[tuple[str, str]] = []

    async def aparse_json(self, prompt, **kw):
        system = kw.get("system") or ""
        if system == REPLACE_AUTHOR_SYSTEM:
            self.calls.append(("author", prompt))
            item = self.author.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if system == REPLACE_REVIEWER_SYSTEM:
            self.calls.append(("reviewer", prompt))
            return self.reviewer.pop(0)
        if system == RESTATEMENT_SYSTEM_PROMPT:
            self.calls.append(("restatement", prompt))
            return copy.deepcopy(self.restatement)
        if "profile reconciler" in system:
            self.calls.append(("reconcile", prompt))
            return copy.deepcopy(self.reconcile)
        return {"verdict": "unconfirmed"}

    async def acomplete(self, prompt, **kw):
        return ""

    def roles(self):
        return [c[0] for c in self.calls]


async def _loop(provider, held=HELD, new=NEW, **kw):
    return await run_replace_loop(provider, entry_id="w-1", field_name="responsibilities", held=held, new=new, **kw)


@pytest.mark.asyncio
async def test_a_proposal_the_check_passes_needs_no_reviewer():
    held, new = "Einführung von KVP-Routinen", "Einführung von KVP-Routinen in drei Werken"
    p = _Script(author=[{"new": "Einführung von KVP-Routinen in drei Werken"}])
    out = await _loop(p, held, new)
    assert (out.path, out.absorbs, out.proposal) == ("check", True, "Einführung von KVP-Routinen in drei Werken")
    assert p.roles() == ["author"]


@pytest.mark.asyncio
async def test_the_author_never_sees_the_answer_only_the_two_bullets():
    p = _Script(author=[{"new": None, "reason": "different"}])
    await _loop(p)
    prompt = p.calls[0][1]
    assert prompt == f"HELD: {HELD}\nNEW: {NEW}\n"


@pytest.mark.asyncio
async def test_a_hard_finding_goes_back_to_the_author_never_to_the_reviewer():
    p = _Script(author=[{"new": "Ich leite zwei Werkstudierende seit zwei Jahren an."}, {"new": None}])
    out = await _loop(p)
    assert out is None, "the author withdrew after the finding: both bullets stay, no question"
    assert p.roles() == ["author", "author"]
    assert "first person" in p.calls[1][1] and "YOUR PREVIOUS BULLET" in p.calls[1][1]


@pytest.mark.asyncio
async def test_a_reviewer_confirm_with_verified_quotes_ends_the_loop():
    ev = [{"part": HELD, "quote": "Fachliche Anleitung von zwei Werkstudierenden"},
          {"part": NEW, "quote": GOOD}]
    p = _Script(author=[{"new": GOOD}], reviewer=[{"decision": "confirm", "evidence": ev}])
    out = await _loop(p)
    assert (out.path, out.absorbs, out.proposal) == ("reviewer", True, GOOD)
    assert p.roles() == ["author", "reviewer"]


@pytest.mark.asyncio
async def test_a_confirm_whose_quotes_do_not_verify_is_a_finding_for_the_author():
    bad = [{"part": HELD, "quote": "not in the proposal"}]
    p = _Script(author=[{"new": GOOD}, {"new": None}], reviewer=[{"decision": "confirm", "evidence": bad}])
    out = await _loop(p)
    assert out is None
    assert p.roles() == ["author", "reviewer", "author"]
    assert "could not verify" in p.calls[2][1]


@pytest.mark.asyncio
async def test_a_revise_hands_the_reviewers_finding_to_the_author():
    p = _Script(author=[{"new": GOOD}, {"new": None}],
                reviewer=[{"decision": "revise", "finding": "Keep 'Anleitung' on the students."}])
    await _loop(p)
    assert "FINDING: Keep 'Anleitung' on the students." in p.calls[2][1]


@pytest.mark.asyncio
async def test_the_bound_ends_in_the_new_bullet_as_written_for_the_person():
    revise = {"decision": "revise", "finding": "x"}
    p = _Script(author=[{"new": GOOD}] * 3, reviewer=[revise] * 3)
    out = await _loop(p, max_retries=2)
    assert (out.path, out.absorbs, out.proposal) == ("unresolved", False, NEW)
    assert p.roles() == ["author", "reviewer"] * 3


@pytest.mark.asyncio
async def test_bound_zero_is_one_draft():
    p = _Script(author=[{"new": GOOD}], reviewer=[{"decision": "revise", "finding": "x"}])
    out = await _loop(p, max_retries=0)
    assert out.path == "unresolved" and p.roles() == ["author", "reviewer"]


@pytest.mark.asyncio
async def test_a_failed_first_author_call_asks_nothing():
    p = _Script(author=[RuntimeError("down")])
    assert await _loop(p) is None


# ── 3. candidates: restatement → committed vault → bridge ────────────────────

_ENTRY = {
    "id": "w-1", "company": "Schwarzwald Präzision GmbH", "role": "Controllerin",
    "start_date": "2019-02", "is_current": True,
    "responsibilities": [HELD, "Monatsabschluss nach HGB."],
}
_PROFILE = {
    "personal_info": {"name": "Katrin Vogt", "email": "katrin@example.org"},
    "work_experience": [_ENTRY],
    "metadata": {},
}


def test_still_held_drops_a_candidate_whose_bullets_are_not_both_there():
    ok = {"entry_id": "w-1", "field": "responsibilities", "held": HELD, "new": "Monatsabschluss nach HGB."}
    gone_new = {**ok, "new": NEW}
    gone_held = {**ok, "held": "weg"}
    other = {**ok, "entry_id": "w-9"}
    assert still_held(_PROFILE, [ok, gone_new, gone_held, other]) == [ok]


@pytest.mark.asyncio
async def test_restatement_hands_on_the_reworded_candidate_and_writes_the_new_bullet():
    from applire.services.profile.reconcile.ops import AddBullets
    from applire.services.profile.reconcile.restatement import hold_back_restatements

    p = _Script(restatement={"items": [{"new": "e1.n1", "held": "e1.h1", "added": "seit zwei Jahren, Review"}]})
    reworded: list = []
    ops, receipts = await hold_back_restatements(
        MasterProfileData.model_validate(_PROFILE),
        [AddBullets(target="w-1", responsibilities=[NEW])], p, replace=True, reworded=reworded,
    )
    assert reworded == [{"entry_id": "w-1", "field": "responsibilities", "held": HELD, "new": NEW}]
    assert [o.responsibilities for o in ops if isinstance(o, AddBullets)] == [[NEW]]
    assert not [o for o in ops if isinstance(o, ReplaceBullet)] and receipts == []


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


def _bridge_provider():
    return _Script(
        reconcile={"ops": [{"op": "add_bullets", "target": "w-1", "responsibilities": [NEW]}],
                   "ambiguities": [], "denials": []},
        restatement={"items": [{"new": "e1.n1", "held": "e1.h1", "added": "seit zwei Jahren, Review"}]},
        author=[{"new": GOOD}],
        reviewer=[{"decision": "confirm", "evidence": [
            {"part": HELD, "quote": "Fachliche Anleitung von zwei Werkstudierenden"},
            {"part": NEW, "quote": GOOD}]}],
    )


async def _bridge(db, provider, **kw):
    from applire.services.profile.reconcile.interview_bridge import reconcile_interview_turn

    await retire_live_profiles(db)
    record = make_master_profile(profile_json=copy.deepcopy(_PROFILE))
    db.add(record)
    await db.commit()
    out = await reconcile_interview_turn(
        db, profile_record=record, gap="Führung", question="Führst du Menschen?",
        answer="Seit zwei Jahren leite ich zwei Werkstudierende fachlich an und reviewe ihre Reports.",
        provider=provider, session_id="s-v", lang="de", **kw,
    )
    await db.commit()
    return out


@pytest.mark.asyncio
async def test_bridge_returns_the_proposal_and_writes_both_bullets(db):
    out = await _bridge(db, _bridge_provider(), propose_replace=True)
    assert out.replace_proposals == [{
        "entry_id": "w-1", "field": "responsibilities", "held": HELD, "new": NEW,
        "proposal": GOOD, "absorbs": True, "path": "reviewer",
    }]
    bullets = out.profile_dict["work_experience"][0]["responsibilities"]
    assert HELD in bullets and NEW in bullets, "nothing replaced before the person answers"


@pytest.mark.asyncio
async def test_bridge_asks_only_about_candidates_still_in_the_committed_vault(db, monkeypatch):
    """A candidate whose new bullet did not reach the committed vault (held by another
    guard, dropped by the applier) is never put to the person."""
    import applire.services.profile.reconcile.interview_bridge as bridge

    real = bridge.hold_back_restatements

    async def _with_a_stray(*a, reworded=None, **kw):
        out = await real(*a, reworded=reworded, **kw)
        if reworded is not None:
            reworded.append({"entry_id": "w-1", "field": "responsibilities", "held": HELD,
                             "new": "Ein Punkt, der nie geschrieben wurde."})
        return out

    monkeypatch.setattr(bridge, "hold_back_restatements", _with_a_stray)
    p = _bridge_provider()
    out = await _bridge(db, p, propose_replace=True)
    assert [x["new"] for x in out.replace_proposals] == [NEW]
    assert p.roles().count("author") == 1, "no author call for the stray candidate"


@pytest.mark.asyncio
async def test_bridge_without_the_flag_runs_no_loop(db):
    p = _bridge_provider()
    out = await _bridge(db, p)
    assert out.replace_proposals == []
    assert "author" not in p.roles()


# ── 4. the question ──────────────────────────────────────────────────────────

_ITEM = {"entry_id": "w-1", "field": "responsibilities", "held": HELD, "new": NEW,
         "proposal": GOOD, "absorbs": True, "path": "reviewer"}


def _cluster(item=None):
    return {"id": "bullet-replace-1", "kind": BULLET_REPLACE, "entry": item or _ITEM}


def test_the_question_shows_both_bullets_with_two_closed_answers():
    de = bullet_replace_question(_cluster(), "de")
    assert de["choices"] == ["Ersetzen", "Beide behalten"]
    assert f"Bisher: »{HELD}«" in de["question"] and f"Neu: »{GOOD}«" in de["question"]
    en = bullet_replace_question(_cluster(), "en")
    assert en["choices"] == ["Replace", "Keep both"]
    assert f"Current: “{HELD}”" in en["question"]
    assert is_fact_built_cluster(_cluster())


@pytest.mark.parametrize(("message", "decision"), [
    ("Ersetzen", "replace"), (" ersetzen. ", "replace"), ("Replace", "replace"),
    ("Beide behalten", "keep"), ("keep both", "keep"),
    ("Ja, ersetzen bitte", None), ("Ersetzen, aber ohne Reports", None),
])
def test_only_the_exact_closed_answer_decides(message, decision):
    assert bullet_replace_decision(_cluster(), message) == decision
    assert bullet_replace_decision({"kind": "size_bullet"}, "Ersetzen") is None


def test_each_proposal_is_queued_right_after_the_current_cluster_once():
    state = {"critical_gaps": ["a", "b", "c"], "gap_clusters_by_id": {}, "gap_categories": {}, "hard_ceiling": 9}
    assert queue_bullet_replace_asks(state, [_ITEM, dict(_ITEM)], 0) == 1
    assert state["critical_gaps"] == ["a", "bullet-replace-1", "b", "c"]
    assert state["hard_ceiling"] == 10
    assert state["gap_clusters_by_id"]["bullet-replace-1"]["entry"] == _ITEM
    assert queue_bullet_replace_asks(state, [_ITEM], 1) == 0, "asked once per session"
    assert queue_bullet_replace_asks(state, [], 1) == 0


# ── 5. the write ─────────────────────────────────────────────────────────────


def _apply(op, profile=None):
    return apply_ops(MasterProfileData.model_validate(profile or _with_new()), [op], source="interview")


def _with_new(new=NEW):
    p = copy.deepcopy(_PROFILE)
    p["work_experience"][0]["responsibilities"] = [HELD, "Monatsabschluss nach HGB.", new]
    return p


def _bullets(result):
    return result.profile.work_experience[0].responsibilities


def test_replace_puts_the_proposal_in_the_held_slot_and_removes_the_absorbed_bullet():
    r = _apply(ReplaceBullet(entry_id="w-1", field="responsibilities", old=HELD, new=GOOD, absorbed=NEW))
    assert _bullets(r) == [GOOD, "Monatsabschluss nach HGB."]
    rec = r.profile.work_experience[0].replaced_bullets
    assert [(x.text, x.replaced) for x in rec] == [(GOOD, HELD)], "the Ersetzt line and its undo"


def test_an_unresolved_replace_keeps_one_copy_of_the_new_bullet():
    r = _apply(ReplaceBullet(entry_id="w-1", field="responsibilities", old=HELD, new=NEW, absorbed=NEW))
    assert _bullets(r) == [NEW, "Monatsabschluss nach HGB."]


def test_a_held_bullet_gone_meanwhile_changes_nothing():
    p = _with_new()
    p["work_experience"][0]["responsibilities"].remove(HELD)
    r = _apply(ReplaceBullet(entry_id="w-1", field="responsibilities", old=HELD, new=GOOD, absorbed=NEW), p)
    assert _bullets(r) == ["Monatsabschluss nach HGB.", NEW]


# ── 4b. the session: queue on the turn, write on the chip ────────────────────


def _state(job_id, profile_id, clusters, current):
    return {
        "mode": "targeted", "job_id": str(job_id), "gap_analysis_id": None,
        "profile_id": str(profile_id),
        "critical_gaps": [c["id"] for c in clusters],
        "gap_categories": {c["id"]: "C" for c in clusters},
        "gap_clusters_by_id": {c["id"]: c for c in clusters},
        "addressed_gaps": [], "current_gap_index": current,
        "current_question": "Frage?", "messages": [{"role": "assistant", "content": "Frage?"}],
        "questions_asked": 1, "hard_ceiling": 12, "questions_per_gap": {},
        "skipped_gaps": [], "full_gaps": [],
    }


async def _session(db, clusters, current, profile_json, answer, bridge_result):
    from applire.models.job import JobAnalysis
    from applire.models.session import InterviewSession
    from applire.services.session import send_message

    await retire_live_profiles(db)
    job = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex, raw_text="Leitung Controlling.", role_title="Leitung Controlling",
        required_skills=["HGB"], nice_to_have_skills=[], keywords=[], seniority_level="Senior",
        company_culture_signals=[], language_requirement="German", jd_language="de",
    )
    profile = make_master_profile(profile_json=copy.deepcopy(profile_json))
    db.add_all([job, profile])
    await db.flush()
    record = InterviewSession(
        job_analysis_id=job.id, gap_analysis_id=None, profile_id=profile.id, mode="targeted",
        status="active", state=_state(job.id, profile.id, clusters, current), hard_ceiling=12,
        questions_asked=1, expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(record)
    await db.commit()
    bridge = AsyncMock(return_value=bridge_result)
    provider = MagicMock()
    provider.aparse_json = AsyncMock(return_value={"question": "Weiter?", "choices": None})
    with (
        patch("applire.services.session.reconcile_interview_turn", new=bridge),
        patch("applire.services.session.question_generator_with_profile",
              new=AsyncMock(return_value={"question": "Weiter?", "choices": None})),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        await send_message(record.id, answer, db, provider)
    await db.refresh(record)
    await db.refresh(profile)
    return record, profile, bridge


_GAPS = [{"id": g, "label": g, "gaps": [g], "jd_skills": [], "jd_context": ""} for g in ("Führung", "HGB")]


@pytest.mark.asyncio
async def test_session_asks_the_proposal_right_after_the_turn_and_asks_for_the_loop(db):
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    result = InterviewTurnResult(profile_dict=_with_new(), changes=[], addressed=False, replace_proposals=[_ITEM])
    record, _, bridge = await _session(db, _GAPS, 0, _with_new(), "Seit zwei Jahren …", result)
    assert bridge.await_args.kwargs["propose_replace"] is True
    gaps = record.state["critical_gaps"]
    assert gaps[1] == "bullet-replace-1" and gaps[2] == "HGB"
    assert record.state["gap_clusters_by_id"]["bullet-replace-1"]["entry"] == _ITEM


@pytest.mark.asyncio
async def test_session_writes_the_replace_on_the_chip_without_a_reconcile_call(db):
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    clusters = [{**_cluster(), "label": "bullet-replace-1", "gaps": [], "jd_skills": [], "jd_context": ""}, _GAPS[1]]
    unused = InterviewTurnResult(profile_dict={}, changes=[], addressed=False)
    _, profile, bridge = await _session(db, clusters, 0, _with_new(), "Ersetzen", unused)
    assert bridge.await_count == 0, "the chip IS the answer: never reconciled"
    entry = profile.profile_json["work_experience"][0]
    assert entry["responsibilities"] == [GOOD, "Monatsabschluss nach HGB."]
    assert [r["replaced"] for r in entry["replaced_bullets"]] == [HELD]


@pytest.mark.asyncio
async def test_session_keep_both_writes_nothing(db):
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    clusters = [{**_cluster(), "label": "bullet-replace-1", "gaps": [], "jd_skills": [], "jd_context": ""}, _GAPS[1]]
    unused = InterviewTurnResult(profile_dict={}, changes=[], addressed=False)
    record, profile, bridge = await _session(db, clusters, 0, _with_new(), "Beide behalten", unused)
    assert bridge.await_count == 0
    assert profile.profile_json["work_experience"][0]["responsibilities"] == [HELD, "Monatsabschluss nach HGB.", NEW]
    assert record.state["current_gap_index"] == 1, "the question is answered: the interview moves on"


@pytest.mark.asyncio
async def test_the_graph_puts_the_closed_question_without_a_provider_call():
    from applire.services import interview_graph as g

    provider = MagicMock()
    provider.aparse_json = AsyncMock(side_effect=AssertionError("never drafted"))
    state = {"mode": "targeted", "critical_gaps": ["bullet-replace-1"], "current_gap_index": 0,
             "gap_clusters_by_id": {"bullet-replace-1": _cluster()}}
    out = await g.question_generator_with_profile(state, _with_new(), provider, lang="de")
    assert out["choices"] == ["Ersetzen", "Beide behalten"]
    assert out["question"].startswith("Das klingt wie ein Punkt")
