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

"""#328 Part A — the extraction/reconcile prompts must instruct the model to
keep a stated figure (budget amount, team size, industry) in the candidate's
OWN responsibility/achievement wording, in addition to lifting it into the
typed ``team_size`` / ``budget_managed`` / ``industry_context`` field. The
typed field is a DERIVED PROJECTION, never the only home of the number.

There are TWO extraction prompts (``cv_extraction.py`` for the single-call
path, ``cv_extraction_segmented.py`` for the outline+detail path) plus the
``reconcile.py`` reconciler prompt every extracted profile is folded through
(``services/profile/reconcile/import_bridge.py``). This exact dual-prompt
shape has caused two prior defects (#190 certifications, #229 achievements)
where a fix landed on only one of the two extraction prompts. This test does
NOT claim a keyword in a prompt proves anything reaches the page (#328's own
complaint about three prior no-op fixes) -- the page-level proof lives in
``tests/unit/test_cv_role_facts_integration.py``. This test guards
specifically against the dual-prompt-trap regression: an edit that updates
one prompt (or the reconciler) but not its sibling(s).
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

_MARKER = "QUANTIFIED ROLE FACT"


def test_single_call_extraction_prompt_keeps_the_figure_in_prose():
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT

    assert _MARKER in GENERIC_CV_EXTRACTION_PROMPT, (
        "cv_extraction.py must instruct the model to keep a stated "
        "team_size/budget_managed/industry_context figure in the "
        "responsibilities/achievements bullet that states it, not only in "
        "the typed field."
    )
    # JD_AWARE shares the same _SYSTEM_BASE — must inherit the rule too.
    from applire.prompts.cv_extraction import JD_AWARE_CV_EXTRACTION_PROMPT
    assert _MARKER in JD_AWARE_CV_EXTRACTION_PROMPT


def test_segmented_detail_prompt_keeps_the_figure_in_prose():
    """The segmented DETAIL prompt is the ONLY segmented call that writes
    responsibilities/achievements bullets — the OUTLINE prompt never sees
    them. The enforcement point must live here."""
    from applire.prompts.cv_extraction_segmented import EXTRACTION_DETAIL_SYSTEM_PROMPT

    assert _MARKER in EXTRACTION_DETAIL_SYSTEM_PROMPT, (
        "cv_extraction_segmented.py's DETAIL prompt (the one that writes "
        "responsibilities/achievements) must carry the same rule as the "
        "single-call prompt — this is the dual-prompt trap named in #328."
    )


def test_segmented_outline_prompt_flags_the_fields_as_derived_projections():
    """The OUTLINE prompt is where team_size/budget_managed/industry_context
    are actually populated in the segmented path — it must not read as the
    only place the figure belongs, even though it never writes bullets
    itself."""
    from applire.prompts.cv_extraction_segmented import EXTRACTION_OUTLINE_SYSTEM_PROMPT

    assert _MARKER in EXTRACTION_OUTLINE_SYSTEM_PROMPT


def test_reconcile_prompt_does_not_let_the_reconciler_strip_the_figure():
    """The reconciler (ADR-046) is on the critical path for EVERY CV import
    (services/profile/reconcile/import_bridge.py folds the whole extracted
    MasterProfileData through one reconcile() call) — it must not shorten an
    add_bullets bullet to a bare label merely because the same figure is also
    being lifted into team_size/budget_managed/industry_context via
    upsert_work/set_field."""
    from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT

    assert _MARKER in RECONCILE_SYSTEM_PROMPT
    # The reconciler prompt must name the concrete failure shape (a bare
    # label with the figure dropped) so the rule isn't just an abstract
    # cross-reference a retry can silently ignore.
    assert "Budgetverantwortung" in RECONCILE_SYSTEM_PROMPT
