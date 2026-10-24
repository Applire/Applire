# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

"""FS-7 (ADR-059 amended 2026-10-24) — a denial phrased as term + generic head
noun DECLARES the bare term, so the candidate's own imported CV can no longer
release it as a "narrower compound".

Build-3 residue: "agile methodology experience" floored the row "agile
methodologies" until an imported bullet named the term, and then E-11's
containment release lifted it (787:L43 fixture shape; 7 of 54 distinct denials
in the E-11 label packet carry such a head noun).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_backend = Path(__file__).resolve().parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.keyword_ledger import reevaluate_gap_ledger_against_vault  # noqa: E402
from applire.services.profile.reconcile.stance import (  # noqa: E402
    containment_release_form,
    declared_denial_matches,
    declared_only_by_head_noun,
    head_noun_term,
)


@pytest.mark.parametrize(
    "denial, bare",
    [
        ("payment-institution experience", "payment institution"),
        ("eigene Erfahrung mit Vertrieb", "vertrieb"),
        ("Erfahrung im Vertrieb", "vertrieb"),
        ("keine Kenntnisse in SAP", "sap"),
        ("audit-trail background", "audit trail"),
        ("agile methodology experience", "agile methodology"),
        ("regulated-environment experience", "regulated environment"),
        # A narrowing qualifier stays in the term — never bare "SBAR"/"Kubernetes".
        ("formal SBAR experience", "formal sbar"),
        ("hands-on Kubernetes experience", "hands on kubernetes"),
        ("praktische Erfahrung mit SAP", None),
        # Not a head-noun frame / a head noun alone.
        ("direkte Produktion für Lebensmittelkunden", None),
        ("Kafka transactions", None),
        ("experience", None),
        ("Erfahrung", None),
    ],
)
def test_the_closed_head_noun_list(denial, bare):
    assert head_noun_term(denial) == bare


def test_a_head_noun_denial_declares_the_bare_term():
    assert declared_denial_matches("Vertrieb", ["eigene Erfahrung mit Vertrieb"]) == [
        "eigene Erfahrung mit Vertrieb"
    ]
    assert declared_only_by_head_noun("Vertrieb", ["eigene Erfahrung mit Vertrieb"]) == [
        "eigene Erfahrung mit Vertrieb"
    ]
    # A literal denial of the term is not "only by head noun".
    assert declared_only_by_head_noun("Vertrieb", ["Vertrieb"]) == []


def test_the_cv_no_longer_releases_a_head_noun_denial():
    """The release predicate itself: a declared match is absolute."""
    corpus = "teamarbeit nach agile methodologies im projektalltag"
    assert (
        containment_release_form(
            "agile methodologies", ["agile methodologies"], ["agile methodology experience"], corpus
        )
        is None
    )
    # A narrowing qualifier keeps the old reading: the CV still releases bare SBAR.
    assert (
        containment_release_form("SBAR", ["SBAR"], ["formal SBAR experience"], "sbar handover in the icu")
        == "SBAR"
    )


def _vault(bullet: str, denial: str) -> dict:
    return {
        "work_experience": [{"id": "w1", "company": "Acme", "role": "PM", "responsibilities": [bullet]}],
        "metadata": {
            "denied_concepts": [{"concept": denial, "statement": "s", "source": "interview",
                                 "date": "2026-10-01"}],
            "enrichment_history": [{
                "source": "cv_upload", "timestamp": "2026-09-01T00:00:00Z",
                "changes": [{"section": "work_experience", "field": "responsibilities",
                             "action": "merged", "old_value": None, "new_value": [bullet]}],
            }],
        },
    }


def test_seam_the_vault_recheck_keeps_the_bare_term_floored_against_an_imported_bullet():
    """Seam (ledger re-evaluation, the E-11 release path): the 787:L43 shape
    with the bullet from the IMPORTED CV — before FS-7 this released."""
    row = {"concept": "agile methodologies", "status": "gap", "claimable": False,
           "surface_forms": ["agile methodologies"], "evidence": "", "fit_weight": 1.0,
           "sources": ["required"]}
    vault = _vault("Teamarbeit nach agile methodologies im Projektalltag", "agile methodology experience")
    out, _changed = reevaluate_gap_ledger_against_vault([row], vault)
    assert out[0]["claimable"] is False


def test_seam_a_narrowing_qualifier_still_lets_the_imported_cv_release():
    row = {"concept": "SBAR", "status": "gap", "claimable": False,
           "surface_forms": ["SBAR"], "evidence": "", "fit_weight": 1.0, "sources": ["required"]}
    vault = _vault("Structured SBAR handover on every shift change", "formal SBAR experience")
    out, _changed = reevaluate_gap_ledger_against_vault([row], vault)
    assert out[0]["claimable"] is True
