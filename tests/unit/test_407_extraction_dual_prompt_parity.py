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

"""#407 — dual-prompt parity for the two vault-write fixes.

There are TWO extraction prompts (``cv_extraction.py`` for the single-call path,
``cv_extraction_segmented.py`` for the outline+detail path, ADR-047) that must
independently ask for the same thing or the segmented path silently keeps the old,
defective behaviour while the single-call path is fixed. This exact dual-prompt
shape has caused prior defects (#190, #229, #328) where a fix landed on only one
of the two — this test guards specifically against that regression for #407's two
sub-fixes, pinned against the run-12 (panel_review_case/operations_marcus_de)
real-provider evidence:

  item 1 — a German self-declaration word ("Anwender") must map through the same
           word-scale table the prompt already teaches for English/LinkedIn words,
           not be left to the model's own (ungrounded) translation.
  item 2 — a work_experience/project/volunteer entry's "technologies" list must be
           grounded in THAT entry's own text, never backfilled from a separate
           skills/"Kenntnisse" section or a different entry.

This test does NOT claim a keyword in a prompt proves anything reaches the page —
the real-provider proof is the before/after replay recorded in this issue's commit
message (Weberit's technologies: ["SAP"] -> [], SAP proficiency: "intermediate" ->
"basic", both on the verbatim captured run-12 prompt). This test guards the
narrower, cheaper regression: an edit that updates one prompt but not its sibling.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

_GERMAN_PROFICIENCY_WORD = "anwender"
_GROUNDING_MARKER = "PER-ENTRY GROUNDING"


def test_single_call_prompt_teaches_german_proficiency_words():
    from applire.prompts.cv_extraction import (
        GENERIC_CV_EXTRACTION_PROMPT,
        JD_AWARE_CV_EXTRACTION_PROMPT,
    )

    for prompt in (GENERIC_CV_EXTRACTION_PROMPT, JD_AWARE_CV_EXTRACTION_PROMPT):
        assert _GERMAN_PROFICIENCY_WORD in prompt.lower(), (
            "cv_extraction.py must teach the German self-declaration word "
            "'Anwender' in its PROFICIENCY SCALE rule."
        )


def test_segmented_core_prompt_teaches_german_proficiency_words():
    """The segmented path's core-schema SYSTEM prompt is the one that actually
    constrains ``proficiency`` (US195/ADR-047) — it must carry the same word,
    or a CV extracted through the segmented fallback path keeps the #304/#317
    defect even though the single-call path is fixed."""
    from applire.prompts.cv_extraction_segmented import EXTRACTION_CORE_SYSTEM_PROMPT

    assert _GERMAN_PROFICIENCY_WORD in EXTRACTION_CORE_SYSTEM_PROMPT.lower(), (
        "cv_extraction_segmented.py's EXTRACTION_CORE_SYSTEM_PROMPT must teach "
        "the German self-declaration word 'Anwender' in its PROFICIENCY rule — "
        "this is the dual-prompt trap named in #190/#229/#328."
    )


def test_single_call_prompt_forbids_technologies_backfill():
    from applire.prompts.cv_extraction import (
        GENERIC_CV_EXTRACTION_PROMPT,
        JD_AWARE_CV_EXTRACTION_PROMPT,
    )

    for prompt in (GENERIC_CV_EXTRACTION_PROMPT, JD_AWARE_CV_EXTRACTION_PROMPT):
        assert _GROUNDING_MARKER in prompt, (
            "cv_extraction.py must instruct the model not to backfill an "
            "entry's technologies list from a separate skills/Kenntnisse "
            "section or a different entry."
        )


def test_segmented_detail_prompt_forbids_technologies_backfill():
    """The segmented DETAIL prompt is the ONLY segmented call that writes
    ``technologies`` per position (EXTRACTION_DETAIL_SYSTEM_PROMPT) — the
    enforcement point must live here, mirroring the single-call prompt."""
    from applire.prompts.cv_extraction_segmented import EXTRACTION_DETAIL_SYSTEM_PROMPT

    assert _GROUNDING_MARKER in EXTRACTION_DETAIL_SYSTEM_PROMPT, (
        "cv_extraction_segmented.py's DETAIL prompt must carry the same "
        "per-entry technologies grounding rule as the single-call prompt — "
        "this is the dual-prompt trap named in #190/#229/#328."
    )
