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
    DENIED_EVIDENCE,
    _enforce_denial_stance,
    build_keyword_ledger,
    upgrade_ledger_for_concepts,
)

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
