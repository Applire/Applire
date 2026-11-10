# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""787:197 / I-3 (Hazelnut build 8, WP-I) — the clustering call never sees the posting's
text, so jd_context states what the role expects and gives no reason. Before: 55 of 66
captured jd_context sentences carried a composed "because ..."; one reached the interview
as the employer's setup ("likely deployed ... on a container platform"). Replay on the 12
captured clustering calls: reason clauses 57/156 -> 0/160 clusters (luna 2 x 7 + 6 x 2,
Sonnet 1 x 5); input gaps left unclustered 0/36 -> 1/36 calls (luna, not repeated in 12
further calls on the same input).
"""
from __future__ import annotations

from applire.prompts.gap_clustering import CLUSTERING_SYSTEM_PROMPT, build_clustering_prompt


def test_jd_context_is_asked_for_without_a_reason():
    flat = " ".join(CLUSTERING_SYSTEM_PROMPT.split())
    assert "why it matters" not in flat and "why the work needs it" not in flat
    assert "because payment events" not in flat and "weil die Fertigung" not in flat
    assert "It gives NO reason" in flat
    assert "never its text" in flat


def test_the_call_still_sees_no_posting_text_the_premise_of_the_rule():
    p = build_clustering_prompt(["Kubernetes"], [], ["Kubernetes"], [], lang="en")
    assert "Working knowledge" not in p and "posting text" not in p.lower()
    assert "Required skills from JD" in p
