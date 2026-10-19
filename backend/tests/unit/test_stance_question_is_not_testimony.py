# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire. Contact: kontakt@applire.de

"""ADR-061 amended 2026-10-18 (ruling DEL-5 (1)) — the question is not testimony.

Delivery run 2 (Hazelnut build 1, LLM log line 29): the interview asked
"Hast du … Investitionsplanung, Arbeitssicherheit oder ISO 45001 verantwortet
oder dabei mitgewirkt …?" and the answer said "Bei Investitionen war ich beim
MES-Projekt 2023 beteiligt". The reconciler emitted
``upsert_skill Investitionsplanung``; the testimony predicate accepted it
deterministically because its corpus was gap + question + answer, and the
question names the token. It reached the CV and the letter as "verantwortete
… Investitionsplanung".

The contract pinned here: for a built-in interview turn the deterministic
accept reads the ANSWER only; a question-only token goes to adjudication, which
sees the turn split into QUESTION / ANSWER, and whose citation must come from
the ANSWER.
"""
from __future__ import annotations

from typing import Any

import pytest

from applire.prompts.stance_adjudication import (
    STANCE_ADJUDICATION_SYSTEM_PROMPT,
    TURN_ANSWER_HEADING,
    TURN_QUESTION_HEADING,
    render_interview_turn,
)
from applire.services.profile.reconcile.ops import AddBullets, UpsertSkill
from applire.services.profile.reconcile.stance import enforce_stance

# Verbatim new_info of the delivery-run-2 turn (synthetic persona, panel case).
_DEL5_TURN = {
    "gap": "Investitionen und Arbeitssicherheit",
    "question": (
        "Hast du in deiner Laufbahn bereits Investitionsplanung, Arbeitssicherheit "
        "oder ISO 45001 verantwortet oder dabei mitgewirkt – zum Beispiel im "
        "Zusammenhang mit Budgetentscheidungen, Sicherheitsmaßnahmen oder Audits?"
    ),
    "answer": (
        "Bei Weberit verantworte ich seit 2017 ein Budget von ca. 6 Mio. € für "
        "Personal, Instandhaltung und Material-Gemeinkosten. Bei Investitionen war "
        "ich beim MES-Projekt 2023 beteiligt, von der Systemauswahl bis zum Rollout. "
        "Eine konkrete Investitionssumme dazu kann ich nicht nennen. Zur "
        "Arbeitssicherheit: Ich bin ausgebildeter Sicherheitsbeauftragter und habe "
        "bei Weberit die Vorbereitung der ISO-45001-Zertifizierung für die "
        "Produktion begleitet."
    ),
}


class _StubAdjudicator:
    """Answers the adjudication call with a fixed verdict and records the prompt."""

    def __init__(self, answer: str, quote: str) -> None:
        self.answer = answer
        self.quote = quote
        self.prompts: list[str] = []

    async def aparse_json(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        assert kwargs.get("system") == STANCE_ADJUDICATION_SYSTEM_PROMPT
        self.prompts.append(prompt)
        return {"answer": self.answer, "quote": self.quote}


@pytest.mark.asyncio
async def test_question_only_token_is_not_confirmed_without_adjudication() -> None:
    ops = [UpsertSkill(name="Investitionsplanung", category="domain")]
    out = await enforce_stance(ops, denials=[], new_info=_DEL5_TURN, source="interview")
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


@pytest.mark.asyncio
async def test_a_yes_quoting_the_question_does_not_confirm() -> None:
    # The adjudicator answers "yes" but its citation is the QUESTION's span —
    # the candidate never said it, so the citation check must reject it.
    stub = _StubAdjudicator("yes", "Investitionsplanung, Arbeitssicherheit oder ISO 45001 verantwortet")
    ops = [UpsertSkill(name="Investitionsplanung", category="domain")]
    out = await enforce_stance(
        ops, denials=[], new_info=_DEL5_TURN, source="interview", provider=stub,
    )
    assert out[0].status == "unconfirmed"
    assert len(stub.prompts) == 1


@pytest.mark.asyncio
async def test_adjudicator_sees_question_and_answer_split() -> None:
    stub = _StubAdjudicator("unclear", "")
    ops = [UpsertSkill(name="Investitionsplanung", category="domain")]
    await enforce_stance(ops, denials=[], new_info=_DEL5_TURN, source="interview", provider=stub)
    (prompt,) = stub.prompts
    q_at = prompt.index(TURN_QUESTION_HEADING)
    a_at = prompt.index(TURN_ANSWER_HEADING)
    assert q_at < a_at
    assert _DEL5_TURN["question"] in prompt[q_at:a_at]
    assert _DEL5_TURN["answer"] in prompt[a_at:]


@pytest.mark.asyncio
async def test_token_in_the_answer_is_confirmed_without_a_call() -> None:
    stub = _StubAdjudicator("no", "")
    ops = [UpsertSkill(name="Arbeitssicherheit", category="domain")]
    out = await enforce_stance(
        ops, denials=[], new_info=_DEL5_TURN, source="interview", provider=stub,
    )
    assert out[0].status == "confirmed"
    assert stub.prompts == []


@pytest.mark.asyncio
async def test_hyphenated_standard_in_the_answer_is_confirmed_without_a_call() -> None:
    stub = _StubAdjudicator("no", "")
    ops = [UpsertSkill(name="ISO 45001", category="domain")]
    out = await enforce_stance(
        ops, denials=[], new_info=_DEL5_TURN, source="interview", provider=stub,
    )
    assert out[0].status == "confirmed"
    assert stub.prompts == []


@pytest.mark.asyncio
async def test_answer_that_says_yes_to_the_question_still_confirms() -> None:
    # #127's design intent survives: "Yes — six years" affirms the question's
    # token, now through the adjudicator quoting the ANSWER.
    turn = {
        "gap": "Python experience",
        "question": "Do you have professional Python experience?",
        "answer": "Yes — six years, daily, in production.",
    }
    stub = _StubAdjudicator("yes", "Yes — six years, daily, in production.")
    ops = [UpsertSkill(name="Python", category="technical")]
    out = await enforce_stance(ops, denials=[], new_info=turn, source="interview", provider=stub)
    assert out[0].status == "confirmed"


@pytest.mark.asyncio
async def test_question_only_technology_tag_is_dropped() -> None:
    ops = [AddBullets(target="w1", technologies=["Investitionsplanung", "MES"])]
    out = await enforce_stance(ops, denials=[], new_info=_DEL5_TURN, source="interview")
    assert out[0].technologies == ["MES"]


@pytest.mark.asyncio
async def test_agent_interview_door_unchanged() -> None:
    # Already answer-only (adversarial B3); the split does not apply.
    turn = {"gap": "Kubernetes", "question": "Kubernetes?", "answer": "I ran our container platform."}
    stub = _StubAdjudicator("unclear", "")
    ops = [UpsertSkill(name="Kubernetes", category="technical")]
    await enforce_stance(ops, denials=[], new_info=turn, source="agent_interview", provider=stub)
    (prompt,) = stub.prompts
    assert TURN_QUESTION_HEADING not in prompt
    assert "TURN:\nI ran our container platform.\n" in prompt


def test_render_without_context_is_the_answer() -> None:
    assert render_interview_turn(gap=None, question="  ", answer="Nur die Antwort.") == "Nur die Antwort."


def test_adjudication_prompt_carries_the_participation_rule() -> None:
    assert "TAKING PART IS NOT HAVING IT" in STANCE_ADJUDICATION_SYSTEM_PROMPT
    assert "only the ANSWER is the" in STANCE_ADJUDICATION_SYSTEM_PROMPT
