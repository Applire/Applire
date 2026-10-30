# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""M-3 (Hazelnut build 5, delivery run 6): a ripple of the D-20 "one home per fact"
rule. A corrector moved a role's go-live line into its nested project, which was
correct: the same work stated twice. The next corrector round got only the
page-length finding ("shorten older roles toward one strong line"). It then cut the
role's LAST own bullet, a fact the project never states, so the role showed only
its project.

Replay of that captured corrector call on gpt-6-luna, n=5: the role had no bullet
of its own in 5/5 before and 0/5 after. Only the SAME work moves. The role keeps
every fact the project does not state, and always at least one bullet of its own.
These pins hold the wording at the writer, the v2 corrector, and the page-length
finding (pinned in test_e068_cv_pipeline_v2).
"""
from __future__ import annotations

from applire.prompts import cv_tailoring


def _flat(s: str) -> str:
    return " ".join(s.split())


def test_the_writer_moves_only_the_same_work():
    for prompt in (cv_tailoring.SYSTEM_PROMPT, cv_tailoring.V2_SYSTEM_PROMPT):
        p = _flat(prompt)
        assert "write it in the project only; the entry keeps its other facts." in p


def test_the_corrector_never_empties_a_role():
    p = _flat(cv_tailoring.V2_TAILORING_REFINEMENT_PROMPT)
    assert "Only the SAME work moves" in p
    assert "the entry keeps every fact the project does not state" in p
    assert "always at least one bullet of its own" in p
