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

"""How the four-status ledger reaches the ATS audit (ADR-048/059 am. 2026-07-27).

The ATS report grades the DOCUMENT; the ledger status describes the CANDIDATE.
A status may only ever change how a miss is EXPLAINED — never whether it counts.
Three seams violated that after the four-status split landed:

  * ``present_unsupported`` graded an honest denial as an unsupported claim. It
    matches by normalised substring, and a substring cannot see negation, so the
    positioning sentence the amended prompts now ask for ("I have not worked in
    BaFin supervision") was indistinguishable from claiming it. Every instrument
    that reads for MEANING already exempts a denial clause — this quadrant was
    the sole outlier.
  * ``missing_claimable`` demanded an ADJACENT partial's JD term while the
    coverage reviewer, correctly, did not — so the panel flagged the document for
    omitting exactly what the pipeline decided to omit (#122's "the loop that
    grades is the loop that heals", stated the other way round).
  * the page budget protected the JD term (TOGAF) instead of the substitute
    (arc42), leaving the promoted bullet a no-hit — first in line to be cut.
"""

import pytest

from applire.services.ats_audit import _keyword_coverage, _norm
from applire.services.cv_budget import _flatten_claimable_forms, _hit_count
from applire.services.keyword_ledger import (
    is_positioning_only,
    retention_forms,
    split_ledger_for_prompt,
    verified_missing_claimable,
)

