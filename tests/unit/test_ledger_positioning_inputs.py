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

"""What the writers are told about unmet and partly-met requirements
(ADR-048 amended 2026-07-27, clause 6).

Before this change the ledger gave the generators exactly two buckets —
claimable-with-evidence, and honest-gaps-as-prohibition. That binary is the
ledger's main defect at the writer seam:

  * a `denied` requirement arrived as a bare "do NOT claim" line, so nothing
    could tell it apart from a requirement nobody ever asked about;
  * a `partial` requirement, being claimable, drew the OPPOSITE pressure — the
    deterministic coverage check demanded the JD's own term appear in the
    draft. Charter run #7 exhausted the CV reviewer's full five-retry budget
    being pushed to insert `Payments platform`, `Settlement pipeline` and
    `Payout flows` into a document whose candidate did not own them.
"""

from applire.services.keyword_ledger import (
    render_ledger_prompt_block,
    verified_missing_claimable,
)


def _entry(concept, status, claimable, **kw):
    e = {
        "concept": concept,
        "surface_forms": [concept],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": status,
        "evidence": "",
        "claimable": claimable,
    }
    e.update(kw)
    return e


class TestCoveragePressure:
    def test_an_adjacent_partial_is_not_demanded_literally(self):
        """The candidate does not have TOGAF. Demanding the word "TOGAF" appear
        in the draft is a demand to over-claim — the whole point of recording
        the adjacency is that arc42 goes on the page instead."""
        ledger = [
            _entry("TOGAF", "partial", True, adjacent_evidence="arc42"),
        ]
        missing = verified_missing_claimable({"summary": "5 years of arc42."}, ledger)
        assert [m["concept"] for m in missing] == []

    def test_a_below_the_bar_partial_is_still_demanded(self):
        """The other kind of partial: the candidate really does have Python,
        just less of it. The term belongs on the page."""
        ledger = [_entry("Python", "partial", True)]
        missing = verified_missing_claimable({"summary": "Backend engineer."}, ledger)
        assert [m["concept"] for m in missing] == ["Python"]

    def test_a_direct_entry_is_still_demanded(self):
        ledger = [_entry("Kubernetes", "direct", True)]
        missing = verified_missing_claimable({"summary": "Backend engineer."}, ledger)
        assert [m["concept"] for m in missing] == ["Kubernetes"]


class TestPromptBlockDistinguishesDeniedFromUnknown:
    def test_denied_is_named_as_the_candidates_own_statement(self):
        block = render_ledger_prompt_block(
            [_entry("BaFin supervision", "denied", False)]
        )
        assert "BaFin supervision" in block
        low = block.lower()
        assert "denied" in low or "stated they do not" in low, (
            "a denial must reach the writer as the candidate's own position, not "
            "as an anonymous absence — the letter has to be able to position it"
        )

    def test_unknown_gap_still_renders_as_do_not_claim(self):
        block = render_ledger_prompt_block([_entry("Erlang", "gap", False)])
        assert "Erlang" in block
        assert "DO NOT CLAIM" in block

    def test_adjacent_partial_tells_the_writer_what_to_promote(self):
        block = render_ledger_prompt_block(
            [_entry("TOGAF", "partial", True, adjacent_evidence="arc42")]
        )
        assert "arc42" in block, (
            "the writer must be told which vault item to give prominence; "
            "without it the only actionable instruction is 'surface TOGAF'"
        )
