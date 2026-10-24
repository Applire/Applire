# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 4 / WP-G: the classifier's ``adjacent_evidence`` sometimes arrives as
a JSON LIST ("the profile's own name(s)"). ``str()`` of it carried the Python repr
``"['Jahresabschluss (HGB)']"`` into the ledger, the writer prompt and the CV
bullet-retention forms, where it matches no bullet (7 of 233 adjacent rows over the
build-3/4 matrices, Katrin run v5/3)."""

from applire.services.keyword_ledger import (
    adjacent_evidence_text,
    build_keyword_ledger,
    retention_forms,
)


def _ledger(adjacent):
    return build_keyword_ledger(
        [
            {
                "concept": "Budgetprozess",
                "status": "partial",
                "evidence": "Jahresplanung sowie Budgetierung & Forecast sind im Profil belegt.",
                "surface_forms": ["Budgetprozess"],
                "adjacent_evidence": adjacent,
            }
        ],
        ["Budgetprozess"],
        [],
        [],
    )


def test_a_list_is_joined_never_its_python_repr():
    row = _ledger(["Jahresplanung", "Budgetierung & Forecast"])[0]
    assert row["adjacent_evidence"] == "Jahresplanung, Budgetierung & Forecast"
    assert "[" not in row["adjacent_evidence"]


def test_a_string_is_kept_as_is():
    assert _ledger("  Budgetierung & Forecast ")[0]["adjacent_evidence"] == "Budgetierung & Forecast"


def test_text_helper_shapes():
    assert adjacent_evidence_text(["a", " ", 3, "a", "b"]) == "a, b"
    assert adjacent_evidence_text(None) == ""
    assert adjacent_evidence_text({"x": 1}) == ""


def test_retention_forms_carry_each_listed_name():
    entry = {
        "concept": "Budgetprozess",
        "status": "partial",
        "claimable": True,
        "adjacent_evidence": "Jahresplanung, Budgetierung & Forecast",
        "surface_forms": ["Budgetprozess"],
    }
    forms = retention_forms(entry)
    assert "Jahresplanung" in forms and "Budgetierung & Forecast" in forms
    assert "Budgetprozess" not in forms  # positioning-only: the JD's term protects nothing
