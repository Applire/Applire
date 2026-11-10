# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire Community Edition.
#
# Applire is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
"""RULING W-2 (founder, Hazelnut build 8): the person's own career figure is the
ceiling for a computed tool span, in the CV and the letter alike (one producer)."""

from applire.services.prompt_view import own_career_ceiling, span_above_career


def _profile(summary: str | None, roles: list[str] | None = None) -> dict:
    p: dict = {"work_experience": [{"id": "w1", "responsibilities": roles or []}]}
    if summary is not None:
        p["professional_summary"] = {"en": summary}
    return p


def test_the_career_figure_is_the_summarys_largest_own_figure():
    p = _profile("Backend engineer with 8 years of experience in Python services.")
    assert own_career_ceiling(p) == 8.0


def test_both_directions_on_the_felix_shape():
    """9.4 years of PostgreSQL over an 8-year career: no duration. 5.7 years of
    Django below it: kept."""
    ceiling = own_career_ceiling(_profile("Backend engineer with 8 years of experience."))
    assert span_above_career(9.4, ceiling) is True
    assert span_above_career(5.7, ceiling) is False
    assert span_above_career(7.9, ceiling) is False


def test_a_span_equal_to_the_career_figure_gets_no_duration():
    """ADV-2 (build 8 adversarial pass): the only wording for a span of exactly 8.0
    years is "über 8 Jahre", which reads as more than the candidate's own "8 years"
    just as 8.6 does. At or above the figure: no duration."""
    ceiling = own_career_ceiling(_profile("Backend engineer with 8 years of experience."))
    assert span_above_career(8.0, ceiling) is True


def test_two_summary_languages_that_disagree_give_the_smaller_career_figure():
    """ADV-2: the DE summary says 8 years, the EN one 10. The largest figure across
    both let the German CV offer "über 9 Jahre PostgreSQL" beside its own "8 Jahren"
    (the 786:226 case). The career figure is the largest WITHIN one summary and the
    smallest ACROSS the languages, so no document shows a span above its own figure."""
    p = {"professional_summary": {
        "de": "Backend-Entwickler mit 8 Jahren Erfahrung, davon 3 Jahre in Zahlungssystemen.",
        "en": "Backend engineer with 10 years of experience, including my apprenticeship.",
    }}
    assert own_career_ceiling(p) == 8.0
    assert span_above_career(9.4, own_career_ceiling(p)) is True
    only_en = {"professional_summary": {"de": "Backend-Entwickler für Zahlungssysteme.",
                                        "en": "Backend engineer with 10 years of experience."}}
    assert own_career_ceiling(only_en) == 10.0


def test_no_own_career_figure_means_no_ceiling_and_no_cut():
    p = _profile("Backend engineer designing and operating Python services.")
    assert own_career_ceiling(p) is None
    assert span_above_career(25.0, own_career_ceiling(p)) is False
    assert own_career_ceiling({}) is None
    assert own_career_ceiling(None) is None


def test_a_role_lines_figure_is_never_the_career():
    """"Seit zwei Jahren fachliche Führung" is a figure about that role: it must not
    cap every tool at two years."""
    p = _profile(None, roles=["Seit zwei Jahren fachliche Führung der beiden Werkstudierenden."])
    assert own_career_ceiling(p) is None
    p = _profile("Controllerin mit 9 Jahren Erfahrung.",
                 roles=["Seit zwei Jahren fachliche Führung der beiden Werkstudierenden."])
    assert own_career_ceiling(p) == 9.0


def test_the_largest_summary_figure_is_the_career_not_a_part_of_it():
    """"7 years of acute care, including 4 years in intensive care": the career is 7."""
    p = _profile("Registered Nurse with 7 years of acute-care experience, including 4 years in adult intensive care.")
    assert own_career_ceiling(p) == 7.0
