# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#730 / F-1 + F-11 — a refusal read as consent, closed at the matcher.

Founder UAT, 2026-09-20, edge over the agent channel (`1ae3cb4e`). The candidate
had just said *"I have not led formal Scrum or SAFe delivery, so please do not
claim agile methodology experience for me"* (`denial_recorded: true`). The next
turn was a skill-dedupe confirmation for `Agile Collaboration` whose two options
were both WRITES, and the candidate answered off-menu:

    "Neither... Please do not add it as a separate skill and do not merge it
     into my existing Collaboration skill either... Record this as a denial of
     agile methodology, not as a skill."

`_skill_confirmation_decision`'s first branch is ``if "separate" in c`` — and the
refusal contains the word "separate", inside *"do not add it as a separate
skill"*. The denial resolved to ``distinct``, the vault gained the skill at
``status="confirmed"``, the Oracle checked the CV against that vault and reported
0 inflated / 0 unbacked, and a blind hiring manager cited *"'Agile Collaboration'
listed"* as evidence the candidate meets the JD's agile requirement.

**The shape this file is built on** is the one only the guard rescues: a refusal
sentence that CONTAINS the word of the option it rejects.
``test_the_refusal_really_contains_the_word_of_the_option_it_rejects`` asserts
that property by running the real predicate over the real string, so the fixture
cannot quietly stop being the case it claims to be.

Three things are pinned here:

1. the resolution (`confirmations.resolve_skill_decision`): the refusal names no
   option, so there is no decision — and a keyed record never falls through to
   the substring matcher;
2. both doors (the in-turn interview one and the standalone profile-review one):
   nothing is written, the park stays open, `questions_asked` does not move, and
   the re-ask carries `pending_confirmations` with `option_keys` so the AGENT
   door sees the options again;
3. the refusal OPTION itself: family 3 now offers "Neither", in both languages,
   on the existing `keep` key — and picking it writes nothing.

The persisted-denial floor is NOT re-implemented here. `commit.py:642`
(`_refloor_persisted_denials`) already runs `demote_ops_for_denials` against
`metadata.denied_concepts` inside `commit_ops`, i.e. on this path and on every
other door; `test_the_confirmation_write_is_refloored_by_a_persisted_denial`
proves the seam. What that floor CANNOT do — and must not, per ADR-059 amended
2026-08-08 (#486) and reconcile rule 9 — is bridge a denial of "agile
methodologies" to a skill named "Agile Collaboration": asserting `denied` on a
term the candidate never named fabricates testimony. Measured in WP-A's lemma
probe (`Documents/Runs/Nougat/founder-uat-fixes/a/`), which is why contributor 1
— this file — is what closes F-1.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import MasterProfile, authorized_profile_write
from applire.models.session import InterviewSession
from applire.schemas.profile import (
    DeniedConcept,
    MasterProfileData,
    PendingConfirmation,
    ProfileMetadata,
)
from applire.schemas.session import SessionMessageResponse
from applire.services.profile.reconcile.confirmations import (
    SKILL_OPTION_KEYS,
    match_skill_decision_text,
    resolve_option_key,
    resolve_skill_decision,
    skill_containment_confirmation,
    skill_overlap_confirmation,
)

#: The candidate's answer, verbatim from the UAT record (F-11).
REFUSAL = (
    "Neither. I have not led formal Scrum or SAFe delivery. Please do not add "
    "it as a separate skill and do not merge it into my existing Collaboration "
    "skill either. Record this as a denial of agile methodology, not as a skill."
)

INCOMING = "Agile Collaboration"
EXISTING = "Collaboration"


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _containment_entry() -> dict[str, Any]:
    """The state-dict shape of the F-1 confirmation, built by the REAL builder
    so the option texts and keys are the product's, not the test's."""
    op = skill_containment_confirmation(
        incoming_skill=INCOMING,
        related=[EXISTING],
        context={
            "incoming_skill": INCOMING,
            "related_skills": [EXISTING],
            "category": "soft",
            "proficiency": "advanced",
            "evidence_refs": [],
        },
    )
    parked = PendingConfirmation(
        question=op.question,
        options=list(op.options),
        context=dict(op.context),
        source="interview",
        question_i18n=dict(op.question_i18n or {}),
        options_i18n=[dict(p) for p in (op.options_i18n or [])],
        option_keys=list(op.option_keys or []),
    )
    from applire.services.session import _confirmation_state

    return _confirmation_state(parked)


def _profile_json(denials: list[str] | None = None) -> dict:
    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Max Muster", "email": "kontakt@applire.de"},
            "work_experience": [
                {"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}
            ],
            "skills": [{"name": EXISTING, "category": "soft"}],
        }
    )
    profile.metadata = ProfileMetadata(
        completeness_score=0.0,
        denied_concepts=[
            DeniedConcept(
                concept=c,
                statement="…",
                source="interview",
                date=datetime.now(timezone.utc).isoformat(),
            )
            for c in (denials or [])
        ],
    )
    return profile.model_dump(mode="json")


