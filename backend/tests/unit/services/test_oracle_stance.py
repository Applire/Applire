# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US245 — stance marker classification (DE+EN data files, unicode-normalized)."""
import pytest

from applire.services.oracle.stance import (
    classify_stance,
    marker_versions,
    normalize_stance_text,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        # EN
        ("Reduced manual effort by 70%.", "achieved"),
        ("Targets a ~70% reduction in manual effort.", "aspirational"),
        ("The project aims to cut onboarding time.", "aspirational"),
        ("Delivered the migration ahead of schedule.", "achieved"),
        ("Plans to deliver the rollout in Q3.", "aspirational"),
        # DE
        ("Manuellen Aufwand um 70% reduziert.", "achieved"),
        ("Soll den manuellen Aufwand um 70% senken.", "aspirational"),
        ("Kosten um 30% gesenkt.", "achieved"),
        ("Eine Senkung der Kosten ist geplant.", "aspirational"),
        # both classes present → achieved dominates (a delivery is asserted)
        ("Reduced costs by 20% and plans to cut another 10%.", "achieved"),
        # no markers
        ("Responsible for the compliance workflow.", None),
        ("", None),
        # word boundaries: "target audience" is not an aspiration marker
        ("Analyzed the target audience.", None),
    ],
)
def test_classify_stance(text, expected):
    assert classify_stance(text) == expected


def test_typographic_apostrophes_are_normalized():
    # U+2019 (’) and U+02BC (ʼ) fold to ASCII before matching (2026-07-11 lesson)
    assert normalize_stance_text("the team’s") == "the team's"
    assert normalize_stance_text("the teamʼs") == "the team's"
    assert classify_stance("Reduced the team’s effort by 70%.") == "achieved"


def test_marker_files_are_versioned_data():
    versions = marker_versions()
    assert versions.get("de") == "1.0"
    assert versions.get("en") == "1.0"
