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

    async def acomplete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096, disable_thinking=None):
        self.systems.append(system or "")
        return "Beispiel-Frage?"

    async def aparse_json(self, prompt, *, system=None, temperature=0.1, max_tokens=4096, disable_thinking=None):
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

        async def acomplete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096, disable_thinking=None):
            return ""

        async def aparse_json(self, prompt, *, system=None, temperature=0.1, max_tokens=4096, disable_thinking=None):
            if "language reviewer" in (system or "").lower():
                return {"approved": False, "issues": ["wrong language"], "feedback": "Rewrite in English."}
            self.regenerated = True
            return {"question": "Corrected question?", "choices": None}

    p = _RejectThenFix()
    out = await ig._review_question_language({"question": "Frage?", "choices": None}, "en", p)
    assert p.regenerated is True
    assert out["question"] == "Corrected question?"


@pytest.mark.asyncio
async def test_mode_a_choices_survive_review_round_trip():
    class _ChoicesProvider(LLMProvider):
        async def acomplete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096, disable_thinking=None):
            return ""

        async def aparse_json(self, prompt, *, system=None, temperature=0.1, max_tokens=4096, disable_thinking=None):
            if "language reviewer" in (system or "").lower():
                return {"approved": True, "issues": [], "feedback": ""}
            return {"question": "Pick one?", "choices": ["A", "B", "C"]}

    out = await question_generator_with_profile(_mode_a_state(), {}, _ChoicesProvider(), lang="en")
    assert out["question"] == "Pick one?"
    assert out["choices"] == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# M8 deterministic backstop (2026-07-29) — a choice's "level" tag must
# survive the language-review/refinement pass even when the reviewer drops,
# translates, or renames the field. Its only OTHER guarantee is prompt
# instruction; without this backstop every honest denial choice silently
# starts being deleted again (the exact bug this branch fixed).
# ---------------------------------------------------------------------------


def test_carry_levels_by_index_restores_dropped_level_on_count_match():
    from applire.services.interview_graph import _carry_levels_by_index

    pre = [
        {"text": "Yes, I've used it.", "level": "direct"},
        {"text": "Somewhat.", "level": "partial"},
        {"text": "No, never.", "level": "denial"},
    ]
    # Reviewer rewrote the wording and DROPPED "level" entirely.
    reviewed = [
        {"text": "Yes, I have used it."},
        {"text": "A little."},
        {"text": "No, I never have."},
    ]

    out = _carry_levels_by_index(pre, reviewed)

    assert [c["level"] for c in out] == ["direct", "partial", "denial"]
    # Wording stays the REVIEWED text, not the pre-review text — only the
    # level is carried, never the content the review pass rewrote.
    assert [c["text"] for c in out] == [
        "Yes, I have used it.", "A little.", "No, I never have.",
    ]


def test_carry_levels_by_index_falls_back_on_count_mismatch():
    from applire.services.interview_graph import _carry_levels_by_index

    pre = [
        {"text": "Yes, I've used it.", "level": "direct"},
        {"text": "No, never.", "level": "denial"},
    ]
    # Reviewer dropped one choice — positional mapping is no longer safe.
    reviewed = [{"text": "No, I never have."}]

    out = _carry_levels_by_index(pre, reviewed)

    # Falls back to the reviewed output exactly as-is — no guessed level.
    assert out == reviewed


def test_carry_levels_by_index_never_overrides_a_valid_reviewed_level_on_reorder():
    """F1 regression: same count, choices REORDERED, reviewed levels intact.

    The "do not reorder choices" guarantee lives in the SAME prompt sentence
    as "preserve level verbatim" (prompts/review_question_language.py). A
    model that drops one plausibly drops/ignores the other — so a positional
    backstop that OVERRIDES an already-valid reviewed level silently swaps
    the levels onto the wrong text. The backstop must only fill in a level
    that is missing or unrecognised on the reviewed choice, never override
    one that is already valid.
    """
    from applire.services.interview_graph import _carry_levels_by_index

    pre = [
        {"text": "Yes, hands-on Kubernetes experience.", "level": "direct"},
        {"text": "No, I have never worked with Kubernetes.", "level": "denial"},
    ]
    # Reviewer returned the SAME two choices, REORDERED, with correct levels
    # attached to their own (now reordered) text.
    reviewed = [
        {"text": "No, I have never worked with Kubernetes.", "level": "denial"},
        {"text": "Yes, hands-on Kubernetes experience.", "level": "direct"},
    ]

    out = _carry_levels_by_index(pre, reviewed)

    # Each choice must keep ITS OWN level, not the level of whatever sat at
    # the same index in the pre-review draft.
    by_text = {c["text"]: c["level"] for c in out}
    assert by_text["No, I have never worked with Kubernetes."] == "denial"
    assert by_text["Yes, hands-on Kubernetes experience."] == "direct"


