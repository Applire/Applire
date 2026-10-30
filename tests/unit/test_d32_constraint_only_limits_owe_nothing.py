# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-32 (Hazelnut build 5, round 9): a stated limit the posting does not ask about
is owed nothing — the review entry says so, in the words the writer block uses.

Delivery run 7 rep a: the posting's key requirements selected none of the
candidate's two denials (ADR-075 / DEL-3 input filter), so the writer got the
"CONSTRAINT ONLY — the letter NEVER mentions them" block. The reviewers got the
grounding entry instead, which said nothing of the sort and invited "an honest
denial"; three reviewer rounds demanded "the required stated_limits positioning"
and the letter shipped two denials, both panelists' leading No reason.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services import cover_letter as cl  # noqa: E402


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_constraint_only_entry_says_the_limits_are_owed_nothing():
    entry = cl._constraining_stated_limits_entry(["Mit X habe ich keine Erfahrung."])
    text = _flat(entry["instruction"])
    assert "required" not in entry
    assert text.startswith("CONSTRAINT ONLY — owed nothing.")
    assert "the letter never mentions them" in text
    assert "a review never demands one — a letter that writes one has a finding" in text
    # the invitation that read as a demand is gone from THIS entry
    assert "an honest denial names" not in text
    # DEL-1 still reaches the review constraint
    assert "never write the withholding into the letter (RULING DEL-1)" in text


def test_the_required_entry_keeps_its_positioning_contract():
    # the owed (ADR-075) entry is unchanged: still required, still the shared constraint
    assert cl._STATED_LIMITS_CONSTRAINT not in cl._STATED_LIMITS_NOT_OWED
    assert "an honest denial names the adjacent strengths" in cl._STATED_LIMITS_CONSTRAINT
