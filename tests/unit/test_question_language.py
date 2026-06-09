# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire. AGPL-3.0-or-later — see LICENSE.

import pytest
from types import SimpleNamespace

from applire.prompts.interview import with_language, QUESTION_SYSTEM_PROMPT
from applire.providers.llm.base import LLMProvider
from applire.services.interview_graph import question_generator_with_profile


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


# ---------------------------------------------------------------------------
# B6: question_generator_with_profile — lang directive + language review
# ---------------------------------------------------------------------------


class _CapturingProvider(LLMProvider):
    """Captures every system prompt; approves the language review."""

    def __init__(self):
        super().__init__()
        self.systems: list[str] = []

    async def acomplete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096):
        self.systems.append(system or "")
        return "Beispiel-Frage?"

    async def aparse_json(self, prompt, *, system=None, temperature=0.1, max_tokens=4096):
        self.systems.append(system or "")
        if "language reviewer" in (system or "").lower():
            return {"approved": True, "issues": [], "feedback": ""}
        return {"question": "Beispiel-Frage?", "choices": None}


def _mode_a_state():
    return {
        "mode": "targeted",
        "critical_gaps": ["c1"],
        "current_gap_index": 0,
        "messages": [],
        "gap_clusters_by_id": {
            "c1": {
                "id": "c1",
                "label": "Kubernetes",
                "gaps": ["Kubernetes"],
                "jd_skills": ["Kubernetes"],
                "jd_context": "Diese Rolle erfordert Kubernetes-Erfahrung.",
            }
        },
    }


def _generation_systems(systems):
    return [s for s in systems if "language reviewer" not in s.lower()]


@pytest.mark.asyncio
async def test_mode_a_question_gets_english_directive_over_german_context():
    p = _CapturingProvider()
    await question_generator_with_profile(_mode_a_state(), {}, p, lang="en")
    gen = _generation_systems(p.systems)
    assert any("English" in s and "OUTPUT LANGUAGE" in s for s in gen)


@pytest.mark.asyncio
async def test_mode_a_question_gets_german_directive():
    p = _CapturingProvider()
    await question_generator_with_profile(_mode_a_state(), {}, p, lang="de")
    gen = _generation_systems(p.systems)
    assert any("German" in s for s in gen)


@pytest.mark.asyncio
async def test_guided_question_gets_directive():
    p = _CapturingProvider()
    state = {"mode": "guided", "critical_gaps": ["skills"], "current_gap_index": 0, "messages": []}
    await question_generator_with_profile(state, {}, p, lang="en", job_context={"role_title": "QA"})
    gen = _generation_systems(p.systems)
    assert any("English" in s for s in gen)


@pytest.mark.asyncio
async def test_followup_question_gets_directive():
    p = _CapturingProvider()
    state = _mode_a_state()
    await question_generator_with_profile(
        state, {}, p, lang="de", follow_up_hint="ask about adjacent GMP work"
    )
    gen = _generation_systems(p.systems)
    assert any("German" in s for s in gen)


@pytest.mark.asyncio
async def test_review_disabled_returns_draft_unchanged(monkeypatch):
    import applire.services.interview_graph as ig
    monkeypatch.setattr(ig, "INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES", 0)
    draft = {"question": "x", "choices": None}
    out = await ig._review_question_language(draft, "en", _CapturingProvider())
    assert out == draft


@pytest.mark.asyncio
async def test_reviewer_regenerates_on_wrong_language(monkeypatch):
    import applire.services.interview_graph as ig
    monkeypatch.setattr(ig, "INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES", 1)

    class _RejectThenFix(LLMProvider):
        def __init__(self):
            super().__init__()
            self.regenerated = False

        async def acomplete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096):
            return ""

        async def aparse_json(self, prompt, *, system=None, temperature=0.1, max_tokens=4096):
            if "language reviewer" in (system or "").lower():
                return {"approved": False, "issues": ["wrong language"], "feedback": "Rewrite in English."}
            self.regenerated = True
            return {"question": "Corrected question?", "choices": None}

    p = _RejectThenFix()
    out = await ig._review_question_language({"question": "Frage?", "choices": None}, "en", p)
    assert p.regenerated is True
    assert out["question"] == "Corrected question?"
