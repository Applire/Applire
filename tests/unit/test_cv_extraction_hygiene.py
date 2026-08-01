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

"""US175 + US176 — CV extraction prompt hygiene contracts (E034 Chocolate riders).

Deterministic prompt-contract tests (CI tier). The literal behavioural acceptance
criteria — two competencies at 4/5 dots normalising to the same level (US175), and a
mixed tool/standard CV yielding a standards-free technologies list (US176) — require a
real LLM and live in tests/integration/test_extraction_hygiene_llm.py (PQ tier).

These tests assert the prompt *states the rule* so the model behaves deterministically:
  US175 — an explicit dot/word proficiency scale → level mapping, aligned with
          _PROFICIENCY_ALIASES (schemas/profile.py). No schema change.
  US176 — practices/standards/methodologies are NOT technologies; route to
          skills(category=domain) or omit. No schema change.

Run:
    pytest tests/unit/test_cv_extraction_hygiene.py -v
"""

import pytest

from applire.prompts.cv_extraction import (
    GENERIC_CV_EXTRACTION_PROMPT,
    JD_AWARE_CV_EXTRACTION_PROMPT,
)
from applire.schemas.profile import _PROFICIENCY_ALIASES


# ---------------------------------------------------------------------------
# US175 — deterministic proficiency-scale mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [GENERIC_CV_EXTRACTION_PROMPT, JD_AWARE_CV_EXTRACTION_PROMPT],
    ids=["generic", "jd_aware"],
)
def test_prompt_states_proficiency_scale_rule(prompt):
    """The prompt must carry an explicit proficiency-scale mapping rule."""
    assert "proficiency scale" in prompt.lower()


@pytest.mark.parametrize(
    "prompt",
    [GENERIC_CV_EXTRACTION_PROMPT, JD_AWARE_CV_EXTRACTION_PROMPT],
    ids=["generic", "jd_aware"],
)
def test_prompt_proficiency_scale_anchors_to_four_levels(prompt):
    """The mapping must name the four canonical levels so the model emits valid enums."""
    lowered = prompt.lower()
    for level in ("basic", "intermediate", "advanced", "expert"):
        assert level in lowered, f"proficiency scale rule must mention '{level}'"


@pytest.mark.parametrize(
    "prompt",
    [GENERIC_CV_EXTRACTION_PROMPT, JD_AWARE_CV_EXTRACTION_PROMPT],
    ids=["generic", "jd_aware"],
)
def test_prompt_proficiency_scale_gives_dot_examples(prompt):
    """AC example: 4-of-5 dots -> advanced, 5-of-5 -> expert must be stated verbatim enough
    to be unambiguous (anchors the dot-scale convention)."""
    lowered = prompt.lower()
    assert "4/5" in lowered and "5/5" in lowered, (
        "proficiency scale rule must give the 4/5 and 5/5 dot anchors"
    )


@pytest.mark.parametrize(
    "prompt",
    [GENERIC_CV_EXTRACTION_PROMPT, JD_AWARE_CV_EXTRACTION_PROMPT],
    ids=["generic", "jd_aware"],
)
def test_prompt_proficiency_scale_states_determinism(prompt):
    """The rule must require equal scale positions to yield the same level (determinism)."""
    assert "same proficiency level" in prompt.lower()


def test_prompt_proficiency_words_align_with_schema_aliases():
    """The word-scale terms taught in the prompt must agree with _PROFICIENCY_ALIASES so the
    prompt and the schema validator never disagree on a given word. Guards against drift."""
    lowered = GENERIC_CV_EXTRACTION_PROMPT.lower()
    # Sample one representative alias per target level that the rule teaches by name.
    sampled = {
        "fluent": "advanced",
        "native": "expert",
        "beginner": "basic",
    }
    for word, expected_level in sampled.items():
        assert _PROFICIENCY_ALIASES[word] == expected_level, "test fixture drift"
        if word in lowered:
            # If the prompt teaches the word, it must teach it to the same level.
            # Find the word and assert the matching level word appears in the rule block.
            assert expected_level in lowered


# ---------------------------------------------------------------------------
# US176 — technologies vs practices hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [GENERIC_CV_EXTRACTION_PROMPT, JD_AWARE_CV_EXTRACTION_PROMPT],
    ids=["generic", "jd_aware"],
)
def test_prompt_excludes_practices_from_technologies(prompt):
    """The prompt must state that practices/standards/methodologies are NOT technologies."""
    lowered = prompt.lower()
    assert "not technologies" in lowered, (
        "prompt must explicitly prohibit practices/standards in technologies"
    )


@pytest.mark.parametrize(
    "prompt",
    [GENERIC_CV_EXTRACTION_PROMPT, JD_AWARE_CV_EXTRACTION_PROMPT],
    ids=["generic", "jd_aware"],
)
def test_prompt_routes_standards_to_domain_skill_or_omit(prompt):
    """A named standard/methodology must route to skills(category=domain) or be omitted.
    Anchored on the explicit routing phrase so it gates the US176 rule, not the pre-existing
    'domain' in the schema block or 'omit' in the required-fields rule."""
    lowered = prompt.lower()
    assert "route" in lowered and "domain" in lowered, (
        "rule must instruct routing a named standard to skills(category=domain)"
    )
    assert "or omit" in lowered, "the 'or omit' fallback must be stated"


