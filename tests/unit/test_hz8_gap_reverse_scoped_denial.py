# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""u27 (Hazelnut build 8, WP-I; ruling MD8-3): the gap classifier's RECORDED DENIALS rule
carries both directions of a scoped denial. A qualifier on the DENIAL narrows it (v11). A
requirement that asks for the denied thing applied to an object is ruled out by it (v12),
while the object alone keeps its own evidence. The evidence is in the build-8 WP-I report
(luna n=10, Sonnet n=5); this test only pins the rule's presence and its two guards."""
from applire.prompts.gap_analysis import SYSTEM_PROMPT


def _denials_rule() -> str:
    start = SYSTEM_PROMPT.index("RECORDED DENIALS: when an entry there rules this requirement out")
    return SYSTEM_PROMPT[start:SYSTEM_PROMPT.index("COMPOUND requirements", start)]


def test_the_reverse_scoped_shape_is_in_the_rule():
    rule = _denials_rule()
    assert "APPLIED TO an object" in rule
    assert "is a requirement of its own" in rule and "keeps its own evidence" in rule
    assert 'never "partial"' in rule


def test_the_v11_narrowing_shape_survives():
    rule = _denials_rule()
    assert 'does not rule out "ECMO"' in rule
    assert '"no formal security certification" does not rule out "Security"' in rule
