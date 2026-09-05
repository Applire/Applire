# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""#187 — an interview-turn skill-dedupe confirmation must RESOLVE, not loop.

A reconciler ``RequestConfirmation`` (e.g. bare single-token containment:
incoming 'Docker Compose' vs an existing 'Docker') has no deterministic
resolution path: the next turn re-runs the stateless reconciler, which re-emits
the identical confirmation forever. These session-level tests drive the real
``send_message`` state machine under the mock provider and assert the interview
advances AND the user's choice is actually applied.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from applire.models.profile import MasterProfile
from applire.models.session import InterviewSession
from applire.providers.llm.mock import MockLLMProvider
from applire.schemas.profile import MasterProfileData, Skill
from applire.services.session import _build_state, send_message
from tests.support.profile_factory import make_master_profile

_CONTAINMENT_ANSWER = (
    "I have used Docker Compose extensively to orchestrate multi-container "
    "development environments."
)


async def _seed_docker_session(async_db):
    """A profile with a single 'Docker' skill + an active 2-gap targeted session."""
    profile_data = MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Test User"},
            "skills": [
                {"name": "Docker", "category": "technical", "proficiency": "intermediate"}
            ],
        }
    )
    profile = make_master_profile(profile_json=profile_data.model_dump(mode="json"))
    async_db.add(profile)
    await async_db.commit()
    await async_db.refresh(profile)

    gaps = ["cluster-containers", "cluster-testing"]
    state = _build_state(
        mode="targeted",
        job_id=None,
        gap_analysis_id=None,
        profile_id=profile.id,
        critical_gaps=gaps,
        gap_categories={g: "B" for g in gaps},
        gap_clusters_by_id={
            g: {"id": g, "label": g, "gaps": [], "jd_skills": [], "jd_context": ""}
            for g in gaps
        },
        current_question="Tell me about your containerisation experience.",
        hard_ceiling=12,
    )
    state["current_question"] = "Tell me about your containerisation experience."
    state["questions_asked"] = 1
    state["messages"] = [
        {"role": "assistant", "content": "Tell me about your containerisation experience."}
    ]

    record = InterviewSession(
        job_analysis_id=None,
        gap_analysis_id=None,
        profile_id=profile.id,
        mode="targeted",
        status="active",
        state=state,
        hard_ceiling=12,
        questions_asked=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    async_db.add(record)
    await async_db.commit()
    await async_db.refresh(record)
    return profile, record


async def _reload_skill_names(async_db, profile_id):
    result = await async_db.execute(
        select(MasterProfile).where(MasterProfile.id == profile_id)
    )
    rec = result.scalar_one()
    return [s.get("name") for s in (rec.profile_json.get("skills") or [])]


@pytest.mark.asyncio
async def test_add_separate_skill_resolves_and_appends(async_db):
    """Turn 1 surfaces the containment confirmation; turn 2 replying the exact
    'Add ... as a separate skill' option must ADVANCE the interview and append
    'Docker Compose' as its own distinct skill (the #187 loop)."""
    provider = MockLLMProvider()
    profile, record = await _seed_docker_session(async_db)

    # Turn 1: the answer names Docker Compose -> reconciler containment confirmation.
    r1 = await send_message(record.id, _CONTAINMENT_ANSWER, async_db, provider)
    assert r1.pending_confirmations, "turn 1 should surface the dedupe confirmation"
    conf_question = r1.question

    # Turn 2: pick the EXACT 'separate skill' option.
    r2 = await send_message(
        record.id, "Add 'Docker Compose' as a separate skill", async_db, provider
    )

    # (a) the interview advanced — no identical re-ask.
    assert not r2.pending_confirmations, "confirmation must resolve, not loop"
    assert r2.question != conf_question

    # (b) 'Docker Compose' is now a distinct skill alongside 'Docker'.
    names = await _reload_skill_names(async_db, profile.id)
    assert "Docker" in names
    assert "Docker Compose" in names


@pytest.mark.asyncio
async def test_merge_skill_resolves_without_stray_separate_skill(async_db):
    """Replying 'Merge into the existing skill' must advance AND fold the incoming
    into the existing skill — no second stray Docker-family skill, and the merge
    actually happens (not a silent drop)."""
    provider = MockLLMProvider()
    profile, record = await _seed_docker_session(async_db)

    r1 = await send_message(record.id, _CONTAINMENT_ANSWER, async_db, provider)
    assert r1.pending_confirmations

    r2 = await send_message(
        record.id, "Merge into the existing skill", async_db, provider
    )
    assert not r2.pending_confirmations, "confirmation must resolve, not loop"

    names = await _reload_skill_names(async_db, profile.id)
    # Exactly one Docker-family skill survives — the incoming was folded in, not
    # appended as a distinct entry.
    docker_family = [n for n in names if "docker" in n.lower()]
    assert len(docker_family) == 1, f"expected a single merged skill, got {docker_family}"


# --- #353: TWO confirmations in ONE turn -------------------------------------

_MODULES_ANSWER = (
    "Ich arbeite taeglich mit SAP PP, SAP MM und SAP SD — das sind drei "
    "unterschiedliche Module mit unterschiedlichen Aufgaben."
)


async def _seed_sap_session(async_db):
    """A profile with a single 'SAP' skill + an active 2-gap targeted session."""
    profile_data = MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Test User"},
            "skills": [
                {"name": "SAP", "category": "technical", "proficiency": "advanced"}
            ],
        }
    )
    profile = make_master_profile(profile_json=profile_data.model_dump(mode="json"))
    async_db.add(profile)
    await async_db.commit()
    await async_db.refresh(profile)

    gaps = ["cluster-erp", "cluster-testing"]
    state = _build_state(
        mode="targeted",
        job_id=None,
        gap_analysis_id=None,
        profile_id=profile.id,
        critical_gaps=gaps,
        gap_categories={g: "B" for g in gaps},
        gap_clusters_by_id={
            g: {"id": g, "label": g, "gaps": [], "jd_skills": [], "jd_context": ""}
            for g in gaps
        },
        current_question="Which SAP modules do you work with?",
        hard_ceiling=12,
    )
    state["current_question"] = "Which SAP modules do you work with?"
    state["questions_asked"] = 1
    state["messages"] = [
        {"role": "assistant", "content": "Which SAP modules do you work with?"}
    ]

    record = InterviewSession(
        job_analysis_id=None,
        gap_analysis_id=None,
        profile_id=profile.id,
        mode="targeted",
        status="active",
        state=state,
        hard_ceiling=12,
        questions_asked=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    async_db.add(record)
    await async_db.commit()
    await async_db.refresh(record)
    return profile, record


@pytest.mark.asyncio
async def test_every_confirmation_of_the_same_turn_is_asked_not_dropped(async_db):
    """#353 — a turn owing N confirmations must ask ALL of them.

    Answering the first must surface the SECOND question (not advance past it),
    answering that one the THIRD, and every skill must end up on the profile.
    Before the fix only ``pending_confirmations[0]`` was persisted, so the
    second and third questions were never asked again and their skills were
    lost with no error and no re-ask."""
    provider = MockLLMProvider()
    profile, record = await _seed_sap_session(async_db)

    r1 = await send_message(record.id, _MODULES_ANSWER, async_db, provider)
    assert len(r1.pending_confirmations or []) == 3, (
        "turn 1 must surface ALL THREE containment confirmations"
    )
    first_question = r1.question
    assert "SAP PP" in (first_question or "")

    # Answer the FIRST confirmation only.
    r2 = await send_message(
        record.id, "Add 'SAP PP' as a separate skill", async_db, provider
    )
    assert r2.question != first_question
    assert "SAP MM" in (r2.question or ""), (
        f"expected the SAP MM confirmation, got {r2.question!r}"
    )

    # ... then the SECOND: the third must still be owed, not swallowed with it.
    r3 = await send_message(
        record.id, "Add 'SAP MM' as a separate skill", async_db, provider
    )
    assert "SAP SD" in (r3.question or ""), (
        f"expected the SAP SD confirmation, got {r3.question!r}"
    )

    # Answering the last one advances the interview.
    r4 = await send_message(
        record.id, "Add 'SAP SD' as a separate skill", async_db, provider
    )
    assert not r4.pending_confirmations, "all confirmations are resolved"

    names = await _reload_skill_names(async_db, profile.id)
    for expected in ("SAP", "SAP PP", "SAP MM", "SAP SD"):
        assert expected in names, f"{expected} must survive the turn, got {names}"


# ── #620: why the deterministic confirmations are NOT localised yet ──────────
#
# The collector line asks for `{"de": …, "en": …}` payloads on
# `RequestConfirmation` (8 call sites in reconcile/apply.py, one in
# reconcile/attribution.py, one in reconcile/stance.py). The QUESTION half is
# free prose and could move today. The OPTIONS half cannot, and this is the
# reason, executable rather than asserted: an option string is not a label, it
# is the IDENTITY the answer is matched on, in English, by substring, on a
# vault WRITE path shared by both doors (ADR-058).
#
# `_skill_confirmation_decision` keys on "separate" / "keep"+"existing" /
# "merge" and defaults to "distinct" for anything else. Localise the options
# without replacing that matcher with a stable key and the German rendering of
# "Keep the existing skills" silently resolves to "distinct" — the incoming
# skill is ADDED to the vault when the candidate asked to discard it. Silent,
# wrong, and on the truthfulness-bearing side.
#
# These tests characterise today's matcher so the trap is visible at the exact
# place it bites. They are a PIN, not a gate: they do not stop anyone localising
# the options — the ADR delta in the run folder proposes the stable-key shape.


def test_option_matching_is_english_substring_matching_today():
    from applire.services.session import _skill_confirmation_decision

    assert _skill_confirmation_decision("Add 'SAP MM' as a separate skill") == "distinct"
    assert _skill_confirmation_decision("Keep the existing skills") == "keep"
    assert _skill_confirmation_decision("Merge into 'SAP MM'") == "merge"


def test_a_german_rendering_of_the_same_options_mis_resolves():
    """The blocker, executable. `keep` is the dangerous one: the candidate asked
    to DISCARD the incoming skill and the matcher answers `distinct`, which adds
    it. Localising the options is therefore a vault-write change, not a string
    change — see `D-ledger/adr-delta.md`."""
    from applire.services.session import _skill_confirmation_decision

    assert _skill_confirmation_decision("Bestehende Skills behalten") == "distinct"
    assert _skill_confirmation_decision("In 'SAP MM' zusammenführen") == "distinct"
    assert _skill_confirmation_decision("Als eigenständigen Skill hinzufügen") == "distinct"
