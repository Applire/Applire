# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Founder ruling V-1 = B (787:L11, Hazelnut build 2) — the writers' half.

The KEYWORD LEDGER marks a claimable row no role bullet or story states with
``keyword_ledger.LISTED_ONLY_MARKER`` (V's half). The v2 CV and letter writers and
their correctors carry ONE rule for it: a listed-only term may be NAMED, never
narrated as responsibility, ownership or outcome. v1 stays byte-identical
(R-3 / R-10 precedent). These tests pin that the rule names the marker EXACTLY as
the ledger renders it — a reworded marker on either side would leave the model a
rule that points at nothing.
"""
from __future__ import annotations

import pytest

from applire.services.keyword_ledger import LISTED_ONLY_MARKER, render_ledger_prompt_block


def _v2_prompts() -> dict[str, str]:
    from applire.prompts import cover_letter as w
    from applire.prompts import cv_tailoring as c
    from applire.prompts import review_cover_letter as r

    return {
        "cv writer": c.V2_SYSTEM_PROMPT,
        "cv corrector": c.V2_TAILORING_REFINEMENT_PROMPT,
        "letter writer de": w.build_system_prompt("de", pipeline="v2"),
        "letter writer en": w.build_system_prompt("en", pipeline="v2"),
        "letter corrector": r.V2_COVER_LETTER_REFINEMENT_PROMPT,
    }


@pytest.mark.parametrize("name", list(_v2_prompts()))
def test_every_v2_writer_and_corrector_names_the_ledger_marker_verbatim(name):
    prompt = _v2_prompts()[name]
    assert LISTED_ONLY_MARKER in prompt
    assert "never narrate" in prompt or "never narrated" in prompt or "never written into a bullet" in prompt


def test_v1_prompts_carry_no_listed_only_rule():
    from applire.prompts import cover_letter as w
    from applire.prompts import cv_tailoring as c
    from applire.prompts import review_cover_letter as r

    for prompt in (c.SYSTEM_PROMPT, c.CV_TAILORING_REFINEMENT_PROMPT, w.SYSTEM_PROMPT,
                   r.COVER_LETTER_REFINEMENT_PROMPT):
        assert LISTED_ONLY_MARKER not in prompt


def test_the_rule_matches_what_the_ledger_actually_renders():
    block = render_ledger_prompt_block([{
        "concept": "SAP MM", "surface_forms": ["SAP MM"], "claimable": True,
        "status": "direct", "narrative_backed": False, "listed_only": True, "evidence": "skills: SAP MM",
    }])
    assert LISTED_ONLY_MARKER in block
