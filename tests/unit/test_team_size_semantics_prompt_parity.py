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

"""#562 — `team_size` was extracted as company headcount (480), bed count (28) or
mentee count (2) in 3 of 4 ``panel_review_case`` runs; only ``operations_marcus_de``
(38 people led in a Dreischichtbetrieb) was right. Triage (applire-prompt-first):
category B — the schema carried the field ("Integer team size or null") and the
QUANTIFIED ROLE FACTS rule required a stated figure to stay in the bullet's prose,
but no rule anywhere defined what `team_size` COUNTS. The model was never told the
difference between "people the candidate led" and any other quantity sitting near a
headcount word in the same sentence.

Positive set of prompts that can EMIT `team_size` into the vault (``grep -rn
team_size backend/applire/prompts/``): the single-call extraction prompt
(``cv_extraction.py``), the segmented extraction's OUTLINE pass (the only segmented
call that populates team_size — DETAIL never returns the field, CORE excludes
work_experience entirely), and the reconciler (``reconcile.py``) — EVERY CV import
folds the extracted MasterProfileData through one `reconcile()` call
(services/profile/reconcile/import_bridge.py, see
``tests/unit/test_role_facts_prompt_parity.py``'s docstring), so the reconciler gets
a second, independent look at the same figures.

`interview.py`'s field-gap question ("Ask how many people reported to them in THIS
role") already had correct semantics and needed no change — it is not an emitter of
the typed field, only a question. `gap_analysis.py` / `job_analysis.py` /
`scope_requirements.py` are CONSUMERS of an already-typed `team_size` value (the
sufficiency judge, the JD-side scope bar) and correctly state the field's INTENDED
semantics to the judge — they cannot fix a value that was mis-populated upstream.

ADR-066 (one rule, N copies only where the prompts are separate documents): the same
TEAM_SIZE SEMANTICS definition is added to all three emitters, wording kept as close
to identical as each prompt's local register allows (the OUTLINE pass has no bullets
of its own, so it points to the DETAIL pass's bullet instead of "the bullet text";
the reconciler's numbered-rule indentation re-wraps the same sentences at different
column widths than the two "- " bulleted prompts). This test guards against the
dual/triple-prompt trap named in #328/#407: a fix that lands on one emitter but not
its siblings.

Multi-word needles are matched against WHITESPACE-NORMALISED prompt text
(``_flatten``): the three prompts wrap the identical sentence at different columns
(bullet vs. numbered-rule indentation), so a raw substring check is brittle against
cosmetic re-wrapping that changes no meaning — exactly the kind of false failure a
prompt editor should not have to fight.
"""
import re
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

_MARKER = "TEAM_SIZE SEMANTICS"
# A second, more specific marker so the pin cannot be satisfied by a bare section
# title with the actual rule content deleted underneath it.
_CONTENT_MARKER = "PERSONALLY led or managed"
_POSITIVE_EXAMPLE = "mit 38 Mitarbeitenden im Dreischichtbetrieb"


def _flatten(text: str) -> str:
    """Collapse all whitespace runs (including line wraps) to a single space, so
    a multi-word needle matches regardless of where the source file wrapped it."""
    return re.sub(r"\s+", " ", text)


def test_single_call_extraction_prompt_defines_team_size():
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT

    flat = _flatten(GENERIC_CV_EXTRACTION_PROMPT)
    assert _MARKER in flat, (
        "cv_extraction.py must define what team_size counts — the field's "
        "presence in the schema is not a definition (#562 category B)."
    )
    assert _CONTENT_MARKER in flat
    # JD_AWARE shares the same _SYSTEM_BASE — must inherit the rule too.
    from applire.prompts.cv_extraction import JD_AWARE_CV_EXTRACTION_PROMPT
    assert _MARKER in JD_AWARE_CV_EXTRACTION_PROMPT


def test_segmented_outline_prompt_defines_team_size():
    """The OUTLINE prompt is the ONLY segmented call that actually populates
    team_size (DETAIL's own return schema has no team_size key; CORE excludes
    work_experience) — the definition must live here, not on a sibling pass that
    never emits the field."""
    from applire.prompts.cv_extraction_segmented import EXTRACTION_OUTLINE_SYSTEM_PROMPT

    flat = _flatten(EXTRACTION_OUTLINE_SYSTEM_PROMPT)
    assert _MARKER in flat
    assert _CONTENT_MARKER in flat


def test_segmented_detail_prompt_does_not_need_the_definition():
    """DETAIL never returns team_size (only responsibilities/achievements/
    technologies) — asserting the definition's ABSENCE here pins that the emitter
    set is exactly {single-call, OUTLINE, reconcile}, not a fourth accidental one."""
    from applire.prompts.cv_extraction_segmented import EXTRACTION_DETAIL_SYSTEM_PROMPT

    assert _MARKER not in EXTRACTION_DETAIL_SYSTEM_PROMPT


def test_reconcile_prompt_defines_team_size():
    """The reconciler (ADR-046) is on the critical path for EVERY CV import
    (services/profile/reconcile/import_bridge.py folds the whole extracted
    MasterProfileData through one reconcile() call) as well as every interview
    testimony that upserts a work entry — it must not re-derive team_size from a
    headcount/capacity/mentee figure any more than the extraction prompts may."""
    from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT

    flat = _flatten(RECONCILE_SYSTEM_PROMPT)
    assert _MARKER in flat
    assert _CONTENT_MARKER in flat
    # The reconciler prompt must name the concrete failure shape, not just an
    # abstract cross-reference a retry can silently ignore.
    assert "480 Mitarbeitenden" in flat


def test_all_three_emitters_state_the_same_positive_example():
    """ADR-066 — keep the wording identical where the prompts are separate
    documents. Pin the one figure a correct extraction MUST still allow
    (operations_marcus_de, 38 people led in a Dreischichtbetrieb) so a future
    edit cannot tighten the rule into rejecting the one case that already works."""
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT
    from applire.prompts.cv_extraction_segmented import EXTRACTION_OUTLINE_SYSTEM_PROMPT
    from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT

    assert _POSITIVE_EXAMPLE in _flatten(GENERIC_CV_EXTRACTION_PROMPT)
    assert _POSITIVE_EXAMPLE in _flatten(EXTRACTION_OUTLINE_SYSTEM_PROMPT)
    assert _POSITIVE_EXAMPLE in _flatten(RECONCILE_SYSTEM_PROMPT)


def test_all_three_emitters_exclude_the_three_negative_examples():
    """The issue's other three panel_review_case failures — company headcount,
    facility capacity, mentee count — must all be named as explicit exclusions in
    every emitter, not just the one that happened to be easiest to quote."""
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT
    from applire.prompts.cv_extraction_segmented import EXTRACTION_OUTLINE_SYSTEM_PROMPT
    from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT

    for prompt in (
        GENERIC_CV_EXTRACTION_PROMPT,
        EXTRACTION_OUTLINE_SYSTEM_PROMPT,
        RECONCILE_SYSTEM_PROMPT,
    ):
        flat = _flatten(prompt)
        assert "480 Mitarbeitenden" in flat  # controlling_emma_de — employer headcount
        assert "28-bed ward" in flat  # nursing_priya_relocator — facility capacity
        assert "Mentor two mid-level engineers" in flat  # it_backend_daniel — mentees
