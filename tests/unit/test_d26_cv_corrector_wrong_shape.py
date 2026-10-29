# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-26 (Hazelnut build 5, r5 bench, D-20 arm run 2): the CV terminal corrector
answered with only ``{"analysis": "…"}``. The composer read that as "the writer
omitted every work entry". On the re-entry bound the EMPTY CV shipped: no summary,
no bullet, no skill, and the page-length row said *pass*.

A CV corrector draft without the prose shape is a failed correction. The loop
ships the last known-good draft, the same fate as malformed JSON, and the
letter's ``letter_shape_ok`` precedent applies. Synthetic data only.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cv import _cv_prose_shape_ok  # noqa: E402
from applire.services.reviewer import review_and_refine  # noqa: E402

#: The captured corrector reply's shape (the analysis text itself is not kept).
ANALYSIS_ONLY = {"analysis": "Need to remove the duplicated project bullet and keep the role bullet."}
GOOD = {
    "summary": "Systems engineer with ten years in laboratory software.",
    "work": [{"id": "w1", "bullets": ["Led the LIMS roll-out across three sites."], "projects": []}],
    "projects": [],
    "skills": ["Python", "LIMS"],
}


@pytest.mark.parametrize("shape", [
    ANALYSIS_ONLY, None, [], {"summary": "x", "work": [], "skills": []},
    {"summary": "x", "work": [{"bullets": ["no id"]}], "skills": []},
    {"summary": "x", "work": [{"id": "w1"}]},          # no skills list
    {"work": [{"id": "w1"}], "skills": []},            # no summary
])
def test_wrong_shapes_are_not_cv_prose(shape):
    assert _cv_prose_shape_ok(shape) is False


def test_the_writer_shape_is_cv_prose():
    assert _cv_prose_shape_ok(GOOD) is True


@pytest.mark.asyncio
async def test_an_analysis_only_corrector_reply_keeps_the_reviewed_draft():
    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=[
        {"approved": False, "issues": [{"text": "The project repeats the role bullet.", "severity": "blocking"}],
         "feedback": "Remove the duplicate."},
        dict(ANALYSIS_ONLY),
    ])
    result = await review_and_refine(
        source="src", draft=dict(GOOD),
        generator_prompt_fn=lambda d, f, s: "retry", generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev", reviewer_system="rev",
        provider=provider, max_retries=2, chain_id="cv_terminal_review",
        draft_shape_ok=_cv_prose_shape_ok,
    )
    assert result["work"] == GOOD["work"] and result["summary"] == GOOD["summary"]
    assert result["skills"] == GOOD["skills"]


def test_both_cv_correction_loops_pass_the_shape_predicate():
    """The tailoring loop and the terminal review (the language pass has its own
    #759 settle guard)."""
    src = (_backend / "applire" / "services" / "cv.py").read_text()
    calls = src.split("await review_and_refine(")[1:]
    by_chain = {}
    for call in calls:
        head = call[:2500]
        for chain in ("cv_tailoring", "cv_terminal_review"):
            if f'chain_id="{chain}"' in head:
                by_chain[chain] = "draft_shape_ok=_cv_prose_shape_ok" in head
    assert by_chain == {"cv_tailoring": True, "cv_terminal_review": True}
