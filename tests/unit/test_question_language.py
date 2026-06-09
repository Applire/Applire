# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire. AGPL-3.0-or-later — see LICENSE.

import pytest
from types import SimpleNamespace

from applire.prompts.interview import with_language, QUESTION_SYSTEM_PROMPT


def test_with_language_de_names_german():
    out = with_language(QUESTION_SYSTEM_PROMPT, "de")
    assert "German" in out
    assert "OUTPUT LANGUAGE" in out
    assert out.startswith(QUESTION_SYSTEM_PROMPT)  # original preserved


def test_with_language_en_names_english():
    out = with_language(QUESTION_SYSTEM_PROMPT, "en")
    assert "English" in out
    assert "never mirror" in out.lower()


def test_with_language_unknown_falls_back_to_english():
    out = with_language(QUESTION_SYSTEM_PROMPT, "fr")
    assert "English" in out


from applire.prompts.review_question_language import (
    build_question_language_review_prompt,
    build_question_language_refinement_prompt,
)


def test_review_prompt_mentions_required_language_and_content():
    p = build_question_language_review_prompt(
        "English", {"question": "Was ist Ihre Erfahrung?", "choices": ["Ja", "Nein"]}
    )
    assert "English" in p
    assert "Was ist Ihre Erfahrung?" in p
    assert "Ja" in p


def test_refinement_prompt_includes_feedback_and_draft():
    p = build_question_language_refinement_prompt(
        {"question": "Was ist...?", "choices": None}, "Rewrite in English."
    )
    assert "Rewrite in English." in p
    assert "Was ist...?" in p


def test_review_prompt_handles_null_choices():
    p = build_question_language_review_prompt(
        "German", {"question": "Wie lange sind Sie dabei?", "choices": None}
    )
    assert "null" not in p  # None -> [] not serialised as JSON null
    assert "[]" in p


from applire.services.session import get_ui_language


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeDB:
    def __init__(self, row):
        self._row = row

    async def execute(self, *args, **kwargs):
        return _FakeResult(self._row)


@pytest.mark.asyncio
async def test_get_ui_language_returns_stored_value():
    db = _FakeDB(SimpleNamespace(ui_language="de"))
    assert await get_ui_language(db) == "de"


@pytest.mark.asyncio
async def test_get_ui_language_defaults_en_when_no_row():
    db = _FakeDB(None)
    assert await get_ui_language(db) == "en"