@pytest.mark.asyncio
async def test_mode_a_denial_survives_and_fabrication_is_dropped_when_reviewer_reorders_with_levels_intact():
    """F1 end-to-end regression: reviewer returns the SAME two choices,
    REORDERED, with valid "level" tags already attached to their own text.

    Before the fix, the positional backstop overwrote the reviewed (correct)
    levels by index — the fabricated "direct" claim inherited "denial" (and
    became grounding-exempt, shown as a clickable chip) while the honest
    denial inherited "direct" (and was deleted for failing the term-evidence
    check on "Kubernetes", which the zero-evidence profile cannot ground).
    """
    class _ReorderingReviewer(LLMProvider):
        async def acomplete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096, disable_thinking=None):
            return ""

        async def aparse_json(self, prompt, *, system=None, temperature=0.1, max_tokens=4096, disable_thinking=None):
            sys_lower = (system or "").lower()
            if "language reviewer" in sys_lower:
                return {"approved": False, "issues": ["reorder"], "feedback": "Lead with the denial."}
            if "rewrite" in sys_lower:
                # Refinement: REORDERS the two choices, keeps levels intact
                # and attached to their own (correct) text.
                return {
                    "question": "Have you worked with Kubernetes?",
                    "choices": [
                        {"text": "No, I have never worked with Kubernetes.", "level": "denial"},
                        {"text": "Yes, hands-on Kubernetes experience.", "level": "direct"},
                    ],
                }
            return {
                "question": "Have you worked with Kubernetes?",
                "choices": [
                    {"text": "Yes, hands-on Kubernetes experience.", "level": "direct"},
                    {"text": "No, I have never worked with Kubernetes.", "level": "denial"},
                ],
            }

    out = await question_generator_with_profile(
        _mode_a_state(), {}, _ReorderingReviewer(), lang="en",
    )

    assert out["choices"] is not None
    assert "No, I have never worked with Kubernetes." in out["choices"]
    assert "Yes, hands-on Kubernetes experience." not in out["choices"]


@pytest.mark.asyncio
async def test_mode_a_denial_choice_survives_a_reviewer_that_drops_the_level_field():
    """End-to-end: the refinement call rewrites wording and DROPS "level"
    from every choice. Without the backstop, the denial choice would fall
    back to the unrecognised-level full grounding check and be dropped for
    naming the (profile-unevidenced) cluster term "Kubernetes" — exactly the
    over-drop bug this branch fixed. With the backstop, its "denial" level
    is carried over by position and it survives (term-evidence exempt)."""
    class _LevelDroppingReviewer(LLMProvider):
        async def acomplete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096, disable_thinking=None):
            return ""

        async def aparse_json(self, prompt, *, system=None, temperature=0.1, max_tokens=4096, disable_thinking=None):
            sys_lower = (system or "").lower()
            if "language reviewer" in sys_lower:
                return {"approved": False, "issues": ["reword"], "feedback": "Tighten the wording."}
            if "rewrite" in sys_lower:
                # Refinement: rewrites wording, DROPS "level" from every choice.
                return {
                    "question": "Have you worked with Kubernetes?",
                    "choices": [
                        {"text": "I have hands-on Kubernetes experience."},
                        {"text": "I have never worked with Kubernetes."},
                    ],
                }
            return {
                "question": "Have you worked with Kubernetes?",
                "choices": [
                    {"text": "Yes, hands-on Kubernetes experience.", "level": "direct"},
                    {"text": "No, I have never worked with Kubernetes.", "level": "denial"},
                ],
            }

    out = await question_generator_with_profile(
        _mode_a_state(), {}, _LevelDroppingReviewer(), lang="en",
    )

    assert out["choices"] is not None
    assert "I have never worked with Kubernetes." in out["choices"]
    # The DIRECT choice is correctly dropped regardless (profile={} has zero
    # Kubernetes evidence) — proving the surviving denial choice isn't just
    # "everything kept", but specifically the level-exempt one.
    assert "I have hands-on Kubernetes experience." not in out["choices"]


@pytest.mark.asyncio
async def test_e2e_reject_then_regenerate_through_question_generator_with_profile():
    """Full round-trip: generation → reviewer rejects → refinement → corrected question returned.

    Call order within aparse_json:
      1. Generation call:   system contains "expert career coach" (QUESTION_SYSTEM_PROMPT)
      2. Reviewer call:     system contains "language reviewer"
      3. Refinement call:   system contains "rewrite" (QUESTION_LANGUAGE_REFINEMENT_PROMPT)

    The three fingerprints are mutually exclusive across the real prompt constants,
    so call-order tracking is only used as a guard; substring matching drives routing.
    """

    class _RejectOnFirstReview(LLMProvider):
        def __init__(self):
            super().__init__()
            self._reviewer_calls = 0

        async def acomplete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096, disable_thinking=None):
            return ""

        async def aparse_json(self, prompt, *, system=None, temperature=0.1, max_tokens=4096, disable_thinking=None):
            sys_lower = (system or "").lower()
            if "language reviewer" in sys_lower:
                # First reviewer call → reject; any subsequent call → approve
                self._reviewer_calls += 1
                if self._reviewer_calls == 1:
                    return {
                        "approved": False,
                        "issues": ["wrong language"],
                        "feedback": "Rewrite in English.",
                    }
                return {"approved": True, "issues": [], "feedback": ""}
            if "rewrite" in sys_lower:
                # Refinement call
                return {"question": "Corrected?", "choices": None}
            # Generation call (expert career coach system prompt)
            return {"question": "Ursprungsfrage?", "choices": None}

    out = await question_generator_with_profile(_mode_a_state(), {}, _RejectOnFirstReview(), lang="en")
    assert out["question"] == "Corrected?"
