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

"""#277 (#270 Fix D inverted) — the vault-derived SCOPED BOUNDARIES block must reach
the CV tailoring prompts (single-call + segmented summary/skills), not just the
cover-letter prompt. String-level assertions only; no LLM.

``find_scoped_boundaries``/``render_scoped_boundary_block`` themselves are already
covered by ``backend/tests/unit/services/test_cross_document.py`` — these tests only
check that the CV-side prompt builders thread the rendered block through, and that an
absent block leaves the prompt byte-for-byte unchanged (no regression for the common
no-boundary case).
"""

from applire.prompts.cv_segmented import build_skills_prompt, build_summary_prompt
from applire.prompts.cv_tailoring import build_user_prompt
from applire.services.cross_document import find_scoped_boundaries, render_scoped_boundary_block

_JOB = {"role_title": "ML Engineer", "required_skills": [], "keywords": []}
_PROFILE = {"work_experience": []}
_DIRECTIVE = {"summary_angle": "vector search", "skills_focus": []}

# Invented fixture data (never real personal data) — mirrors the fixture shape already
# used by backend/tests/unit/services/test_cross_document.py.
_LEDGER = [
    {
        "concept": "RAG pipelines",
        "claimable": True,
        "surface_forms": ["RAG pipelines", "RAG"],
        "evidence": "Built and owned the RAG pipeline data layer at Northwind Labs.",
    }
]
_DENIED_CONCEPTS = [
    {
        "concept": "hands-on embedding work",
        "statement": (
            "I designed the database for the RAG pipeline but did not configure the "
            "embedding models myself."
        ),
        "source": "interview",
    }
]

_BOUNDARIES = find_scoped_boundaries(_LEDGER, _DENIED_CONCEPTS)
_BLOCK = render_scoped_boundary_block(_BOUNDARIES)


def test_scoped_boundaries_fixture_actually_produces_a_block():
    # Pins the fixture itself — if this ever goes empty, every test below is vacuous.
    assert _BOUNDARIES, "fixture must yield a scoped boundary"
    assert _BLOCK
    assert "RAG pipelines" in _BLOCK
    assert "embedding models" in _BLOCK


def test_build_user_prompt_carries_the_scoped_boundary_block():
    prompt = build_user_prompt(_JOB, _PROFILE, [], [], scoped_boundary_block=_BLOCK)
    assert "SCOPED BOUNDARIES" in prompt
    assert "RAG pipelines" in prompt
    assert "embedding models" in prompt


def test_build_user_prompt_without_scoped_boundary_is_byte_identical_to_baseline():
    baseline = build_user_prompt(_JOB, _PROFILE, [], [])
    with_none = build_user_prompt(_JOB, _PROFILE, [], [], scoped_boundary_block=None)
    with_empty = build_user_prompt(_JOB, _PROFILE, [], [], scoped_boundary_block="")
    assert with_none == baseline
    assert with_empty == baseline
    assert "SCOPED BOUNDARIES" not in baseline


def test_build_summary_prompt_carries_the_scoped_boundary_block():
    prompt = build_summary_prompt(
        _DIRECTIVE, _JOB, _PROFILE, [], "en", scoped_boundary_block=_BLOCK
    )
    assert "SCOPED BOUNDARIES" in prompt
    assert "RAG pipelines" in prompt


def test_build_summary_prompt_without_scoped_boundary_is_byte_identical_to_baseline():
    baseline = build_summary_prompt(_DIRECTIVE, _JOB, _PROFILE, [], "en")
    with_none = build_summary_prompt(
        _DIRECTIVE, _JOB, _PROFILE, [], "en", scoped_boundary_block=None
    )
    assert with_none == baseline
    assert "SCOPED BOUNDARIES" not in baseline


def test_build_skills_prompt_carries_the_scoped_boundary_block():
    prompt = build_skills_prompt(
        _DIRECTIVE, _JOB, _PROFILE, [], "en", scoped_boundary_block=_BLOCK
    )
    assert "SCOPED BOUNDARIES" in prompt
    assert "RAG pipelines" in prompt


def test_build_skills_prompt_without_scoped_boundary_is_byte_identical_to_baseline():
    baseline = build_skills_prompt(_DIRECTIVE, _JOB, _PROFILE, [], "en")
    with_none = build_skills_prompt(
        _DIRECTIVE, _JOB, _PROFILE, [], "en", scoped_boundary_block=None
    )
    assert with_none == baseline
    assert "SCOPED BOUNDARIES" not in baseline


def test_scoped_boundary_instruction_never_deletes_the_concept():
    # ADR-059 / guardrail: the remedy must always be a MORE precise claim, never a
    # deletion of the concept or a softened denial.
    assert "never a bare denial that discards" in _BLOCK
    assert "never" in _BLOCK.lower() and "unqualified" in _BLOCK.lower()


def test_denied_concept_with_no_matching_claimable_ledger_entry_never_becomes_a_boundary():
    """ADR-059: a denied (non-claimable) concept must never surface as a claim — here,
    verified at the boundary-computation step the CV prompt threading depends on."""
    ledger = [
        {
            "concept": "RAG pipelines",
            "claimable": False,  # honest gap, not claimable
            "surface_forms": ["RAG pipelines"],
            "evidence": "",
        }
    ]
    boundaries = find_scoped_boundaries(ledger, _DENIED_CONCEPTS)
    assert boundaries == []
    assert render_scoped_boundary_block(boundaries) == ""
