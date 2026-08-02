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

"""ADR-071 clause 3 — the Oracle's ``misattributed`` verdict gains a
generation-side consumer.

The verdict itself is not new and is not broken. ``extract_claims_from_tailored``
stamps every bullet claim with the id of the position it is rendered under
(US187), ``_attribution_red_flag`` compares that against ``EvidenceUnit.owner_ids``,
and the whole thing is deterministic, id-anchored and runs on every CV
generation. The frontend has shown the red chip since 2026-07-19. What was
missing is that **nothing acted on a correct answer** — while the letter chain
has had exactly such a consumer (``guard_letter_figures``) all along.

The remedy is deliberately narrow, and each boundary is a test below:

* **not a strip** — deleting the bullet destroys the candidate's true evidence,
  and this project has measured that harm twice (#347: blanket denials wrong
  3/3, always toward destroying real evidence; #377: the cap deleted the CV's
  most quantified achievement). Only the writer can re-place a fact.
* **not a gate** — ADR-052 §5 and ADR-060's PO decision 2 stand: deliver the
  best document we have. A document that ships still-misattributed ships.
* **at most one round** — exhaustion is logged, not retried into the ground.
"""
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.attribution_round import (  # noqa: E402
    build_attribution_feedback,
    misattributed_findings,
    run_attribution_round,
)

SAP_BULLET = (
    "Tägliche Arbeit mit SAP PP und MM (Disposition und "
    "Bestellanforderungen für Instandhaltungsmaterial)"
)

PROFILE = {
    "work_experience": [
        {"id": "weberit", "company": "Weberit GmbH", "role": "Produktionsleiter",
         "start_date": "2017-04", "end_date": None, "is_current": True,
         "responsibilities": [SAP_BULLET], "achievements": []},
        {"id": "rasselstein", "company": "Rasselstein", "role": "Schichtleiter",
         "start_date": "2011-08", "end_date": "2017-03",
         "responsibilities": ["Schichtführung im Walzwerk"], "achievements": []},
    ]
}


def _report(**overrides) -> dict:
    """A truthfulness report carrying the #413 shape: the Weberit-owned SAP
    fact rendered under Rasselstein."""
    base = {
        "document_kind": "cv",
        "counts": {"grounded": 5, "misattributed": 1},
        "claims": [
            {"claim": {"text": "Schichtführung im Walzwerk",
                       "location": "work_history[1].bullets[0]", "kind": "bullet",
                       "source_experience_id": "rasselstein"},
             "verdict": {"verdict": "grounded", "checker": "deterministic",
                         "evidence": []}},
            {"claim": {"text": SAP_BULLET,
                       "location": "work_history[1].bullets[1]", "kind": "bullet",
                       "source_experience_id": "rasselstein"},
             "verdict": {"verdict": "misattributed", "checker": "deterministic",
                         "evidence": [{"kind": "profile_path",
                                       "ref": "work_experience[0].responsibilities[0]",
                                       "excerpt": SAP_BULLET}],
                         "detail": "evidence owned by another position"}},
        ],
    }
    base.update(overrides)
    return base


DRAFT = {
    "summary": "Produktionsleiter mit Erfahrung in der Fertigung.",
    "work": [
        {"id": "weberit", "bullets": ["Leitung der Fertigung"]},
        {"id": "rasselstein", "bullets": ["Schichtführung im Walzwerk", SAP_BULLET]},
    ],
    "skills": ["SAP PP"],
}


# --- finding the claims -----------------------------------------------------


class TestMisattributedFindings:
    def test_picks_out_only_the_misattributed_verdicts(self):
        found = misattributed_findings(_report())
        assert len(found) == 1
        assert found[0].text == SAP_BULLET
        assert found[0].rendered_under_id == "rasselstein"
        assert found[0].owner_ref == "work_experience[0].responsibilities[0]"

    def test_an_absent_or_malformed_report_yields_nothing(self):
        """Fail open in every direction — this consumer may never be the reason
        a generation fails."""
        assert misattributed_findings(None) == []
        assert misattributed_findings({}) == []
        assert misattributed_findings({"claims": "not a list"}) == []
        assert misattributed_findings({"claims": [{"verdict": None}]}) == []

    def test_a_clean_report_yields_nothing(self):
        clean = _report(claims=[
            {"claim": {"text": "x", "location": "work_history[0].bullets[0]"},
             "verdict": {"verdict": "grounded", "checker": "deterministic"}},
        ])
        assert misattributed_findings(clean) == []

    def test_a_finding_with_no_owning_evidence_is_still_reported(self):
        """The verdict is what triggers the round; a missing ``EvidenceRef``
        only makes the feedback vaguer. Dropping the finding would let the
        weaker case ship unremediated."""
        vague = _report(claims=[
            {"claim": {"text": SAP_BULLET, "location": "work_history[1].bullets[1]",
                       "source_experience_id": "rasselstein"},
             "verdict": {"verdict": "misattributed", "checker": "deterministic",
                         "evidence": []}},
        ])
        found = misattributed_findings(vague)
        assert len(found) == 1
        assert found[0].owner_ref is None