# The candidate: holds Kubernetes and arc42. Told us plainly they have never
# worked in BaFin supervision. Nobody ever asked about Erlang.
LEDGER = [
    {
        "concept": "Kubernetes",
        "surface_forms": ["Kubernetes", "K8s"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "direct",
        "evidence": "Ran EKS for 12 services.",
        "claimable": True,
    },
    {
        "concept": "TOGAF",
        "surface_forms": ["TOGAF"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "partial",
        "evidence": "Five years of architecture practice.",
        "claimable": True,
        "adjacent_evidence": "arc42",
    },
    {
        "concept": "BaFin supervision",
        "surface_forms": ["BaFin supervision"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "denied",
        "evidence": "Candidate explicitly stated a limit here (interview).",
        "claimable": False,
    },
    {
        "concept": "Erlang",
        "surface_forms": ["Erlang"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "gap",
        "evidence": "",
        "claimable": False,
    },
]

JD_KEYWORDS = ["Kubernetes", "TOGAF", "BaFin supervision", "Erlang"]

# What the amended prompts ask for: name the denial as the candidate's own
# position, promote the adjacent capability instead of the JD's term.
HONEST_LETTER = (
    "I have run Kubernetes in production for six years. "
    "I have led architecture work using arc42 throughout that time. "
    "I have not worked in BaFin supervision and would be learning that on the job."
)

# The vault's own literal text. Denial statements are stripped from this corpus
# upstream (profile_literal_corpus / _strip_denial_text, wave-6), so "BaFin
# supervision" is genuinely absent here — the #249 escape hatch cannot and must
# not rescue the sentence above.
VAULT = _norm("Kubernetes EKS arc42 architecture")


def _coverage(text: str, ledger=LEDGER, keywords=JD_KEYWORDS):
    return _keyword_coverage(_norm(text), keywords, ledger, vault_text_norm=VAULT)


class TestHonestDenialIsNotAnUnsupportedClaim:
    def test_a_denied_concept_named_in_a_negation_is_not_flagged(self):
        """The PO decision (2026-07-27): truthfulness is owed to a reader that
        parses meaning, not to a substring counter. The sentence is honest to
        every such reader; that the counter scores it as coverage is the
        counter's problem, not a defect to warn about."""
        assert _coverage(HONEST_LETTER).present_unsupported == []

    def test_an_unknown_gap_is_still_flagged(self):
        """No regression on the quadrant's real job (#117). Nobody denied
        Erlang — it simply has no backing, so writing it is unsupported."""
        text = HONEST_LETTER + " I have shipped Erlang services."
        assert _coverage(text).present_unsupported == ["Erlang"]

    def test_the_denied_keyword_still_counts_toward_coverage(self):
        """The ring measures the document against the JD, and the term IS on the
        page. Scoring it honestly is the whole point of leaving the quadrant."""
        cov = _coverage(HONEST_LETTER)
        assert "BaFin supervision" in cov.present

    def test_a_denied_concept_absent_from_the_document_stays_an_honest_gap(self):
        cov = _coverage("I have run Kubernetes in production. I use arc42.")
        assert "BaFin supervision" in cov.missing_honest_gap
        assert "BaFin supervision" not in cov.missing_claimable


class TestPanelAndReviewerAgreeOnAdjacency:
    def test_an_adjacent_partial_is_not_a_surfacing_miss(self):
        """The candidate does not have TOGAF. Its absence is honest, not a miss
        the writer should have avoided — grading it amber tells the user to
        over-claim."""
        cov = _coverage(HONEST_LETTER)
        assert "TOGAF" not in cov.missing_claimable
        assert "TOGAF" in cov.missing_honest_gap

    def test_a_below_the_bar_partial_is_still_a_surfacing_miss(self):
        """The other kind of partial carries no pointer: the candidate really
        does have the skill, just less of it. The term belongs on the page."""
        ledger = [dict(LEDGER[1], concept="Python", surface_forms=["Python"])]
        del ledger[0]["adjacent_evidence"]
        cov = _coverage("Backend work.", ledger=ledger, keywords=["Python"])
        assert cov.missing_claimable == ["Python"]

    def test_the_two_instruments_do_not_contradict_each_other(self):
        """#122's invariant, checked directly rather than per-instrument: the
        pipeline must not ship a document its own panel flags."""
        draft = {"body": HONEST_LETTER}
        reviewer = {m["concept"] for m in verified_missing_claimable(draft, LEDGER)}
        panel = set(_coverage(HONEST_LETTER).missing_claimable)
        assert reviewer == panel

    def test_claimable_concepts_never_advertises_a_term_we_will_not_write(self):
        """`claimable_concepts` rides on the report and drives the truthfulness
        panel's third state — TOGAF there would invite the user to add it."""
        assert "TOGAF" not in _coverage(HONEST_LETTER).claimable_concepts


class TestRetentionProtectsTheSubstitute:
    def test_positioning_only_entries_are_recognised(self):
        assert is_positioning_only(LEDGER[1]) is True
        assert is_positioning_only(LEDGER[0]) is False
        assert is_positioning_only(None) is False

    def test_retention_forms_swap_the_jd_term_for_the_substitute(self):
        assert retention_forms(LEDGER[1]) == ["arc42"]
        assert retention_forms(LEDGER[0]) == ["Kubernetes", "K8s", "Kubernetes"]

    def test_the_budget_protects_arc42_not_togaf(self):
        claimable, _ = split_ledger_for_prompt(LEDGER)
        forms = _flatten_claimable_forms(claimable)
        assert "arc42" in forms
        assert "TOGAF" not in forms, (
            "no bullet contains TOGAF, so retaining that form protects nothing "
            "while the arc42 bullet it stands in for scores as a no-hit and is "
            "cut first by condense_to_budget"
        )

    @pytest.mark.parametrize(
        "role_text,expected",
        [
            ("Led architecture work using arc42 across four teams.", 1),
            ("Ran Kubernetes clusters and arc42 architecture reviews.", 2),
            ("Managed a helpdesk rota.", 0),
        ],
    )
    def test_the_role_carrying_the_substitute_scores_as_relevant(self, role_text, expected):
        """Before the fix the arc42 role scored zero hits and dropped to the
        tightest bullet tier — the page budget cutting the very evidence the CV
        prompt had just been told to give prominence."""
        claimable, _ = split_ledger_for_prompt(LEDGER)
        assert _hit_count(_norm(role_text), claimable) == expected


class TestSurfaceFormsStayNarrow:
    def test_the_adjacent_term_never_makes_the_jd_term_read_as_present(self):
        """arc42 belongs to RETENTION only. Leaking it into surface_forms would
        make an arc42 bullet satisfy TOGAF — precisely the over-claim the
        adjacency record exists to prevent."""
        cov = _coverage("I have led architecture work using arc42.")
        assert "TOGAF" not in cov.present
