# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#669 residual (interview) — a confirmation re-rendered after an answer
loses its language and its option keys.

Delivery-run evidence, 2026-09-17 close-out (`Documents/Runs/Nougat/close-out/
delivery-run/2026-09-17-operations-marcus-de.md`, outcome 13; artifacts
`11-interview-q2.json` / `11b-interview-confirm-sap-pp.json`): a German
targeted-interview turn parked TWO skill-namesake confirmations (SAP PP,
SAP MM), both rendered German with ``option_keys: ["distinct", "merge"]`` on
the turn that raised them (`_ask_confirmation` → `_to_confirmation_prompts`,
already correct — see ``test_669_confirmation_option_keys.py``). After the
candidate answered the FIRST (SAP PP), the response carrying the SECOND
(SAP MM) rendered in ENGLISH with ``option_keys: []`` — the exact #669 harm:
with no keys the answer resolves through the back-compat English substring
matcher, so a German "zusammenführen" reply would resolve to ``distinct`` and
the vault would gain the skill instead of merging it.

Root cause, pinned: #669's fix (ADR-063 amended 2026-09-05) put the
lang-aware, key-carrying render at the ONE place a turn's FIRST confirmation
is surfaced (``_ask_confirmation`` → ``_to_confirmation_prompts``). Three
further sites emit a confirmation without going through that discipline:

1. ``services/session.py::_ask_queued_confirmation`` (~line 746) — promotes
   the next confirmation off ``pending_interview_confirmation_queue`` after
   the current one is answered. It read the parked dict's plain ``question``/
   ``options`` fields directly (the English form stored for pre-#669 back-
   compat, see ``reconcile/confirmations.py::_build``) and built
   ``ConfirmationPrompt(...)`` with no ``option_keys=`` at all — THE site that
   produced ``11b``.
2. ``services/session.py::send_message``'s hard-ceiling completion branch
   (~line 2607) — calls ``_to_confirmation_prompts(turn.pending_confirmations)``
   with no ``lang`` argument, silently defaulting to ``"en"``.
3. ``routers/profile_enrich.py::respond_to_enrich`` (Mode C door, ~line 368) —
   builds ``ConfirmationPrompt`` directly off the raw ``PendingConfirmation``
   fields, same as (1): no rendering, no ``option_keys``.

Fixed: every site above now renders through the session's resolved ``lang``
— (1) and (3) via the SAME ``_to_confirmation_prompts``/``render_confirmation``
discipline #669 already established, never a raw field or a default language.

The MCP door (``applire/mcp/server.py``) re-emits confirmations only by
forwarding ``session_svc.send_message``'s/``resolve_gap``'s already-built
``SessionMessageResponse.pending_confirmations`` — it constructs no
``ConfirmationPrompt`` of its own (grepped: the only ``ConfirmationPrompt(``/
``_to_confirmation_prompts(`` call sites in ``backend/applire/`` are the four
covered here). Fixing the service layer therefore also fixes the MCP tier by
construction; asserting that over the stdio transport is out of scope here
(its conftest restarts docker — COMMON-BRIEF §1) and is not repeated as a
separate test.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from applire.models.profile import MasterProfile
from applire.models.session import InterviewSession
from applire.models.user_settings import UserSettings
from applire.providers.llm.mock import MockLLMProvider
from applire.schemas.enrich import EnrichRespondRequest
from applire.schemas.profile import MasterProfileData
from applire.services.color_detection import _CE_STUB_USER_ID
from applire.services.profile.reconcile.confirmations import skill_containment_confirmation
from applire.services.session import (
    _ask_queued_confirmation,
    _build_state,
    _confirmation_state,
    send_message,
)
from tests.support.profile_factory import make_master_profile

# #353's fixture: one turn naming SAP PP/MM/SD against an existing 'SAP' —
# the mock's "profile reconciler" branch keys on "sap pp" in the prompt and
# always returns three containment ops. Reused here unchanged; these tests
# only assert on the SECOND confirmation's rendering, exactly the transition
# the delivery run captured (`11` -> `11b`).
_MODULES_ANSWER = (
    "Ich arbeite taeglich mit SAP PP, SAP MM und SAP SD — das sind drei "
    "unterschiedliche Module mit unterschiedlichen Aufgaben."
)


async def _set_ui_language(db, lang: str) -> None:
    db.add(UserSettings(user_id=_CE_STUB_USER_ID, ui_language=lang))
    await db.commit()


async def _seed_sap_profile(db) -> MasterProfile:
    profile_data = MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Test User"},
            "skills": [
                {"name": "SAP", "category": "technical", "proficiency": "advanced"}
            ],
        }
    )
    profile = make_master_profile(profile_json=profile_data.model_dump(mode="json"))
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