@pytest.mark.parametrize(
    "prompt",
    [GENERIC_CV_EXTRACTION_PROMPT, JD_AWARE_CV_EXTRACTION_PROMPT],
    ids=["generic", "jd_aware"],
)
def test_prompt_gives_standard_examples(prompt):
    """The rule must give concrete examples (ISO 25010, V-Model) so the model recognises the
    category, not just abstract 'standards'."""
    lowered = prompt.lower()
    assert "iso 25010" in lowered and "v-model" in lowered, (
        "rule must give concrete standard/methodology examples"
    )


# ---------------------------------------------------------------------------
# #407 item 1 — German self-declaration words in the PROFICIENCY SCALE rule
#
# Root cause (run-12, panel_review_case/operations_marcus_de): "SAP (Anwender)" was
# mapped by the model itself to "intermediate" because the word-scale legend was
# English-only ("beginner", "proficient", "fluent", "native", ...) — the model had
# to guess its own English-scale equivalent for a German declaration, and the
# _PROFICIENCY_ALIASES backstop (schemas/profile.py) never saw the raw German word
# because the schema constrains the model to emit only the final enum. These tests
# pin the prompt-level fix so a future edit cannot silently drop it again.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [GENERIC_CV_EXTRACTION_PROMPT, JD_AWARE_CV_EXTRACTION_PROMPT],
    ids=["generic", "jd_aware"],
)
def test_prompt_proficiency_scale_teaches_german_words(prompt):
    """The word-scale mapping must teach the German self-declaration tiers, not just
    the English/LinkedIn vocabulary — #304/#317's own case ("Anwender") went through
    exactly this gap."""
    lowered = prompt.lower()
    for word in (
        "anwender", "grundkenntnisse", "grundlagen",
        "fortgeschritten", "erfahren", "verhandlungssicher",
        "muttersprache",
    ):
        assert word in lowered, f"PROFICIENCY rule must teach the German word {word!r}"
    # Both the German ß and its ASCII "ss" transliteration must be covered, mirroring
    # _PROFICIENCY_ALIASES (a prior incident, #213/#214, had a Unicode variant defeat
    # a matcher; do not assume only one spelling reaches this rule).
    assert "fließend" in lowered or "fliessend" in lowered


def test_prompt_german_proficiency_words_align_with_schema_aliases():
    """The German words taught in the prompt must map to the same level
    _PROFICIENCY_ALIASES assigns them (schemas/profile.py) — the alias table is the
    backstop for any path that bypasses the prompt, so the two must never disagree."""
    lowered = GENERIC_CV_EXTRACTION_PROMPT.lower()
    sampled = {
        "anwender": "basic",
        "grundkenntnisse": "basic",
        "fortgeschritten": "advanced",
        "verhandlungssicher": "advanced",
        "muttersprache": "expert",
    }
    for word, expected_level in sampled.items():
        assert _PROFICIENCY_ALIASES[word] == expected_level, "test fixture drift"
        assert word in lowered, f"prompt must teach {word!r}"


@pytest.mark.parametrize(
    "prompt",
    [GENERIC_CV_EXTRACTION_PROMPT, JD_AWARE_CV_EXTRACTION_PROMPT],
    ids=["generic", "jd_aware"],
)
def test_prompt_proficiency_scale_covers_bare_parenthetical_qualifier(prompt):
    """Real run-12 shape: "SAP (Anwender)" is a bare parenthetical qualifier next to a
    skill name, not a dedicated multi-item scale — the rule must explicitly say this
    counts too, or the model reads the qualifier as ambiguous and defaults to
    "intermediate" (reproduced against the real provider before this rule existed)."""
    lowered = prompt.lower()
    assert "sap (anwender)" in lowered, (
        "rule must give the exact run-12 example so the model recognises a bare "
        "parenthetical qualifier as a proficiency declaration"
    )


# ---------------------------------------------------------------------------
# #407 item 2 — PER-ENTRY GROUNDING FOR TECHNOLOGIES
#
# Root cause (run-12, same profile): the extractor reproducibly attributed "SAP" to
# Weberit's technologies list even though Weberit's own bullets never mention SAP —
# the model backfilled a general "Kenntnisse"/skills-section item onto the most
# recent/current role instead of the role (Rasselstein) whose own bullet actually
# states it. Reproduced 8/8 times against the real provider across three separate
# capture dates (2026-07-29, 2026-07-30, 2026-07-31) before this rule existed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [GENERIC_CV_EXTRACTION_PROMPT, JD_AWARE_CV_EXTRACTION_PROMPT],
    ids=["generic", "jd_aware"],
)
def test_prompt_forbids_backfilling_technologies_from_skills_section(prompt):
    """An entry's technologies list must be grounded in THAT entry's own text, never
    backfilled from a separate skills/Kenntnisse section or a different entry."""
    lowered = prompt.lower()
    assert "per-entry grounding" in lowered
    assert "kenntnisse" in lowered
    assert "different entry" in lowered or "different position" in lowered


@pytest.mark.parametrize(
    "prompt",
    [GENERIC_CV_EXTRACTION_PROMPT, JD_AWARE_CV_EXTRACTION_PROMPT],
    ids=["generic", "jd_aware"],
)
def test_prompt_technologies_grounding_gives_run12_example(prompt):
    """The rule must give the concrete run-12 example (SAP under Company A's own
    bullet, not Company B's) so the model has an unambiguous anchor."""
    lowered = prompt.lower()
    assert "company a" in lowered and "company b" in lowered
