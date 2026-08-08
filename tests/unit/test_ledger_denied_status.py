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

"""`denied` as a first-class ledger status (ADR-059 amended 2026-07-27).

`gap` used to mean two different things — "no signal" and "the candidate told
us no" — so the only durable record of a refusal lived in
``ProfileMetadata.denied_concepts``, a different structure every consumer had
to join against the ledger. These tests pin the four-value vocabulary and, more
importantly, the invariant ADR-059 Decision clause 2 always stated but nothing
enforced: **a denial never upgrades the ledger.**

Ground truth for the regressions below is charter run #7 (2026-07-26, synthetic
case ``it_backend_daniel``), which persisted a ledger row where eight denied
concepts sat at ``status="direct", claimable=True`` with the candidate's own
denial sentence stored as their backing evidence.
"""

from applire.services.keyword_ledger import (
    DENIAL_FLOOR_EVIDENCE,
    DENIED_EVIDENCE,
    _enforce_denial_stance,
    build_keyword_ledger,
    profile_literal_corpus,
    upgrade_ledger_for_concepts,
)
from applire.services.profile.reconcile.stance import is_denied_concept

# The verbatim run-7 answers. Q3 denied the regulated-environment requirement;
# Q5 answered payments and never mentioned crypto at all.
RUN7_DENIAL_ANSWER = (
    "No — I have not worked under a PSD2 licence or with BaFin supervision, so "
    "that regulated exposure is genuinely new to me. On security: at Finleap I "
    "ran our quarterly dependency-audit rotation and fixed two externally "
    "reported auth issues, a session fixation and an IDOR on invoice PDFs."
)
RUN7_PAYMENTS_ANSWER = (
    "My payments exposure is the Stripe checkout integration at Finleap — PSP "
    "integration, webhooks, and reconciliation of payouts against invoices. To "
    "be clear, I have not worked under a PSD2 licence or with BaFin supervision."
)


def _entry(concept, status="gap", claimable=False, **kw):
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


class TestDenialFloorWritesTheStatus:
    def test_denied_concept_gets_status_denied_not_gap(self):
        """The floor must record WHY the concept is unclaimable.

        Forcing it to ``gap`` loses the distinction between "we never asked"
        and "the candidate said no" — the distinction the interview, the
        writers and the critic all need in order to behave differently.
        """
        ledger = [_entry("BaFin supervision", status="direct", claimable=True)]
        out = _enforce_denial_stance(ledger, ["BaFin supervision"])
        assert out[0]["status"] == "denied"
        assert out[0]["claimable"] is False

    def test_undenied_entry_is_untouched(self):
        ledger = [_entry("Kubernetes", status="direct", claimable=True)]
        out = _enforce_denial_stance(ledger, ["BaFin supervision"])
        assert out[0]["status"] == "direct"
        assert out[0]["claimable"] is True


class TestDeniedSurvivesARebuild:
    def test_denied_is_a_valid_status_and_is_not_coerced_to_gap(self):
        """A persisted ``denied`` entry must round-trip through a rebuild.

        ``build_keyword_ledger`` fails closed on an unrecognised status by
        forcing ``gap`` — which would silently erase every denial the moment
        the ledger is rebuilt.
        """
        out = build_keyword_ledger(
            required_skills=["BaFin supervision"],
            nice_to_have_skills=[],
            keywords=[],
            classifications=[
                {
                    "concept": "BaFin supervision",
                    "status": "denied",
                    "evidence": "Candidate explicitly stated a limit here (interview).",
                }
            ],
        )
        entry = next(e for e in out if e["concept"] == "BaFin supervision")
        assert entry["status"] == "denied"
        assert entry["claimable"] is False