async def _seed_sap_session(
    db, profile: MasterProfile, *, hard_ceiling: int, mode: str = "targeted"
) -> InterviewSession:
    gaps = ["cluster-erp"]
    state = _build_state(
        mode=mode,
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
        hard_ceiling=hard_ceiling,
    )
    state["current_question"] = "Which SAP modules do you work with?"
    state["messages"] = [
        {"role": "assistant", "content": "Which SAP modules do you work with?"}
    ]

    record = InterviewSession(
        job_analysis_id=None,
        gap_analysis_id=None,
        profile_id=profile.id,
        mode=mode,
        status="active",
        state=state,
        hard_ceiling=hard_ceiling,
        questions_asked=0,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


# ── (a) the delivery-run shape, real service path, seam test ──────────────────


@pytest.mark.asyncio
async def test_second_confirmation_of_a_turn_rerenders_german_after_the_first_is_answered(
    async_db,
):
    """The exact `11` -> `11b` transition, driven through the real
    `send_message` state machine under a German `ui_language`. Turn 1 surfaces
    both confirmations in German with keys (already correct pre-fix); turn 2
    answers the FIRST (SAP PP) and must re-render the SECOND (SAP MM) in
    German, WITH `option_keys == ["distinct", "merge"]` — not the English
    plain form with empty keys #669 left this queued-promotion path emitting.
    """
    await _set_ui_language(async_db, "de")
    profile = await _seed_sap_profile(async_db)
    record = await _seed_sap_session(async_db, profile, hard_ceiling=12)
    provider = MockLLMProvider()

    r1 = await send_message(record.id, _MODULES_ANSWER, async_db, provider)
    assert len(r1.pending_confirmations or []) == 3
    assert "SAP PP" in r1.question
    assert "teilt ein Wort" in r1.question, f"turn 1 must render German, got {r1.question!r}"
    first_answer = r1.pending_confirmations[0].options[0]  # the German 'distinct' button
    assert first_answer == "„SAP PP“ als eigene Fähigkeit hinzufügen"

    r2 = await send_message(record.id, first_answer, async_db, provider)

    # The top-level question/choices the candidate sees next ...
    assert "SAP MM" in r2.question, f"expected the SAP MM confirmation, got {r2.question!r}"
    assert "teilt ein Wort" in r2.question, (
        f"the SECOND confirmation must also render German, got {r2.question!r}"
    )
    assert r2.choices == [
        "„SAP MM“ als eigene Fähigkeit hinzufügen",
        "In die vorhandene Fähigkeit zusammenführen",
    ]

    # ... and the nested pending_confirmations entry the agent/UI door reads
    # option_keys off (ADR-058 parity) — this is the field #669 exists for.
    sap_mm_prompt = r2.pending_confirmations[0]
    assert sap_mm_prompt.option_keys == ["distinct", "merge"], (
        f"empty/missing option_keys resolves a German answer through the "
        f"back-compat ENGLISH substring matcher (#669's exact harm); got "
        f"{sap_mm_prompt.option_keys!r}"
    )
    assert "teilt ein Wort" in sap_mm_prompt.question


# ── (b) one seam test per changed construction site ────────────────────────────


@pytest.mark.asyncio
async def test_ask_queued_confirmation_renders_the_promoted_head_in_the_given_language(
    async_db,
):
    """Direct seam test for `services/session.py::_ask_queued_confirmation`
    (~line 746) — THE site that produced `11b`. Bypasses the full
    `send_message` plumbing: hand-builds the queue of already-parked
    (state-dict-shaped) confirmations `_ask_confirmation` would have left on
    `pending_interview_confirmation_queue`, and asserts the function's own
    output — not a caller's — carries the reader's language and the keys."""
    profile = await _seed_sap_profile(async_db)
    record = await _seed_sap_session(async_db, profile, hard_ceiling=12)
    state = dict(record.state)

    op_mm = skill_containment_confirmation(incoming_skill="SAP MM", related=["SAP"], context={})
    op_sd = skill_containment_confirmation(incoming_skill="SAP SD", related=["SAP"], context={})
    queue = [_confirmation_state(op_mm), _confirmation_state(op_sd)]
    expected_question, expected_options = op_mm.rendered("de")

    result = await _ask_queued_confirmation(record, state, async_db, queue, 0, "de")

    assert result.question == expected_question
    assert result.choices == expected_options
    assert len(result.pending_confirmations) == 2
    assert result.pending_confirmations[0].question == expected_question
    assert result.pending_confirmations[0].options == expected_options
    assert result.pending_confirmations[0].option_keys == list(op_mm.option_keys)
    assert result.pending_confirmations[1].option_keys == list(op_sd.option_keys)

    # English asked for too — door parity, not just a German special case.
    state_en = dict(record.state)
    expected_question_en, expected_options_en = op_mm.rendered("en")
    result_en = await _ask_queued_confirmation(record, state_en, async_db, queue, 0, "en")
    assert result_en.question == expected_question_en
    assert result_en.choices == expected_options_en
    assert result_en.pending_confirmations[0].option_keys == list(op_mm.option_keys)


@pytest.mark.asyncio
async def test_hard_ceiling_completion_carries_the_pending_confirmation_in_german(async_db):
    """Direct seam test for the ceiling-hit branch of `send_message`
    (~line 2607, inside `_complete_session`'s `pending_confirmations=` arg) —
    a targeted micro-session (`hard_ceiling=1`) that raises a reconciler
    ambiguity on its ONE allowed turn must still surface it rendered in the
    session's language, not `_to_confirmation_prompts`'s bare `"en"` default.
    """
    await _set_ui_language(async_db, "de")
    profile = await _seed_sap_profile(async_db)
    record = await _seed_sap_session(async_db, profile, hard_ceiling=1)
    provider = MockLLMProvider()

    result = await send_message(record.id, _MODULES_ANSWER, async_db, provider)

    assert result.complete is True
    assert result.reason == "max_questions_reached"
    assert result.pending_confirmations, "the ceiling-hit turn's ambiguity must not be dropped"
    for prompt in result.pending_confirmations:
        assert "teilt ein Wort" in prompt.question, (
            f"ceiling-hit completion must render German, got {prompt.question!r}"
        )
        assert prompt.option_keys == ["distinct", "merge"], (
            f"ceiling-hit completion dropped option_keys: {prompt.option_keys!r}"
        )


@pytest.mark.asyncio
async def test_profile_enrich_mode_c_door_renders_confirmation_in_german(async_db):
    """Direct seam test for the Mode C door
    (`routers/profile_enrich.py::respond_to_enrich`, ~line 368-391) — a
    SEPARATE direct `ConfirmationPrompt(...)` construction off the raw
    `PendingConfirmation` fields, same defect class as (1): no `lang`
    rendering, no `option_keys`. This is 09-13's originally-found Mode C
    instance of the seam, still open on this call site independent of the
    interview-turn fix above.
    """
    from applire.routers.profile_enrich import respond_to_enrich

    await _set_ui_language(async_db, "de")
    profile = await _seed_sap_profile(async_db)
    record = await _seed_sap_session(async_db, profile, hard_ceiling=12, mode="profile_enrich")
    provider = MockLLMProvider()

    response = await respond_to_enrich(
        record.id, EnrichRespondRequest(answer=_MODULES_ANSWER), async_db, provider, None
    )

    assert response.pending_confirmations, "the Mode C door must surface the ambiguity"
    assert "teilt ein Wort" in (response.next_question or ""), (
        f"Mode C's next_question must render German, got {response.next_question!r}"
    )
    for prompt in response.pending_confirmations:
        assert "teilt ein Wort" in prompt.question
        assert prompt.option_keys == ["distinct", "merge"], (
            f"Mode C dropped option_keys: {prompt.option_keys!r}"
        )
