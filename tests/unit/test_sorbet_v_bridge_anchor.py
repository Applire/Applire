# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sorbet invite-gate WP-V — a bridge anchor names a role, project or activity,
never a bare figure (Interview collector, PR #844 adversarial R6: "Zu MES bei 120:").

Measured over the 308 anchors the question writer named in the captured delivery
runs (p1 … q10): none without a letter, so the rule drops no real bridge. Synthetic
profile.
"""
import pytest

from applire.services.choice_grounding import build_bridge_openings

_PROFILE = {"work_experience": [{
    "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
    "responsibilities": ["Führung von 120 Mitarbeitenden in drei Schichten, 2,5 Mio. € Budget."],
}]}
_CLUSTER = {"label": "MES", "gaps": ["MES"], "jd_skills": []}


def _text(anchor):
    out = build_bridge_openings(
        [{"text": "", "level": "partial", "concept": "MES", "anchor": anchor}], _CLUSTER, _PROFILE, "de",
    )
    return out[0]["text"] if out else None


@pytest.mark.parametrize("anchor", ["120", "2,5", "2,5 Mio. €", " 120. "])
def test_a_figure_alone_is_no_anchor(anchor):
    assert _text(anchor) is None


@pytest.mark.parametrize("anchor", ["Produktionsleiter", "Führung von 120 Mitarbeitenden"])
def test_a_role_or_an_activity_with_a_figure_stays_an_anchor(anchor):
    assert _text(anchor) == f"Zu MES bei {anchor}:"
