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
translate CV content must ALSO protect a domain acronym / named-system token riding
inside a label from being expanded into its full words.

E049/ADR-067 (2026-07-30): the writer prompts (cv_tailoring.SYSTEM_PROMPT,
cv_segmented._CORE_RULES) replaced their old VERBATIM LABELS acronym enumeration
with rule 8's single "does it NAME something, or DESCRIBE something?" test -- the
old list and the "skill PHRASES MUST be translated" rule it contradicted are both
gone (one live contradiction removed, ADR-067 clause 8). The reviewer/refiner
prompts in review_cv_language.py were NOT part of that rewrite and still carry the
full VERBATIM LABELS wording and acronym enumeration.

These tests pin the NEW, two-shaped invariant instead of resurrecting the old list:
every translation-bearing prompt must still express the GxP-class acronym-protection
intent, via EITHER the writer's name-vs-describe test OR the reviewer's VERBATIM
LABELS rule -- and each mechanism is pinned in its own current wording, not the old
one.

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

# Prompts still carrying the full old-style VERBATIM LABELS acronym enumeration.
# review_cv_language.py was not touched by the ADR-067 prose-craft rewrite.
_VERBATIM_LABEL_PROMPTS = {
    "review_cv_language.CV_LANGUAGE_REVIEW_SYSTEM_PROMPT": CV_LANGUAGE_REVIEW_SYSTEM_PROMPT,
    "review_cv_language.CV_LANGUAGE_REFINEMENT_PROMPT": CV_LANGUAGE_REFINEMENT_PROMPT,
}

# Prompts carrying rule 8's replacement name-vs-describe test.
_NAME_VS_DESCRIBE_PROMPTS = {
    "cv_tailoring.SYSTEM_PROMPT": SYSTEM_PROMPT,
    "cv_segmented._CORE_RULES": _CORE_RULES,
}

# The exact pinned regression, so a future prompt edit can't silently reintroduce it.
_GXP_EXAMPLE = "GxP"
_NAME_VS_DESCRIBE_TEST = "name something, or describe something"


def test_every_translation_prompt_names_the_pinned_gxp_example():
    """GxP is the pinned regression from run #6: whichever mechanism a prompt uses,
    it must still show GxP as a name that is never expanded into its full words."""
    for name, prompt in _PROMPTS.items():
        assert _GXP_EXAMPLE in prompt, f"{name} is missing the pinned GxP example"


def test_every_translation_prompt_carries_one_of_the_two_acronym_protections():
    """E049/ADR-067: the writer prompts no longer carry a VERBATIM LABELS acronym
    list -- rule 8 replaced it with a single name-vs-describe test that covers the
    same ground without contradicting "skill phrases must be translated". Every
    translation-bearing prompt must carry ONE of the two mechanisms; neither is
    optional and there is no third way to express the intent."""
    for name, prompt in _PROMPTS.items():
        lowered = prompt.lower()
        has_verbatim_labels = "verbatim label" in lowered
        has_name_vs_describe = _NAME_VS_DESCRIBE_TEST in lowered
        assert has_verbatim_labels or has_name_vs_describe, (
            f"{name} carries neither the VERBATIM LABELS rule nor the "
            "name-vs-describe test"
        )


def test_verbatim_label_prompts_name_the_full_domain_acronym_set():
    """Only the reviewer/refiner prompts still enumerate the acronym set literally.
    The writer prompts (name-vs-describe test) illustrate with GxP plus "one you do
    not recognise" by design -- the test is deliberately general, not a list to keep
    in sync (see test_name_vs_describe_test_covers_an_unrecognised_acronym below)."""
    acronyms = ["GxP", "GMP", "ALCOA+", "CSV", "LIMS", "MES", "ITIL"]
    for name, prompt in _VERBATIM_LABEL_PROMPTS.items():
        for acronym in acronyms:
            assert acronym in prompt, f"{name} is missing domain acronym {acronym!r}"


def test_verbatim_label_rule_covers_the_named_label_categories():
    categories = ["skill", "certification", "employer", "job title", "system"]
    for name, prompt in _VERBATIM_LABEL_PROMPTS.items():
        lowered = prompt.lower()
        for cat in categories:
            assert cat in lowered, f"{name} does not mention label category {cat!r}"


def test_name_vs_describe_test_covers_an_unrecognised_acronym():
    """Rule 8's whole point is generality -- it must not silently degrade into
    "GxP and only GxP": it explicitly extends to an acronym the model does not
    recognise, which is how it protects the acronym set without enumerating it."""
    for name, prompt in _NAME_VS_DESCRIBE_PROMPTS.items():
        assert "do not recognise" in prompt.lower(), (
            f"{name} lost the 'one you do not recognise' generality clause"
        )


def test_prose_translation_requirement_is_not_weakened():
    """ADR-038 must still hold: prose (summary/bullets) is translated into the output
    language. The verbatim carve-out is scoped to labels, never to sentences."""
    for name, prompt in _PROMPTS.items():
        assert "translat" in prompt.lower(), f"{name} lost the translation requirement"


def test_single_call_system_prompt_keeps_new_rule_numbering():
    """E049/ADR-067: the old rule 6 (ENTRY COUNT) is DELETED wholesale, not
    renumbered -- the work_history entry-count field it defended no longer exists
    in the narrowed prose-only response schema (contact/company/role/dates/
    education are now joined deterministically from the vault). This test pins the
    INVERTED premise: rule 6 is now CLAIM STRENGTH, and LANGUAGE (formerly rule 7)
    is now rule 8."""
    assert "6. CLAIM STRENGTH." in SYSTEM_PROMPT
    assert "8. LANGUAGE." in SYSTEM_PROMPT
    assert "work_history entries in your output must equal exactly" not in SYSTEM_PROMPT
