# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""A `partial` entry records WHAT makes it partial (ADR-048 amended 2026-07-27).

`partial` conflated two different situations under one bare label:

  * the candidate has an ADJACENT capability — the JD asks for TOGAF, the
    candidate has five years of arc42;
  * the candidate has the RIGHT capability below a stated bar — the JD asks for
    five years, the candidate has two.

The discriminator survived only as free-text `evidence` prose, so no consumer
could act on it. That is why the CV writer is told "TOGAF is claimable, surface
it" when the only truthful instruction is "give arc42 prominence".

One pointer field, not a taxonomy of adjacency kinds.
"""

from applire.services.keyword_ledger import build_keyword_ledger


def test_partial_entry_carries_the_adjacent_evidence_pointer():
    out = build_keyword_ledger(
        required_skills=["TOGAF"],
        nice_to_have_skills=[],
        keywords=[],
        classifications=[
            {
                "concept": "TOGAF",
                "status": "partial",
                "reason": "no TOGAF, but 5 years applying arc42 for architecture documentation",
                "adjacent_evidence": "arc42",
            }
        ],
    )
    entry = next(e for e in out if e["concept"] == "TOGAF")
    assert entry["status"] == "partial"
    assert entry["adjacent_evidence"] == "arc42"


def test_below_the_bar_partial_has_no_adjacent_pointer():
    """The other kind of `partial`: right capability, not enough of it. There
    is no adjacent thing to promote, and the field must stay absent rather than
    inventing one."""
    out = build_keyword_ledger(
        required_skills=["5+ years Python"],
        nice_to_have_skills=[],
        keywords=[],
        classifications=[
            {
                "concept": "5+ years Python",
                "status": "partial",
                "reason": "Python present, 2 years — below the stated 5-year bar",
            }
        ],
    )
    entry = next(e for e in out if e["concept"] == "5+ years Python")
    assert entry["status"] == "partial"
    assert not entry.get("adjacent_evidence")


def test_direct_entry_never_carries_an_adjacent_pointer():
    """A full match has nothing adjacent about it — a stray pointer here would
    mislead the writer into promoting a substitute over the real thing."""
    out = build_keyword_ledger(
        required_skills=["Kubernetes"],
        nice_to_have_skills=[],
        keywords=[],
        classifications=[
            {
                "concept": "Kubernetes",
                "status": "direct",
                "reason": "led ECS to EKS migration for 12 services",
                "adjacent_evidence": "Docker",
            }
        ],
    )
    entry = next(e for e in out if e["concept"] == "Kubernetes")
    assert not entry.get("adjacent_evidence")
