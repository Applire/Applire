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
fix in `backend/tests/unit/test_uat_vault_confirmation_refusal.py` — CLOSED by
the same-branch fix below (M-2, `wt-fix-adv`).

WP-A's fix closed F-1/F-11 for every confirmation carrying `option_keys`
(ADR-063 amended 2026-09-20): a keyed record's answer either names an option
or resolves to no decision at all. WP-A's own test file documented, without
reproducing through the real door, that a THIRD branch survived on purpose:
`confirmations.match_skill_decision_text`, "the pre-#669 English substring
matcher — back-compat only", reachable whenever a pending confirmation carries
no `option_keys` at all — fed the RAW free-text answer, so a refusal that
merely quoted the option it rejected ("do not add it as a **separate**
skill") still resolved to `"distinct"` and the vault gained the denied skill.

This file drove that KEYLESS shape through the real interview door
(`session._handle_interview_confirmation_answer`) and proved the vault
actually gained the skill — the same F-1 harm, on a population WP-A's own
author had reasoned (correctly, about the CURRENT population) would be
narrow, but had not closed.

**The fix** (adversarial pass, same-branch): ADR-063's identity rule now
applies to a keyless record too. `confirmations.resolve_skill_decision`
matches the answer EXACTLY (case- and whitespace-folded) against every
rendering the record itself carries (`options` / `options_i18n`), and only
the MATCHED option's own text — never the raw free-text answer — is handed to
`match_skill_decision_text`. An answer equal to no option resolves to `None`,
same as the keyed path, and the door re-asks instead of writing.
`session._skill_confirmation_decision` now delegates outright to
`resolve_skill_decision` whenever the real door hands it the record
(ADR-066 — one implementation). This file is now the GUARD: it asserts the
vault is untouched and the interview re-asks, and it is mutation-killed by
restoring the old raw-answer fallback on a scratchpad copy.
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


def test_ground_truth_the_matcher_itself_still_reads_the_refusal_as_a_write():
    """The fixture's own property, unchanged: `match_skill_decision_text` is
    still "negation-blind by construction" on this string. What changed is
    that the real door no longer feeds it the raw free-text answer — only a
    record's own matched option text ever reaches it (see the guard below)."""
    assert "separate" in REFUSAL.lower()
    assert match_skill_decision_text(REFUSAL) == "distinct"


@pytest.mark.asyncio
async def test_a_keyless_legacy_confirmation_writes_nothing_and_reopens_the_ask(
    db_session, monkeypatch
):
    """GUARD (was the adversarial reproduction; flipped after the same-branch
    fix). Drives the verbatim F-1/F-11 refusal through the REAL interview door
    (`session._handle_interview_confirmation_answer`) against a KEYLESS
    pending confirmation and reads the vault afterward.

    Contrast with `test_uat_vault_confirmation_refusal.py::
    test_the_refusal_writes_nothing_and_re_asks_on_the_interview_door`, whose
    KEYED entry (built by the same real builder, same refusal text) writes
    nothing. Here the only fixture difference is `option_keys: []` — and now
    the outcome is the SAME: nothing written, the ask reopened.

    Mutation: restore the old `return match_skill_decision_text(chosen)`
    fallback (the raw answer, not the matched option) in
    `confirmations.resolve_skill_decision`'s keyless branch on a scratchpad
    copy of the file — this test goes red by name.
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

    # The vault is untouched: no `Agile Collaboration` at any status, on the
    # KEYLESS path — the same guarantee #730 already gave the keyed path.
    await db_session.refresh(record)
    names = _skill_names(record)
    assert INCOMING not in names, (
        "the keyless legacy path must be equally guarded — instead the vault "
        f"after the refusal is {names!r}, reproducing #730/F-1: a denial "
        "containing the rejected option's own word ('separate') wrote the "
        "skill it named, at status=confirmed, on a KEYLESS pending "
        "confirmation"
    )

    # And the interview does NOT believe the turn is settled — it re-asks,
    # exactly like the keyed door's behaviour for the identical text.
    session_mod._ask_or_complete_at.assert_not_awaited()
    assert resp.complete is False
    assert entry["question"] in (resp.question or "")
    assert resp.choices == entry["options"]
    assert state.get("pending_interview_confirmation") == entry
    assert state["questions_asked"] == 3
