# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-29 (Hazelnut build 5, delivery run 6): a letter introduced the candidate's main
system twice. Paragraph 2 introduced it, and the honest-gap paragraph opened again
with "At <employer>, I lead <the system> … that runs on <platform>". The REQUIRED
stated-limits block asks for ONE honest-gap paragraph whose gap sentence carries its
transferring strength. When that strength was the system already introduced, the
writer introduced it a second time.

The block now says the gap sentence names such a system back in a few words, and a
scoped claim about it joins the paragraph that introduced it. Replay of the two
captured writer calls (gpt-6-luna, n=10 each): second introduction 8/20 -> 3/20,
with the limit still positioned. Synthetic data only."""

from __future__ import annotations

from applire.services.cross_document import render_required_limits_block


def _flat(s: str) -> str:
    return " ".join(s.split())


def test_the_gap_sentence_names_an_introduced_system_back():
    p = _flat(render_required_limits_block(["Mit Kubernetes habe ich keine Erfahrung."]))
    assert "ONE HOME PER FACT" in p
    assert '"no X experience, but that system …"' in p
    assert "never introduces it again" in p
    assert "joins the paragraph that introduced it" in p


def test_the_existing_obligations_stay():
    p = _flat(render_required_limits_block(["Mit Kubernetes habe ich keine Erfahrung."]))
    assert "SAME single honest-gap paragraph" in p
    assert "in ONE sentence" in p
    assert "Mit Kubernetes habe ich keine Erfahrung." in p
    assert render_required_limits_block([]) == ""