class TestADR059Clause2ADenialNeverUpgrades:
    """ADR-059: "A denial never marks a gap addressed and never upgrades the
    ledger." Run #7 proved this was aspirational — the #188 seam had no notion
    of polarity, so an answer that DENIED a requirement flipped it to
    ``direct + claimable`` with the denial itself as the evidence."""

    def test_denial_answer_does_not_upgrade_the_denied_concept(self):
        ledger = [_entry("BaFin supervision")]
        out, changed = upgrade_ledger_for_concepts(
            ledger,
            ["BaFin supervision"],
            RUN7_DENIAL_ANSWER,
            denied_concepts=["BaFin supervision"],
        )
        assert out[0]["claimable"] is False
        assert out[0]["status"] == "denied"
        assert RUN7_DENIAL_ANSWER not in (out[0]["evidence"] or ""), (
            "the candidate's own denial must never be stored as the evidence "
            "that backs the claim it denies"
        )
        assert changed is True, "the status DID change — gap became denied"

    def test_an_already_denied_entry_is_never_re_upgraded(self):
        """Belt and braces: even with no denial list passed, a persisted
        ``denied`` status is authoritative on its own."""
        ledger = [_entry("BaFin supervision", status="denied")]
        out, changed = upgrade_ledger_for_concepts(
            ledger, ["BaFin supervision"], "Actually I have loads of it."
        )
        assert out[0]["status"] == "denied"
        assert out[0]["claimable"] is False
        assert changed is False

    def test_a_genuine_confirmation_still_upgrades(self):
        """The floor must not become a blanket freeze — #188's whole purpose
        is that a confirmed strength stops being hedged as a growth area."""
        answer = "I led the migration from ECS to Kubernetes on EKS for 12 services."
        ledger = [_entry("Kubernetes")]
        out, changed = upgrade_ledger_for_concepts(
            ledger, ["Kubernetes"], answer, denied_concepts=["BaFin supervision"]
        )
        assert changed is True
        assert out[0]["status"] == "direct"
        assert out[0]["claimable"] is True
        assert out[0]["evidence"] == answer


class TestADR059ARetractionReversesAnUpgrade:
    """#352 — the floor could guard a write in flight but never reverse one.

    ``upgrade_ledger_for_concepts`` filtered candidate entries on
    ``not claimable`` BEFORE either denial floor ran, so an entry an earlier
    turn had already flipped to ``direct``/``claimable`` was skipped outright
    and the candidate's own retraction could not reach it. The durable floor
    (:func:`_enforce_denial_stance`) has always reversed a claimable entry —
    it logs a warning when it does — so the two instruments ADR-059 clause 3
    requires to agree ("polarity is consulted at EVERY ledger write seam")
    disagreed: one rebuild of the same row produced ``denied``, the in-place
    seam kept ``direct``.
    """

    UPGRADED = "I administered Kubernetes clusters for 3 years."
    RETRACTION = "Actually scratch that, I have never touched Kubernetes."

    def test_a_denial_reverses_an_entry_an_earlier_turn_upgraded(self):
        ledger = [
            _entry("Kubernetes", status="direct", claimable=True, evidence=self.UPGRADED)
        ]
        out, changed = upgrade_ledger_for_concepts(
            ledger,
            ["Kubernetes"],
            self.RETRACTION,
            denied_concepts=["Kubernetes"],
        )
        assert changed is True
        assert out[0]["status"] == "denied"
        assert out[0]["claimable"] is False
        assert out[0]["evidence"] == DENIED_EVIDENCE
        assert self.UPGRADED not in (out[0]["evidence"] or ""), (
            "the reversed entry must not keep the evidence that backed the "
            "claim the candidate has just taken back"
        )
        assert self.RETRACTION not in (out[0]["evidence"] or "")

    def test_the_in_place_seam_and_the_durable_floor_agree(self):
        """ADR-059 clause 3's actual requirement: one instrument, two seams.
        Whatever a rebuild of this row would produce, the in-place seam must
        produce too — otherwise the next ``analyze_gaps`` silently corrects
        (or contradicts) the row the writers already consumed."""
        ledger = [
            _entry("Kubernetes", status="direct", claimable=True, evidence=self.UPGRADED)
        ]
        in_place, _ = upgrade_ledger_for_concepts(
            ledger, ["Kubernetes"], self.RETRACTION, denied_concepts=["Kubernetes"]
        )
        durable = _enforce_denial_stance(ledger, ["Kubernetes"])
        for field in ("status", "claimable", "evidence"):
            assert in_place[0][field] == durable[0][field], field

    def test_a_reversal_never_touches_an_undenied_claimable_entry(self):
        """Concept-scoped, never topic-radius: an unrelated denial in the same
        turn leaves a standing upgrade standing."""
        ledger = [
            _entry("Terraform", status="direct", claimable=True, evidence="4y IaC")
        ]
        out, changed = upgrade_ledger_for_concepts(
            ledger, ["Terraform"], "no comment", denied_concepts=["Kubernetes"]
        )
        assert changed is False
        assert out[0]["status"] == "direct"
        assert out[0]["claimable"] is True
        assert out[0]["evidence"] == "4y IaC"

    def test_upgrade_false_runs_the_floor_only(self):
        """The gate #352 has to widen. A turn that applied NO ops confirmed
        nothing, so it may not upgrade — but its denial must still be able to
        reverse. ``upgrade=False`` is that seam: floors on, upgrade off."""
        ledger = [
            _entry("Kubernetes", status="direct", claimable=True, evidence=self.UPGRADED),
            _entry("Terraform"),
        ]
        out, changed = upgrade_ledger_for_concepts(
            ledger,
            ["Kubernetes", "Terraform"],
            self.RETRACTION,
            denied_concepts=["Kubernetes"],
            upgrade=False,
        )
        assert changed is True
        assert out[0]["status"] == "denied"
        assert out[0]["claimable"] is False
        # The undenied honest gap confirmed nothing and must not move.
        assert out[1]["status"] == "gap"
        assert out[1]["claimable"] is False
        assert out[1]["evidence"] == ""

    def test_upgrade_false_with_no_denial_is_a_pure_noop(self):
        ledger = [_entry("Kubernetes")]
        out, changed = upgrade_ledger_for_concepts(
            ledger, ["Kubernetes"], "some answer", upgrade=False
        )
        assert changed is False
        assert out == ledger

    def test_a_scope_entry_is_still_exempt_from_the_reversal_seam(self):
        """ADR-069 / ADR-048 (2026-08-01): a bar-carrying entry's status moves
        only via the clause-2 judgement or elicited testimony, never at this
        seam — in either direction."""
        ledger = [
            _entry(
                "team size",
                status="direct",
                claimable=True,
                evidence="12 reports",
                bar={"kind": "team_size", "value": 10, "comparator": "gte"},
            )
        ]
        out, changed = upgrade_ledger_for_concepts(
            ledger, ["team size"], self.RETRACTION, denied_concepts=["team size"]
        )
        assert changed is False
        assert out[0]["status"] == "direct"


