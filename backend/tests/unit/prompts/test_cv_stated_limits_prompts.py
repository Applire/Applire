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

"""The STATED LIMITS block must reach the CV tailoring prompts (single-call +
segmented summary/skills), not just the cover-letter prompt. String-level
assertions only; no LLM.

Replaces ``test_cv_scoped_boundary_prompts.py`` (charter run #8, 2026-07-28).
That block paired each vault denial with the claimable concept it supposedly
limited; the pairing was a text-overlap heuristic and it ran backwards on real
data, so the CV writer was handed four invented limits on the candidate's own
strongest evidence. ``collect_stated_limits`` / ``render_stated_limits_block``
are covered in ``backend/tests/unit/services/test_cross_document.py`` — these
tests only check that the CV-side prompt builders thread the rendered block
through, and that an absent block leaves the prompt byte-for-byte unchanged.
"""

from applire.prompts.cv_segmented import build_skills_prompt, build_summary_prompt
from applire.prompts.cv_tailoring import build_user_prompt
from applire.services.cross_document import collect_stated_limits, render_stated_limits_block

_JOB = {"role_title": "ML Engineer", "required_skills": [], "keywords": []}
_PROFILE = {"work_experience": []}
_DIRECTIVE = {"summary_angle": "vector search", "skills_focus": []}

# Invented fixture data (never real personal data). Deliberately shaped like a real
# honest denial: it names an adjacent STRENGTH ("designed the database") in the same
# breath as the limit. That shape is what defeated the deleted boundary matcher.
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

_LIMITS = collect_stated_limits(_DENIED_CONCEPTS)
_BLOCK = render_stated_limits_block(_LIMITS)


def test_stated_limits_fixture_actually_produces_a_block():
    # Pins the fixture itself — if this ever goes empty, every test below is vacuous.
    assert _LIMITS, "fixture must yield a stated limit"
    assert _BLOCK
    assert "embedding models" in _BLOCK


def test_build_user_prompt_carries_the_stated_limits_block():
    prompt = build_user_prompt(_JOB, _PROFILE, [], [], stated_limits_block=_BLOCK)
    assert "STATED LIMITS" in prompt
    assert "did not configure the embedding models myself" in prompt


def test_build_user_prompt_without_stated_limits_is_byte_identical_to_baseline():
    baseline = build_user_prompt(_JOB, _PROFILE, [], [])
    with_none = build_user_prompt(_JOB, _PROFILE, [], [], stated_limits_block=None)
    with_empty = build_user_prompt(_JOB, _PROFILE, [], [], stated_limits_block="")
    assert with_none == baseline
    assert with_empty == baseline
    assert "STATED LIMITS" not in baseline


def test_build_summary_prompt_carries_the_stated_limits_block():
    # E049/ADR-067: build_summary_prompt's critical_gaps parameter is gone (#383) —
    # the call is (directive, job_analysis, profile, output_language, ...), one
    # positional arg fewer than before.
    prompt = build_summary_prompt(_DIRECTIVE, _JOB, _PROFILE, "en", stated_limits_block=_BLOCK)
    assert "STATED LIMITS" in prompt
    assert "embedding models" in prompt


def test_build_summary_prompt_without_stated_limits_is_byte_identical_to_baseline():
    baseline = build_summary_prompt(_DIRECTIVE, _JOB, _PROFILE, "en")
    with_none = build_summary_prompt(_DIRECTIVE, _JOB, _PROFILE, "en", stated_limits_block=None)
    assert with_none == baseline
    assert "STATED LIMITS" not in baseline


def test_build_skills_prompt_carries_the_stated_limits_block():
    prompt = build_skills_prompt(_DIRECTIVE, _JOB, _PROFILE, [], "en", stated_limits_block=_BLOCK)
    assert "STATED LIMITS" in prompt
    assert "embedding models" in prompt


def test_build_skills_prompt_without_stated_limits_is_byte_identical_to_baseline():
    baseline = build_skills_prompt(_DIRECTIVE, _JOB, _PROFILE, [], "en")
    with_none = build_skills_prompt(_DIRECTIVE, _JOB, _PROFILE, [], "en", stated_limits_block=None)
    assert with_none == baseline
    assert "STATED LIMITS" not in baseline


def test_the_block_never_names_a_concept_as_limited():
    """The whole point of the replacement: the block states facts and one rule. It
    must not tell the CV writer that some specific concept is bounded — that
    judgement belongs to the model reading the sentence it is about to write.

    "RAG pipeline" appears INSIDE the quoted statement, which is correct and
    unavoidable. What must never come back is the machinery that turned such an
    overlap into an instruction.
    """
    assert "POSITIVE (candidate's own vault evidence)" not in _BLOCK
    assert "SCOPED BOUNDARIES" not in _BLOCK
    assert "render the SCOPED claim" not in _BLOCK
    assert "both halves" not in _BLOCK


def test_the_block_forbids_inventing_a_limit_the_candidate_never_stated():
    """The rule that replaces the matcher. Without it the writer generalises from
    "here are limits" to "qualify everything nearby" — the run-8 defect, where a
    letter disclaimed digitalisation experience the vault positively evidenced.
    """
    low = _BLOCK.lower()
    assert "only limits" in low
    assert "strength, not a limit" in low
    assert "invented limit" in low