# --- the feedback the writer actually receives ------------------------------


class TestAttributionFeedback:
    def test_names_the_claim_the_rendered_position_and_the_owner(self):
        fb = build_attribution_feedback(misattributed_findings(_report()), PROFILE)
        assert "Rasselstein" in fb
        assert "Weberit GmbH" in fb
        assert SAP_BULLET in fb

    def test_asks_for_relocation_and_never_for_deletion(self):
        """The instruction must not read as "remove it". #347 measured what
        happens when a remedy defaults toward destroying real evidence."""
        fb = build_attribution_feedback(misattributed_findings(_report()), PROFILE).lower()
        assert "move" in fb or "under the entry that owns it" in fb
        assert "delete" not in fb

    def test_quotes_the_document_not_the_vault(self):
        """ADR-021 amended 2026-06-29: feedback is referential. The claim text
        is the DOCUMENT's own words — quoting it identifies the bullet. Vault
        evidence is never pasted; the corrector re-reads the profile."""
        fb = build_attribution_feedback(misattributed_findings(_report()), PROFILE)
        assert "Schichtführung im Walzwerk" not in fb

    def test_falls_back_to_the_entry_id_when_a_position_cannot_be_named(self):
        fb = build_attribution_feedback(misattributed_findings(_report()), {})
        assert "rasselstein" in fb
        assert SAP_BULLET in fb

    def test_no_findings_yields_no_feedback(self):
        assert build_attribution_feedback([], PROFILE) == ""


# --- the round itself -------------------------------------------------------


