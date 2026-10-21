# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""783:L97 (ADR-061 amended 2026-10-20) — the DEL-5 sibling: on the built-in
interview the denial floor's independent-affirmation release and the story
figure grounding read the candidate's ANSWER, never the gap label or question.

Reproduced by fixture before the fix (both assertions below failed): a question
that names "CSS" released the candidate's containment denial of "Tailwind CSS"
for an op "CSS", and a question that states "12" grounded a story outcome "12"
the answer never stated. Over every captured interview reconcile call on disk
(882 calls) the verdict never depended on the question — a latent mechanism.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_backend = Path(__file__).resolve().parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.profile.reconcile.ops import UpsertSkill, UpsertStory  # noqa: E402
from applire.services.profile.reconcile.stance import enforce_stance  # noqa: E402


def _turn(question: str, answer: str) -> dict:
    return {"gap": "Frontend", "question": question, "answer": answer}


@pytest.mark.asyncio
async def test_a_question_naming_the_part_does_not_release_a_containment_denial():
    ops = [UpsertSkill(name="CSS", status="confirmed")]
    kept = await enforce_stance(
        ops,
        denials=["Tailwind CSS"],
        new_info=_turn(
            question="Haben Sie mit CSS gearbeitet, etwa mit Tailwind CSS?",
            answer="Tailwind CSS habe ich nie benutzt.",
        ),
        source="interview",
        provider=None,
    )
    assert kept == []


@pytest.mark.asyncio
async def test_the_answer_affirming_the_part_still_releases_it():
    ops = [UpsertSkill(name="CSS", status="confirmed")]
    kept = await enforce_stance(
        ops,
        denials=["Tailwind CSS"],
        new_info=_turn(
            question="Haben Sie mit Tailwind CSS gearbeitet?",
            answer="Tailwind CSS nie, aber reines CSS schreibe ich seit Jahren täglich.",
        ),
        source="interview",
        provider=None,
    )
    assert [o.name for o in kept] == ["CSS"]


def _story(outcome: str) -> UpsertStory:
    return UpsertStory(
        ref="t1",
        title="Teamaufbau",
        challenge="Neues Team",
        mechanism="Einstellungen",
        outcome=outcome,
    )


@pytest.mark.asyncio
async def test_a_figure_only_the_question_states_does_not_ground_a_story():
    kept = await enforce_stance(
        [_story("Team auf 12 Personen aufgebaut")],
        denials=[],
        new_info=_turn(
            question="Sie haben ein Team von 12 Personen aufgebaut — wie?",
            answer="Ja, das stimmt, über gezielte Einstellungen.",
        ),
        source="interview",
        provider=None,
    )
    assert kept == []


@pytest.mark.asyncio
async def test_a_figure_the_answer_states_grounds_the_story():
    kept = await enforce_stance(
        [_story("Team auf 12 Personen aufgebaut")],
        denials=[],
        new_info=_turn(
            question="Wie groß war das Team, das Sie aufgebaut haben?",
            answer="Ich habe ein Team von 12 Personen aufgebaut.",
        ),
        source="interview",
        provider=None,
    )
    assert len(kept) == 1