# --- #351 — denying a compound is not denying its head noun -------------------

CSS_MIXED_ANSWER = (
    "I have plenty of plain CSS experience, though I have never used Tailwind CSS."
)
CSS_PURE_DENIAL_ANSWER = "I have never used Tailwind CSS."
CSS_VAULT = {
    "skills": [{"name": "CSS", "category": "technical", "proficiency": "advanced"}],
    "metadata": {"denied_concepts": [{"concept": "Tailwind CSS"}]},
}


class TestCompoundContainmentDoesNotDenyAnAffirmedHeadNoun:
    """#351 — the in-place upgrade seam's second floor called
    ``is_denied_concept`` with **no corpus**, so ``_is_denied``'s
    compound-containment branch fail-closed: any ledger concept that is a
    bounded substring of a denied compound was recorded as ``denied``, with
    ``DENIED_EVIDENCE`` ("Candidate explicitly stated a limit here") as its
    backing text — a statement about the candidate's testimony that the
    candidate never made, and one the letter prompt then renders verbatim
    ("THE CANDIDATE WAS ASKED AND STATED THEY DO NOT HAVE THIS",
    ``cross_document.py``). The write is terminal: floor 1 blocks any later
    upgrade and ``reevaluate_gap_ledger_against_vault`` only re-examines
    ``status == "gap"`` rows.

    ADR-064's 2026-07-29 amendment names the requirement this closes: the
    containment carve-out "must be applied consistently in all three places
    that independently re-implement 'never upgrade a denied concept' —
    ``_enforce_denial_stance``, ``upgrade_ledger_for_concepts``,
    ``reevaluate_gap_ledger_against_vault`` — or the floor becomes
    inconsistent by call path."
    """

    def test_head_noun_the_turn_itself_affirms_is_not_recorded_as_denied(self):
        """The issue's verbatim reproduction."""
        out, changed = upgrade_ledger_for_concepts(
            [_entry("CSS")],
            ["CSS"],
            CSS_MIXED_ANSWER,
            denied_concepts=["Tailwind CSS"],
        )
        assert out[0]["status"] != "denied"
        assert out[0]["evidence"] != DENIED_EVIDENCE
        # The candidate's own words affirm CSS outside the denied compound, so
        # this seam may upgrade it on that turn's evidence.
        assert out[0]["claimable"] is True
        assert out[0]["evidence"] == CSS_MIXED_ANSWER
        assert changed is True

    def test_head_noun_only_the_vault_affirms_is_left_open_never_denied(self):
        """A pure-denial turn carries no evidence FOR the head noun, so the
        seam may not upgrade it — but the vault contradicts the denial's
        reach, so it may not record a denial either. The entry is left exactly
        as it was, still healable by the corpus-aware vault re-evaluation."""
        out, changed = upgrade_ledger_for_concepts(
            [_entry("CSS")],
            ["CSS"],
            CSS_PURE_DENIAL_ANSWER,
            denied_concepts=["Tailwind CSS"],
            vault_corpus=profile_literal_corpus(CSS_VAULT),
        )
        assert out[0]["status"] == "gap"
        assert out[0]["claimable"] is False
        assert out[0]["evidence"] == ""
        assert changed is False

    def test_containment_with_no_independent_affirmation_is_floored_not_asserted(self):
        """The never-upgrade half is narrowed, not removed: with nothing
        affirming the head noun outside the denied compound — not the turn, not
        the vault — the pre-#351 fail-closed FLOOR stands.

        ADR-059 amended 2026-08-08 (#486) splits what may then be WRITTEN: the
        candidate declared "RAG pipeline", never bare "RAG", so the row is
        floored without asserting testimony about it."""
        answer = "I have never built a RAG pipeline."
        out, changed = upgrade_ledger_for_concepts(
            [_entry("RAG")],
            ["RAG"],
            answer,
            denied_concepts=["RAG pipeline"],
            vault_corpus=profile_literal_corpus({"skills": [{"name": "Python"}]}),
        )
        assert out[0]["status"] == "gap"
        assert out[0]["claimable"] is False
        assert out[0]["evidence"] == DENIAL_FLOOR_EVIDENCE
        assert out[0]["evidence"] != DENIED_EVIDENCE
        assert changed is True

    def test_a_denial_naming_the_concept_itself_is_untouched_by_the_carve_out(self):
        """The carve-out is scoped to the containment branch. A denial that
        NAMES the concept (or a broader denial the concept falls under) is the
        candidate's own declaration and stays absolute, however loudly the
        same turn talks about the concept."""
        answer = "I run Terraform daily; Kubernetes I have never touched."
        out, _ = upgrade_ledger_for_concepts(
            [_entry("Kubernetes")], ["Kubernetes"], answer,
            denied_concepts=["Kubernetes"],
        )
        assert out[0]["status"] == "denied"

        # Denial broader than the concept: "Azure" denies "Microsoft Azure".
        out, _ = upgrade_ledger_for_concepts(
            [_entry("Microsoft Azure")],
            ["Microsoft Azure"],
            "Microsoft Azure is not something I have used.",
            denied_concepts=["Azure"],
        )
        assert out[0]["status"] == "denied"

    def test_no_vault_corpus_keeps_the_pre_351_fail_closed_default(self):
        """``vault_corpus`` is optional and its absence must not loosen
        anything: with no profile on hand and a turn that only denies, the
        containment verdict is unchanged from before #351."""
        out, changed = upgrade_ledger_for_concepts(
            [_entry("CSS")], ["CSS"], CSS_PURE_DENIAL_ANSWER,
            denied_concepts=["Tailwind CSS"],
        )
        # Still floored (#351's fail-closed default), and since #486 floored
        # WITHOUT the testimony marker — "CSS" is nobody's declared term.
        assert out[0]["claimable"] is False
        assert out[0]["status"] == "gap"
        assert out[0]["evidence"] == DENIAL_FLOOR_EVIDENCE
        assert changed is True

    def test_german_compound_with_a_separate_head_noun(self):
        """Both product languages. DE compounds written as separate words
        behave exactly like the EN case."""
        answer = (
            "Ich habe zehn Jahre Fertigung verantwortet, aber keine "
            "automatisierte Fertigung."
        )
        out, _ = upgrade_ledger_for_concepts(
            [_entry("Fertigung")], ["Fertigung"], answer,
            denied_concepts=["automatisierte Fertigung"],
        )
        assert out[0]["status"] != "denied"
        assert out[0]["claimable"] is True

    def test_german_solid_compounds_are_a_separate_seam_and_stay_unchanged(self):
        """#463's seam, deliberately NOT widened into here. A German solid
        compound ("Dreischichtbetrieb") never reached its suffix
        ("Schichtbetrieb") in the first place — ``_word_present`` requires a
        word boundary — so #351 neither opens nor closes that case."""
        assert is_denied_concept("Schichtbetrieb", ["Dreischichtbetrieb"]) is False
        out, _ = upgrade_ledger_for_concepts(
            [_entry("Schichtbetrieb")],
            ["Schichtbetrieb"],
            "Ich habe keinen Dreischichtbetrieb geleitet.",
            denied_concepts=["Dreischichtbetrieb"],
        )
        assert out[0]["status"] != "denied"