@pytest.mark.asyncio
class TestRunAttributionRound:
    async def _provider(self, returns):
        provider = AsyncMock()
        provider.aparse_json = AsyncMock(return_value=returns)
        return provider

    async def test_a_clean_report_makes_no_llm_call_at_all(self):
        provider = await self._provider(DRAFT)
        out = await run_attribution_round(
            DRAFT, report=_report(claims=[]), profile_json=PROFILE,
            source_material="{}", provider=provider,
        )
        assert out is DRAFT
        provider.aparse_json.assert_not_awaited()

    async def test_the_corrected_draft_replaces_the_original(self):
        fixed = {
            "summary": DRAFT["summary"],
            "work": [
                {"id": "weberit", "bullets": ["Leitung der Fertigung", SAP_BULLET]},
                {"id": "rasselstein", "bullets": ["Schichtführung im Walzwerk"]},
            ],
            "skills": ["SAP PP"],
        }
        provider = await self._provider(fixed)
        out = await run_attribution_round(
            DRAFT, report=_report(), profile_json=PROFILE,
            source_material="{}", provider=provider,
        )
        assert out == fixed
        assert provider.aparse_json.await_count == 1

    async def test_exactly_one_round_however_many_claims_are_flagged(self):
        """The hard cap is on ROUNDS, not on findings — all of them go into one
        feedback string."""
        two = _report(claims=_report()["claims"] * 2)
        provider = await self._provider(DRAFT)
        await run_attribution_round(
            DRAFT, report=two, profile_json=PROFILE,
            source_material="{}", provider=provider,
        )
        assert provider.aparse_json.await_count == 1

    async def test_a_draft_that_loses_a_work_entry_id_is_rejected(self):
        """The correction round is still an LLM re-emission of the whole prose
        object — the #303/GxP custody class. A round that drops an entry is
        worse than the misattribution it was fixing, so the original ships."""
        lossy = {"summary": DRAFT["summary"],
                 "work": [{"id": "weberit", "bullets": ["Leitung der Fertigung"]}],
                 "skills": []}
        provider = await self._provider(lossy)
        out = await run_attribution_round(
            DRAFT, report=_report(), profile_json=PROFILE,
            source_material="{}", provider=provider,
        )
        assert out is DRAFT

    async def test_a_draft_that_empties_its_bullets_is_rejected(self):
        """The id set surviving is not evidence the CONTENT did. A corrector
        that keeps every entry and empties them passes an id-only check while
        destroying the whole document — found by this branch's adversarial
        pass, 2026-08-02."""
        gutted = {
            "summary": DRAFT["summary"],
            "work": [{"id": "weberit", "bullets": []},
                     {"id": "rasselstein", "bullets": []}],
            "skills": ["SAP PP"],
        }
        provider = await self._provider(gutted)
        out = await run_attribution_round(
            DRAFT, report=_report(), profile_json=PROFILE,
            source_material="{}", provider=provider,
        )
        assert out is DRAFT

    async def test_dropping_exactly_one_misplaced_detail_is_allowed(self):
        """The floor is one bullet per finding, not zero: the feedback permits
        dropping a misplaced detail when the entry that owns it is not in this
        CV. One finding, one bullet fewer — accepted."""
        trimmed = {
            "summary": DRAFT["summary"],
            "work": [{"id": "weberit", "bullets": ["Leitung der Fertigung"]},
                     {"id": "rasselstein", "bullets": ["Schichtführung im Walzwerk"]}],
            "skills": ["SAP PP"],
        }
        provider = await self._provider(trimmed)
        out = await run_attribution_round(
            DRAFT, report=_report(), profile_json=PROFILE,
            source_material="{}", provider=provider,
        )
        assert out == trimmed

    async def test_a_relocation_that_keeps_the_count_is_allowed(self):
        moved = {
            "summary": DRAFT["summary"],
            "work": [{"id": "weberit", "bullets": ["Leitung der Fertigung", SAP_BULLET]},
                     {"id": "rasselstein", "bullets": ["Schichtführung im Walzwerk"]}],
            "skills": ["SAP PP"],
        }
        provider = await self._provider(moved)
        out = await run_attribution_round(
            DRAFT, report=_report(), profile_json=PROFILE,
            source_material="{}", provider=provider,
        )
        assert out == moved

    async def test_a_draft_that_empties_the_summary_is_rejected(self):
        """This round never asks about the summary, so losing it is collateral
        damage from a full re-emission — the #303/GxP custody class."""
        no_summary = {
            "summary": "",
            "work": [{"id": "weberit", "bullets": ["Leitung der Fertigung", SAP_BULLET]},
                     {"id": "rasselstein", "bullets": ["Schichtführung im Walzwerk"]}],
            "skills": ["SAP PP"],
        }
        provider = await self._provider(no_summary)
        out = await run_attribution_round(
            DRAFT, report=_report(), profile_json=PROFILE,
            source_material="{}", provider=provider,
        )
        assert out is DRAFT

    async def test_a_provider_failure_ships_the_original_draft(self):
        provider = AsyncMock()
        provider.aparse_json = AsyncMock(side_effect=RuntimeError("boom"))
        out = await run_attribution_round(
            DRAFT, report=_report(), profile_json=PROFILE,
            source_material="{}", provider=provider,
        )
        assert out is DRAFT

    async def test_a_malformed_response_ships_the_original_draft(self):
        provider = await self._provider(["not", "an", "object"])
        out = await run_attribution_round(
            DRAFT, report=_report(), profile_json=PROFILE,
            source_material="{}", provider=provider,
        )
        assert out is DRAFT

    async def test_the_round_is_logged_when_it_runs(self, caplog):
        provider = await self._provider(DRAFT)
        with caplog.at_level(logging.INFO, logger="applire.services.attribution_round"):
            await run_attribution_round(
                DRAFT, report=_report(), profile_json=PROFILE,
                source_material="{}", provider=provider,
            )
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "ATTRIBUTION_ROUND" in messages
        assert "rasselstein" in messages

    async def test_an_unchanged_draft_logs_exhaustion(self, caplog):
        """"The document ships and the red flag is what the human sees" is only
        acceptable if the failure is visible in the log too (ADR-021/#264
        exhaustion precedent)."""
        provider = await self._provider(DRAFT)
        with caplog.at_level(logging.INFO, logger="applire.services.attribution_round"):
            await run_attribution_round(
                DRAFT, report=_report(), profile_json=PROFILE,
                source_material="{}", provider=provider,
            )
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "EXHAUSTED" in messages

    async def test_nothing_here_can_block_delivery(self):
        """ADR-052 §5 / ADR-060 PO decision 2, asserted structurally: every
        failure path above returns a usable draft rather than raising."""
        for provider in (
            await self._provider(None),
            await self._provider({}),
            await self._provider({"work": []}),
        ):
            out = await run_attribution_round(
                DRAFT, report=_report(), profile_json=PROFILE,
                source_material="{}", provider=provider,
            )
            assert isinstance(out, dict) and out["work"]