async def _seed(db, denials: list[str] | None = None) -> MasterProfile:
    with authorized_profile_write():
        record = MasterProfile(profile_json=_profile_json(denials))
    db.add(record)
    await db.commit()
    return record


def _state(profile_id, entry: dict) -> dict:
    import applire.services.session as session_mod

    state = session_mod._build_state(
        mode="guided",
        job_id=None,
        gap_analysis_id=None,
        profile_id=profile_id,
        critical_gaps=["cluster-x"],
        gap_categories={},
        gap_clusters_by_id={},
        current_question=entry["question"],
        hard_ceiling=9,
    )
    state["pending_interview_confirmation"] = entry
    state["resolving_confirmation"] = True
    state["questions_asked"] = 3
    return state


async def _interview(db, record, state) -> InterviewSession:
    interview = InterviewSession(
        job_analysis_id=None,
        profile_id=record.id,
        mode="guided",
        status="active",
        state=state,
        hard_ceiling=9,
        questions_asked=state["questions_asked"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(interview)
    await db.commit()
    return interview


def _skill_names(record: MasterProfile) -> list[str]:
    return [s.name for s in MasterProfileData.model_validate(record.profile_json).skills]


# ── 1. Ground truth: the shape the guard exists for ─────────────────────────


def test_the_refusal_really_contains_the_word_of_the_option_it_rejects():
    """The fixture's own property, asserted with the real predicate — not by the
    test author's reading of it. This is why the old matcher wrote: branch 1 is
    ``if "separate" in c``."""
    assert "separate" in REFUSAL.lower()
    # And the SAME string, fed to the pre-#669 matcher, still reads as `distinct`
    # — the matcher is negation-blind by construction and stays that way. What
    # changed is that it is no longer reachable for a keyed record, and no longer
    # the default for an unmatched one.
    assert match_skill_decision_text(REFUSAL) == "distinct"


def test_the_refusal_names_no_option_so_there_is_no_decision():
    entry = _containment_entry()
    assert resolve_option_key(entry, REFUSAL) is None
    assert resolve_skill_decision(entry, REFUSAL) is None


def test_a_keyed_record_never_falls_through_to_the_substring_matcher():
    """The #730 half that is not about this one sentence: for a record that
    carries `option_keys`, an answer naming no option is not an answer."""
    entry = _containment_entry()
    assert entry["option_keys"]
    for answer in (
        REFUSAL,
        "please just merge it",           # would have matched `merge`
        "keep the existing ones as they are",  # would have matched `keep`
        "add it as a separate entry",     # would have matched `distinct`
        "no idea, you decide",
    ):
        assert resolve_skill_decision(entry, answer) is None, answer


def test_a_keyless_record_still_resolves_its_english_options_and_no_longer_defaults():
    """Back-compat: a confirmation parked before #669 carries no keys, so the
    substring matcher is still the only resolver — but an unmatched answer now
    resolves to ``None`` instead of minting the skill."""
    legacy = {
        "question": "'Agile Collaboration' shares a word with …",
        "options": [f"Add '{INCOMING}' as a separate skill", "Merge into the existing skill"],
        "context": {"incoming_skill": INCOMING},
    }
    assert resolve_skill_decision(legacy, f"Add '{INCOMING}' as a separate skill") == "distinct"
    assert resolve_skill_decision(legacy, "Merge into the existing skill") == "merge"
    assert resolve_skill_decision(legacy, "Keep the existing skills") == "keep"
    assert resolve_skill_decision(legacy, "no idea, you decide") is None


# ── 2. The refusal OPTION (F-11) ────────────────────────────────────────────


def test_family_3_offers_a_refusal_option_in_both_languages_on_the_keep_key():
    op = skill_containment_confirmation(
        incoming_skill=INCOMING, related=[EXISTING], context={"incoming_skill": INCOMING}
    )
    assert op.option_keys == ["distinct", "merge", "keep"]
    assert len(op.options) == len(op.option_keys) == len(op.options_i18n or [])
    refusal_option = (op.options_i18n or [])[2]
    assert "Neither" in refusal_option["en"]
    assert "Weder noch" in refusal_option["de"]
    # Both renderings resolve to the SAME key — #669's whole point.
    parked = {"options": list(op.options), "option_keys": list(op.option_keys),
              "options_i18n": [dict(p) for p in (op.options_i18n or [])]}
    assert resolve_skill_decision(parked, refusal_option["en"]) == "keep"
    assert resolve_skill_decision(parked, refusal_option["de"]) == "keep"


def test_every_skill_family_can_express_neither():
    """Family 2 always could (`keep` = "Keep the existing skills"); family 3
    could not, which is F-11. Both are pinned so an eleventh builder cannot ship
    a write-only option set."""
    fam2 = skill_overlap_confirmation(
        incoming_skill=INCOMING, overlapping=[EXISTING, "Teamwork"], context={}
    )
    fam3 = skill_containment_confirmation(
        incoming_skill=INCOMING, related=[EXISTING], context={}
    )
    for op in (fam2, fam3):
        assert "keep" in (op.option_keys or []), op.question
        assert set(op.option_keys or []) <= SKILL_OPTION_KEYS


# ── 3. Door 1: the in-turn interview confirmation ───────────────────────────


@pytest.mark.asyncio
async def test_the_refusal_writes_nothing_and_re_asks_on_the_interview_door(
    db_session, monkeypatch
):
    import applire.services.session as session_mod

    record = await _seed(db_session)
    entry = _containment_entry()
    state = _state(record.id, entry)
    interview = await _interview(db_session, record, state)

    monkeypatch.setattr(
        session_mod,
        "_ask_or_complete_at",
        AsyncMock(return_value=SessionMessageResponse(complete=True, gaps_remaining=0)),
    )

    resp = await session_mod._handle_interview_confirmation_answer(
        interview, state, db_session, MagicMock(), 0, entry, REFUSAL, "en"
    )

    # The vault is untouched: no `Agile Collaboration`, at any status.
    await db_session.refresh(record)
    assert _skill_names(record) == [EXISTING]

    # The interview did NOT advance past the ask.
    session_mod._ask_or_complete_at.assert_not_awaited()
    assert resp.complete is False
    assert entry["question"] in (resp.question or "")
    assert resp.choices == entry["options"]
    # The ask is not spent.
    assert state.get("pending_interview_confirmation") == entry
    assert state.get("resolving_confirmation") is True
    assert state["questions_asked"] == 3


@pytest.mark.asyncio
async def test_the_re_ask_carries_the_option_keys_over_the_agent_door(
    db_session, monkeypatch
):
    """The MCP `send_message` tool returns this response's `model_dump(mode="json")`
    verbatim (`mcp/server.py`), so an agent must see the options AND their stable
    keys again on a re-ask — otherwise it is back to relaying rendered strings,
    which is the shape #730's answer came out of."""
    import applire.services.session as session_mod

    record = await _seed(db_session)
    entry = _containment_entry()
    state = _state(record.id, entry)
    interview = await _interview(db_session, record, state)
    monkeypatch.setattr(
        session_mod,
        "_ask_or_complete_at",
        AsyncMock(return_value=SessionMessageResponse(complete=True, gaps_remaining=0)),
    )

    resp = await session_mod._handle_interview_confirmation_answer(
        interview, state, db_session, MagicMock(), 0, entry, REFUSAL, "en"
    )
    envelope = resp.model_dump(mode="json")
    pending = envelope["pending_confirmations"]
    assert len(pending) == 1
    assert pending[0]["option_keys"] == ["distinct", "merge", "keep"]
    assert pending[0]["options"] == entry["options"]
    assert pending[0]["context"]["incoming_skill"] == INCOMING


@pytest.mark.asyncio
async def test_picking_the_refusal_option_writes_nothing_and_advances(
    db_session, monkeypatch
):
    import applire.services.session as session_mod

    record = await _seed(db_session)
    entry = _containment_entry()
    refusal_text = entry["options"][2]
    state = _state(record.id, entry)
    interview = await _interview(db_session, record, state)
    monkeypatch.setattr(
        session_mod,
        "_ask_or_complete_at",
        AsyncMock(return_value=SessionMessageResponse(complete=True, gaps_remaining=0)),
    )

    await session_mod._handle_interview_confirmation_answer(
        interview, state, db_session, MagicMock(), 0, entry, refusal_text, "en"
    )

    await db_session.refresh(record)
    assert _skill_names(record) == [EXISTING]
    # Answered, so the ask IS spent and the interview advanced.
    session_mod._ask_or_complete_at.assert_awaited_once()
    assert "pending_interview_confirmation" not in state


@pytest.mark.asyncio
async def test_picking_add_separately_still_writes_the_skill(db_session, monkeypatch):
    """The guard may not cost the candidate the skill they DID ask for — this is
    the control arm for every assertion above."""
    import applire.services.session as session_mod

    record = await _seed(db_session)
    entry = _containment_entry()
    state = _state(record.id, entry)
    interview = await _interview(db_session, record, state)
    monkeypatch.setattr(
        session_mod,
        "_ask_or_complete_at",
        AsyncMock(return_value=SessionMessageResponse(complete=True, gaps_remaining=0)),
    )

    await session_mod._handle_interview_confirmation_answer(
        interview, state, db_session, MagicMock(), 0, entry, entry["options"][0], "en"
    )

    await db_session.refresh(record)
    assert INCOMING in _skill_names(record)


# ── 4. Door 2: the standalone profile-review route ──────────────────────────


@pytest.mark.asyncio
async def test_the_refusal_re_asks_on_the_standalone_review_route(db_session, monkeypatch):
    """`_handle_confirmation_answer` is the OTHER resolution door (#686's
    "Decide now" CTA). It has no skill WRITE arm — that stays #674's open line —
    but an unmatched answer must not spend the park here either, or the
    candidate's "neither" is swallowed by the bookkeeping resolve."""
    import applire.services.session as session_mod

    record = await _seed(db_session)
    entry = _containment_entry()
    entry["confirmation_id"] = "conf-1"
    state = _state(record.id, entry)
    state["pending_confirmations_by_id"] = {"cluster-x": entry}
    interview = await _interview(db_session, record, state)

    monkeypatch.setattr(
        session_mod,
        "_ask_or_complete_at",
        AsyncMock(return_value=SessionMessageResponse(complete=True, gaps_remaining=0)),
    )
    resolved = AsyncMock()
    monkeypatch.setattr(session_mod, "_resolve_confirmation_safely", resolved)

    resp = await session_mod._handle_confirmation_answer(
        interview, state, db_session, MagicMock(), 0, entry, REFUSAL
    )

    assert entry["question"] in (resp.question or "")
    assert resp.choices == entry["options"]
    resolved.assert_not_awaited()          # the park is NOT spent
    session_mod._ask_or_complete_at.assert_not_awaited()
    assert state["questions_asked"] == 3
    await db_session.refresh(record)
    assert _skill_names(record) == [EXISTING]


@pytest.mark.asyncio
async def test_a_matched_answer_still_resolves_on_the_standalone_review_route(
    db_session, monkeypatch
):
    """The route's existing behaviour is unchanged for an answer that names an
    option: the park is spent and the interview advances. (It still writes no
    skill — #674's open line, deliberately untouched here.)"""
    import applire.services.session as session_mod

    record = await _seed(db_session)
    entry = _containment_entry()
    entry["confirmation_id"] = "conf-1"
    state = _state(record.id, entry)
    interview = await _interview(db_session, record, state)

    monkeypatch.setattr(
        session_mod,
        "_ask_or_complete_at",
        AsyncMock(return_value=SessionMessageResponse(complete=True, gaps_remaining=0)),
    )
    resolved = AsyncMock()
    monkeypatch.setattr(session_mod, "_resolve_confirmation_safely", resolved)

    await session_mod._handle_confirmation_answer(
        interview, state, db_session, MagicMock(), 0, entry, entry["options"][2]
    )

    resolved.assert_awaited_once()
    session_mod._ask_or_complete_at.assert_awaited_once()


# ── 5. The seam: the persisted-denial floor DOES run on this path ───────────


@pytest.mark.asyncio
async def test_the_confirmation_write_is_refloored_by_a_persisted_denial(
    db_session, monkeypatch
):
    """`commit.py::_refloor_persisted_denials` (ADR-059 amended 2026-08-08 step 2,
    ADR-063 clause 8(d)) runs inside `commit_ops`, so it runs on the confirmation
    path too: a skill a persisted denial NAMES cannot leave this door claimable,
    even when the candidate picks "add it separately".

    This is #730's contributor 2, proven where it already lives rather than
    re-implemented at a second seam (ADR-066). What it deliberately does NOT do
    is bridge a denial of a DIFFERENT term to this skill — see the module
    docstring and `test_skill_demotion.py`."""
    import applire.services.session as session_mod

    record = await _seed(db_session, denials=[INCOMING])
    entry = _containment_entry()
    state = _state(record.id, entry)
    interview = await _interview(db_session, record, state)
    monkeypatch.setattr(
        session_mod,
        "_ask_or_complete_at",
        AsyncMock(return_value=SessionMessageResponse(complete=True, gaps_remaining=0)),
    )

    await session_mod._handle_interview_confirmation_answer(
        interview, state, db_session, MagicMock(), 0, entry, entry["options"][0], "en"
    )

    await db_session.refresh(record)
    stored = MasterProfileData.model_validate(record.profile_json)
    by_name = {s.name: s for s in stored.skills}
    assert INCOMING in by_name, "the write happened — the floor is a demotion, not a refusal"
    assert by_name[INCOMING].status == "denied"


# ── 6. The standalone route's REAL entry shape ──────────────────────────────


def test_the_cluster_shape_the_review_route_actually_answers_resolves_both_ways():
    """`_handle_confirmation_answer` does not get the session-state dict — it gets
    a cluster dict from `interview_graph.build_confirmation_clusters`, whose
    `options` are already RENDERED in the reader's language and which also carries
    `choices`, `option_keys` and both i18n payloads.

    Built on that real shape, in German, because the guard added to that route
    would otherwise re-ask forever for a perfectly good answer: `resolve_option_key`
    reads `options`, and a shape that carried only `choices` would resolve nothing.
    """
    from applire.services.interview_graph import build_confirmation_clusters

    op = skill_containment_confirmation(
        incoming_skill=INCOMING,
        related=[EXISTING],
        context={"incoming_skill": INCOMING, "related_skills": [EXISTING]},
    )
    parked = PendingConfirmation(
        question=op.question,
        options=list(op.options),
        context=dict(op.context),
        source="interview",
        question_i18n=dict(op.question_i18n or {}),
        options_i18n=[dict(p) for p in (op.options_i18n or [])],
        option_keys=list(op.option_keys or []),
    )
    ids, _cats, by_id = build_confirmation_clusters(
        [parked.model_dump(mode="json")], "de"
    )
    entry = by_id[ids[0]]
    assert entry["options"] == entry["choices"]
    assert entry["option_keys"] == ["distinct", "merge", "keep"]
    assert "teilt ein Wort" in entry["question"], entry["question"]

    # Every German rendering resolves — the route can still be answered.
    assert resolve_skill_decision(entry, entry["options"][0]) == "distinct"
    assert resolve_skill_decision(entry, entry["options"][1]) == "merge"
    assert resolve_skill_decision(entry, entry["options"][2]) == "keep"
    # And the English rendering of the same record resolves too (answered after a
    # language switch between ask and answer).
    assert resolve_skill_decision(entry, f"Add '{INCOMING}' as a separate skill") == "distinct"
    # Only a free-text answer names nothing.
    assert resolve_skill_decision(entry, REFUSAL) is None
