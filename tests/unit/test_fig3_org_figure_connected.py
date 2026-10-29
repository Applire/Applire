# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FIG-3 (founder ruling, Hazelnut build 5): an organisation-scale figure the profile
holds in an entry is written CONNECTED to that entry's work ("contributed to the
company's €X revenue by rolling out …"). Never a sentence of its own, never
"separately", never a figure from another entry, and no adjective the profile does
not hold. The reviewer never flags or splits that connection.

Delivery run 4 trace: the writer joined it, the tailoring reviewer asked to split it
4/5, the corrector wrote "BioNTech reported €19 billion …", and the terminal corrector
added "Separately". These pins hold the wording; compliance was measured by replay."""

from __future__ import annotations

from applire.prompts import cv_segmented, cv_tailoring, review_cv_tailoring


def _flat(s: str) -> str:
    return " ".join(s.split())


def test_every_writer_connects_the_figure_to_its_entrys_work():
    for prompt in (cv_tailoring.SYSTEM_PROMPT, cv_tailoring.V2_SYSTEM_PROMPT,
                   cv_segmented.WORK_SECTION_SYSTEM_PROMPT):
        p = _flat(prompt)
        assert "contributed to the company's €X revenue by rolling out" in p
        assert '"separately"' in p
        assert "another entry" in p
        assert "adjective" in p


def test_every_reviewer_keeps_the_connection_and_flags_the_split():
    for prompt in (review_cv_tailoring.REVIEW_SYSTEM_PROMPT, review_cv_tailoring.TERMINAL_REVIEW_SYSTEM_PROMPT,
                   review_cv_tailoring.V2_REVIEW_SYSTEM_PROMPT,
                   review_cv_tailoring.V2_TERMINAL_REVIEW_SYSTEM_PROMPT):
        p = _flat(prompt)
        assert "never flag or split it" in p
        assert 'in a sentence of its own or "separately", or taken from another entry, is a finding' in p
        assert "scale of the operation" not in p  # the framing that drove the split
        # D-20 (r5 replay, 15 captured terminal reviews of joined drafts): a "subject"
        # clause raised false flags 1/15 -> 7/15; the FIG-3 example form stays.
        assert 'joined as "contributed to €N … by <that work>", is correct' in p
        assert "whose subject is" not in p


def test_one_home_per_fact_at_the_writer_and_the_corrector():
    """D-20 (r5 bench + panel: "the project restates the role"): the vault holds the
    same work in an entry line AND its nested project; the home is the project."""
    for prompt in (cv_tailoring.SYSTEM_PROMPT, cv_tailoring.V2_SYSTEM_PROMPT):
        p = _flat(prompt)
        assert "never duplicated into the entry's bullets" in p
        assert "state the same work or result, write it in the project only" in p
    corrector = _flat(cv_tailoring.V2_TAILORING_REFINEMENT_PROMPT)
    assert "keep it in the project's bullets and remove it from the entry's bullets" in corrector
    assert "Never drop the project for it" in corrector