class TestCompoundContainmentComposedWithTheReversalSeam:
    """#351 × #352. #352 widened WHICH entries reach floor 2 (every
    concept-matching one, claimable included, so a retraction can REVERSE a
    standing upgrade); #351 narrowed WHAT floor 2 may conclude from a
    containment-only match. Neither suite exercises the other's case, so the
    composition is pinned here.

    The load-bearing property is ADR-059 clause 3's, which is also #352's own
    stated invariant: the in-place seam must write what a rebuild of the row
    would write. Before #351 this seam judged containment corpus-blind while
    ``_enforce_denial_stance`` judged it against ``vault_corpus`` — so on the
    CSS/Tailwind-CSS shape the two disagreed even after #352.
    """

    UPGRADED = "I have written plain CSS by hand for a decade."
    RETRACTION = "Actually scratch that, I have never used Tailwind CSS."

    def _claimable_css(self):
        return [_entry("CSS", status="direct", claimable=True, evidence=self.UPGRADED)]

    def test_a_containment_only_denial_does_not_reverse_a_standing_upgrade(self):
        """The retraction names the compound. CSS was earned on its own
        evidence and the vault still carries it, so nothing is taken back."""
        out, changed = upgrade_ledger_for_concepts(
            self._claimable_css(),
            ["CSS"],
            self.RETRACTION,
            denied_concepts=["Tailwind CSS"],
            upgrade=False,
            vault_corpus=profile_literal_corpus(CSS_VAULT),
        )
        assert changed is False
        assert out[0]["status"] == "direct"
        assert out[0]["claimable"] is True
        assert out[0]["evidence"] == self.UPGRADED

    def test_the_composed_seam_and_the_durable_floor_still_agree(self):
        """#352's `test_the_in_place_seam_and_the_durable_floor_agree`, on the
        containment shape it does not cover. Both instruments now judge
        containment against the SAME vault corpus, so they cannot diverge."""
        ledger = self._claimable_css()
        corpus = profile_literal_corpus(CSS_VAULT)
        in_place, _ = upgrade_ledger_for_concepts(
            ledger, ["CSS"], self.RETRACTION,
            denied_concepts=["Tailwind CSS"], upgrade=False, vault_corpus=corpus,
        )
        durable = _enforce_denial_stance(ledger, ["Tailwind CSS"], corpus)
        for field in ("status", "claimable", "evidence"):
            assert in_place[0][field] == durable[0][field], field

    def test_a_containment_only_denial_the_vault_contradicts_nowhere_reverses(self):
        """Fail-closed direction preserved: with no independent evidence for
        the head noun anywhere, the retraction reverses exactly as #352
        requires."""
        out, changed = upgrade_ledger_for_concepts(
            self._claimable_css(),
            ["CSS"],
            self.RETRACTION,
            denied_concepts=["Tailwind CSS"],
            upgrade=False,
            vault_corpus=profile_literal_corpus({"skills": [{"name": "Python"}]}),
        )
        assert changed is True
        assert out[0]["claimable"] is False
        # #486: the reversal is a FLOOR, not a fabricated statement about
        # testimony — the retraction named the compound, not the head noun.
        assert out[0]["status"] == "gap"
        assert out[0]["evidence"] == DENIAL_FLOOR_EVIDENCE

    def test_a_declared_denial_still_reverses_through_the_carve_out(self):
        """The carve-out must not become an escape hatch for #352: a denial
        that NAMES the concept reverses whatever either corpus says."""
        out, changed = upgrade_ledger_for_concepts(
            self._claimable_css(),
            ["CSS"],
            "Actually scratch that, I have never used CSS.",
            denied_concepts=["CSS"],
            upgrade=False,
            vault_corpus=profile_literal_corpus(CSS_VAULT),
        )
        assert changed is True
        assert out[0]["status"] == "denied"
        assert out[0]["claimable"] is False

    def test_upgrade_false_never_promotes_a_turn_affirmed_head_noun(self):
        """The two gates are independent. A turn that affirms CSS outside the
        denied compound clears #351's carve-out — and still may not be
        promoted when the turn applied no ops (#352's ``upgrade=False``)."""
        out, changed = upgrade_ledger_for_concepts(
            [_entry("CSS")],
            ["CSS"],
            CSS_MIXED_ANSWER,
            denied_concepts=["Tailwind CSS"],
            upgrade=False,
        )
        assert changed is False
        assert out[0]["status"] == "gap"
        assert out[0]["claimable"] is False
        assert out[0]["evidence"] == ""
