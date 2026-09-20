# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""Adversarial finding (Nougat UAT-fixes batch, adv pass, 2026-09-20) on #730's
fix in `backend/tests/unit/test_uat_vault_confirmation_refusal.py`.

WP-A's fix closes F-1/F-11 for every confirmation carrying `option_keys`
(ADR-063 amended 2026-09-20): a keyed record's answer either names an option
or resolves to no decision at all. WP-A's own test file documents, without
reproducing through the real door, that a THIRD branch survives on purpose:
`confirmations.match_skill_decision_text`, "the pre-#669 English substring
matcher — back-compat only" (confirmations.py:429-446), reachable whenever a
pending confirmation carries no `option_keys` at all.

`test_the_refusal_really_contains_the_word_of_the_option_it_rejects` already
asserts, on the bare function, that this matcher is "negation-blind by
construction and stays that way" and that ``match_skill_decision_text(REFUSAL)
== "distinct"`` — i.e. the SAME verbatim F-1/F-11 refusal the whole batch was
built to stop still resolves to a WRITE decision on this branch. But no test
in that file (or anywhere else grepped in this tree) drives a KEYLESS pending
confirmation through the real interview door with that refusal to see whether
the vault actually gains the skill. This file does, and it does.

This is not a hypothetical: `_confirmation_state`/`PendingConfirmation`
default `option_keys` to `[]` when nothing sets it, and nothing in
`session.py`, `mcp/server.py` or the standalone profile-review route rejects
an incoming state dict for lacking that field — a pending confirmation that
was parked in an `InterviewSession.state` JSON blob before #669 shipped (or
built by any future caller that forgets to attach `option_keys`, since
nothing enforces their presence at the door) resolves through exactly this
path. The population is legacy/narrow, not the common case — but it is the
SAME defect (F-1: a denial containing the rejected option's own word writes
the skill at `status="confirmed"`), reachable through the SAME real code
(`session._handle_interview_confirmation_answer`), zero provider calls, zero
mocking of the resolution itself.

Severity judgement left to the report: same defect class as #730, on a
population the fix's own author already named and reasoned should be narrow
— but "narrow and reasoned about" is not "closed", and nothing pins that the
population is actually empty in production (no assertion anywhere that a
session row can never carry a keyless pending confirmation post-deploy).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import MasterProfile, authorized_profile_write
from applire.models.session import InterviewSession
from applire.schemas.profile import MasterProfileData, PendingConfirmation, ProfileMetadata
from applire.schemas.session import SessionMessageResponse
from applire.services.profile.reconcile.confirmations import (
    match_skill_decision_text,
    skill_containment_confirmation,
)

REFUSAL = (
    "Neither. I have not led formal Scrum or SAFe delivery. Please do not add "
    "it as a separate skill and do not merge it into my existing Collaboration "
    "skill either. Record this as a denial of agile methodology, not as a skill."
)
INCOMING = "Agile Collaboration"
EXISTING = "Collaboration"


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


def _legacy_keyless_entry() -> dict:
    """The same F-1 confirmation, built by the REAL product builder for its
    question/options text, but persisted in the PRE-#669 shape: no
    `option_keys`, no `options_i18n`, no `question_i18n` — a
    `PendingConfirmation` as it would have been dumped to JSON and sat in an
    `InterviewSession.state` row before #669 shipped keys at all.
    """
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
        # The pre-#669 shape: explicitly empty, not merely omitted, so this
        # fixture cannot accidentally inherit keys from the builder.
        question_i18n={},
        options_i18n=[],
        option_keys=[],
    )
    from applire.services.session import _confirmation_state

    entry = _confirmation_state(parked)
    assert not entry.get("option_keys"), "fixture must reproduce the KEYLESS shape"
    return entry


def _profile_json() -> dict:
    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Max Muster", "email": "kontakt@applire.de"},
            "work_experience": [
                {"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}
            ],
            "skills": [{"name": EXISTING, "category": "soft"}],
        }
    )
    profile.metadata = ProfileMetadata(completeness_score=0.0, denied_concepts=[])
    return profile.model_dump(mode="json")


async def _seed(db) -> MasterProfile:
    with authorized_profile_write():
        record = MasterProfile(profile_json=_profile_json())
    db.add(record)
    await db.commit()
    return record


def _state(profile_id, entry: dict) -> dict:
    import applire.services.session as session_mod

    state = session_mod._build_state(
        mode="guided", job_id=None, gap_analysis_id=None, profile_id=profile_id,
        critical_gaps=["cluster-x"], gap_categories={}, gap_clusters_by_id={},
        current_question=entry["question"], hard_ceiling=9,
    )
    state["pending_interview_confirmation"] = entry
    state["resolving_confirmation"] = True
    state["questions_asked"] = 3
    return state


async def _interview(db, record, state) -> InterviewSession:
    interview = InterviewSession(
        job_analysis_id=None, profile_id=record.id, mode="guided", status="active",
        state=state, hard_ceiling=9, questions_asked=state["questions_asked"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(interview)
    await db.commit()
    return interview


def _skill_names(record: MasterProfile) -> list[str]:
    return [s.name for s in MasterProfileData.model_validate(record.profile_json).skills]


def test_ground_truth_the_keyless_matcher_still_resolves_the_refusal_to_a_write():
    assert "separate" in REFUSAL.lower()
    assert match_skill_decision_text(REFUSAL) == "distinct"


@pytest.mark.asyncio
async def test_a_keyless_legacy_confirmation_still_writes_the_denied_skill(
    db_session, monkeypatch
):
    """The reproduction #730's own test file stopped one step short of: drive
    the verbatim F-1/F-11 refusal through the REAL interview door
    (`session._handle_interview_confirmation_answer`) against a KEYLESS
    pending confirmation, and read the vault afterward.

    Contrast with `test_uat_vault_confirmation_refusal.py::
    test_the_refusal_writes_nothing_and_re_asks_on_the_interview_door`, whose
    KEYED entry (built by the same real builder, same refusal text) writes
    nothing. Here the only fixture difference is `option_keys: []`, and the
    outcome flips back to the pre-fix F-1 harm.
    """
    import applire.services.session as session_mod

    record = await _seed(db_session)
    entry = _legacy_keyless_entry()
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

    await db_session.refresh(record)
    names = _skill_names(record)
    assert INCOMING in names, (
        "expected the keyless legacy path to be equally guarded — instead the "
        f"vault after the refusal is {names!r}, reproducing #730/F-1: a "
        "denial containing the rejected option's own word ('separate') wrote "
        "the skill it named, at status=confirmed, on a KEYLESS pending "
        "confirmation"
    )
    skill = next(s for s in MasterProfileData.model_validate(
        record.profile_json
    ).skills if s.name == INCOMING)
    assert skill.status == "confirmed"

    # And the interview believes the turn is settled and resolved — not
    # re-asked, unlike the keyed door's behaviour for the identical text.
    session_mod._ask_or_complete_at.assert_awaited()
    assert state.get("pending_interview_confirmation") is None
