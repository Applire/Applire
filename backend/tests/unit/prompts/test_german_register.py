# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#311 — drift guards for the German du/Sie register in candidate-facing text.

BRAND.md §2.3: "Du, not Sie" — consistent across product UI. The catalogs
(``frontend/messages/de.json``) are guarded on the frontend side; this module
guards the two backend sources of candidate-facing German prose:

1. the interview / assist prompts, which produce German the candidate reads
   (prompt-first, category B: the register was never asked for, so the model
   defaulted to formal Sie — see the module docstrings for the rule added);
2. ``outcome_critic._MESSAGES["de"]``, which is deterministic German text.

Cover-letter prompts are deliberately NOT covered here: that letter addresses
the *employer*, where Sie is correct and mandated by ``prompts/cover_letter``.

Pure string checks, no LLM. Prompt-effect changes still need a real charter
run to confirm model compliance (ADR-062 clause 7).
"""
from __future__ import annotations

import re

import pytest

# Capitalised formal pronouns. Lowercase "sie/ihr" (third person) is fine.
FORMAL_PRONOUNS = re.compile(r"\b(Sie|Ihnen|Ihr|Ihre|Ihren|Ihrem|Ihrer|Ihres)\b")


@pytest.fixture
def interview_question_prompts():
    from applire.prompts.interview import (
        DENIAL_PROBE_QUESTION_SYSTEM_PROMPT,
        FOLLOW_UP_QUESTION_SYSTEM_PROMPT,
        GUIDED_QUESTION_SYSTEM_PROMPT,
        QUESTION_SYSTEM_PROMPT,
    )

    return {
        "targeted": QUESTION_SYSTEM_PROMPT,
        "guided": GUIDED_QUESTION_SYSTEM_PROMPT,
        "follow_up": FOLLOW_UP_QUESTION_SYSTEM_PROMPT,
        "denial_probe": DENIAL_PROBE_QUESTION_SYSTEM_PROMPT,
    }


def test_with_language_de_instructs_the_du_register(interview_question_prompts):
    """Every interview question prompt goes through with_language(), so the
    register rule belongs there — one logical operation, one implementation
    (ADR-066)."""
    from applire.prompts.interview import with_language

    for name, prompt in interview_question_prompts.items():
        rendered = with_language(prompt, "de")
        lower = rendered.lower()
        assert "du-form" in lower or '"du"' in lower, name
        assert "never" in lower and "sie" in lower, name


def test_with_language_en_carries_no_german_register_clause(
    interview_question_prompts,
):
    """English has no du/Sie distinction — the clause would be noise, and the
    word "Sie" in an English prompt invites the model to use it."""
    from applire.prompts.interview import with_language

    for name, prompt in interview_question_prompts.items():
        rendered = with_language(prompt, "en")
        added = rendered[len(prompt) :]
        assert "du-form" not in added.lower(), name
        assert "Sie" not in added, name


def test_with_language_de_keeps_the_output_language_directive(
    interview_question_prompts,
):
    """The register clause must not displace the language directive it joins."""
    from applire.prompts.interview import with_language

    for name, prompt in interview_question_prompts.items():
        rendered = with_language(prompt, "de")
        assert "OUTPUT LANGUAGE" in rendered, name
        assert "German" in rendered, name


def test_cv_assist_question_prompt_instructs_the_du_register():
    """Kaile's assist question is German prose the candidate reads directly."""
    from applire.services.cv_assist import _question_prompt

    prompt = _question_prompt("Einleitung", "Erfahrener Entwickler", "Python")
    lower = prompt.lower()
    assert "du-form" in lower or '„du"' in lower or '"du"' in lower
    assert "sie" in lower


def test_cv_assist_suggestion_prompt_stays_register_free():
    """The suggestion prompt writes CV *section* prose, not text addressed to
    the candidate. A du-directive here would leak "du" into the document, so
    this prompt is deliberately left alone."""
    from applire.services.cv_assist import _suggestion_prompt

    prompt = _suggestion_prompt(
        "Einleitung", "Erfahrener Entwickler", "Seit 2019 Python", "Python"
    )
    assert "Du-Form" not in prompt
    assert "Sie-Form" not in prompt


def test_outcome_critic_german_messages_use_du():
    """Deterministic German advisory text — no model involved, so this one is a
    plain string defect, not a prompt defect."""
    from applire.services.outcome_critic import _MESSAGES

    offenders = {
        key: value
        for key, value in _MESSAGES["de"].items()
        if FORMAL_PRONOUNS.search(value)
    }
    assert offenders == {}
