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

"""Tiramisu wave-6 follow-up (blind hiring-panel run #6) -- pins the prompt-wording
half of the fix: every place the ADR-038 language machinery instructs a model to
translate CV content must ALSO tell it that a domain acronym / named-system token
riding inside a label is copied verbatim, never expanded into its full words.

String-level assertions only; no LLM (mirrors ``test_cv_budget_prompts.py``). The
deterministic guard in ``services/cv.py`` (``_restore_skill_spelling``) is the real
protection -- these tests only pin that the wording carries the rule, since a
regression here would widen how often the guard has to intervene.
"""

from applire.prompts.cv_segmented import _CORE_RULES
from applire.prompts.cv_tailoring import SYSTEM_PROMPT
from applire.prompts.review_cv_language import (
    CV_LANGUAGE_REFINEMENT_PROMPT,
    CV_LANGUAGE_REVIEW_SYSTEM_PROMPT,
)

_PROMPTS = {
    "cv_tailoring.SYSTEM_PROMPT": SYSTEM_PROMPT,
    "cv_segmented._CORE_RULES": _CORE_RULES,
    "review_cv_language.CV_LANGUAGE_REVIEW_SYSTEM_PROMPT": CV_LANGUAGE_REVIEW_SYSTEM_PROMPT,
    "review_cv_language.CV_LANGUAGE_REFINEMENT_PROMPT": CV_LANGUAGE_REFINEMENT_PROMPT,
}

# The exact pinned regression, so a future prompt edit can't silently reintroduce it.
_GXP_EXAMPLE = "GxP"


def test_every_translation_prompt_names_the_verbatim_acronym_rule():
    for name, prompt in _PROMPTS.items():
        assert "verbatim" in prompt.lower(), f"{name} is missing the verbatim carve-out"
        assert _GXP_EXAMPLE in prompt, f"{name} is missing the pinned GxP example"


def test_every_translation_prompt_names_the_full_domain_acronym_set():
    acronyms = ["GxP", "GMP", "ALCOA+", "CSV", "LIMS", "MES", "ITIL"]
    for name, prompt in _PROMPTS.items():
        for acronym in acronyms:
            assert acronym in prompt, f"{name} is missing domain acronym {acronym!r}"


def test_verbatim_rule_covers_the_named_label_categories():
    categories = ["skill", "certification", "employer", "job title", "system"]
    for name, prompt in _PROMPTS.items():
        lowered = prompt.lower()
        for cat in categories:
            assert cat in lowered, f"{name} does not mention label category {cat!r}"


def test_prose_translation_requirement_is_not_weakened():
    """ADR-038 must still hold: prose (summary/bullets) is translated into the output
    language. The verbatim carve-out is scoped to labels, never to sentences."""
    assert "translat" in SYSTEM_PROMPT.lower()
    assert "translat" in _CORE_RULES.lower()
    assert "translat" in CV_LANGUAGE_REFINEMENT_PROMPT.lower()
    assert "translat" in CV_LANGUAGE_REVIEW_SYSTEM_PROMPT.lower()


def test_single_call_system_prompt_keeps_existing_rule_numbering():
    # Rule 6 (entry-count contract) is load-bearing -- must not be renumbered by
    # this edit (mirrors test_cv_budget_prompts.py's own pin).
    assert "6. The number of work_history entries in your output must equal exactly" in SYSTEM_PROMPT
    assert "7. Output language:" in SYSTEM_PROMPT
