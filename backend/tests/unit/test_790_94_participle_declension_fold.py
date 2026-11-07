# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""790:94 (ux-audit-3, blind Kaile): the terminal review reported "rollierender
Forecast" missing (``in_document: false``) on a CV that says "Erfahrung in
Jahresplanung und rollierendem Forecast" and "den rollierenden Forecast", while
``keywords.present`` listed the term through another ledger entry. The shared
presence fold (``surface_present``, Bug #797's attributive-adjective rule) did not
reach participles: "rollierend" has none of the closed adjective suffixes. One fix
in the shared predicate reaches every reader (terminal review ``in_document``,
``claim_missing``, the restore candidates, the ledger's presence).
"""
from __future__ import annotations

import pytest

from applire.services.ats_audit import _fold_variants, _norm, surface_present

_CV = _norm(
    "Erfahrung in Jahresplanung und rollierendem Forecast. "
    "Erstellt die Jahresplanung und den rollierenden Forecast für 78 Mio. €."
)


@pytest.mark.parametrize("phrase", ["rollierender Forecast", "Rollierende Forecasts"])
def test_present_participle_declines_like_an_adjective(phrase):
    assert surface_present(_norm(phrase), _CV)


def test_past_participle_declines_like_an_adjective():
    assert surface_present(_norm("zertifizierter Prozess"), _norm("im zertifizierten Prozess gearbeitet"))


def test_the_fold_stays_closed_for_short_stems_and_other_nouns():
    # "neuer"/"neuen" (stem < 6) and a non-adjective first token stay literal.
    assert _fold_variants(_norm("neuer Forecast")) == ["neuer forecast", "neuer forecasts"]
    assert not surface_present(_norm("rollierender Forecast"), _norm("rollierende Planung und ein Forecast"))
