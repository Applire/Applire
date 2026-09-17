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

"""#684 (epic #683) — one seam test per Statement door for the fill-only
summary (ADR-061 amended 2026-09-08).

``test_684_summary_positioning_seed.py`` pins ``_apply_set_summary``'s
intake-scoped drop with exactly ONE Statement source exercised
(``source="interview"``). The refuter pass in
``Documents/Runs/Nougat/close-out/refute-v.md`` §2 found the other two members
of ``_STATEMENT_SOURCES`` — ``"agent_interview"`` and ``"testimony"`` — had no
committed regression test of their own: the mechanism is correct by code
inspection (three string literals compared against ``source`` in
``apply.py::_STATEMENT_SOURCES``), but a future edit to either bridge's
``_SOURCE`` constant, or to the frozenset itself, would not be caught by CI.
Only a live, uncommitted door-probe script (``Documents/Runs/Nougat/build-1/v/
door-probe.json``) had ever exercised the agent/testimony doors' own code
path.

Two things are pinned here:

1. The applier's drop path, parametrized over all three ``_STATEMENT_SOURCES``
   members directly against ``apply_ops`` (the existing test's shape, widened).
2. One seam test per agent door — ``testimony_bridge.submit_testimony`` and
   ``agent_bridge.submit_agent_claims`` — driving the REAL door function (not
   ``apply_ops`` directly) with a populated ``professional_summary`` and a
   queued reconcile payload carrying a differing ``set_summary`` op, asserting
   the vault slot is unchanged, the persisted ``not_applied`` receipt carries
   ``reason="summary_populated"``, and no ``Conflict`` is parked.

The two receipt channels named ``not_applied`` must not be confused: the
DOOR's ``TestimonyResult.not_applied`` / (absent on) ``ClaimResult`` is the
witness's figure/op-rejection channel (``witness.compute_not_applied``,
schemas/testimony.py's ``NotApplied``) — a SEPARATE mechanism from the
applier's own ``ImportNotApplied`` receipt (``reason="summary_populated"``)
that ``_apply_set_summary`` appends. The applier's receipt rides into
``EnrichmentRecord.not_applied`` (``commit.py``), which is not re-exposed on
either door's wire result — it is asserted here straight off the persisted
``profile_json["metadata"]["enrichment_history"]``, exactly as the existing
``test_agent_claims.py``/``test_testimony_bridge.py`` read other receipt
channels.
"""
from __future__ import annotations

from typing import Any

import pytest

from applire.schemas.claims import ClaimItem, ClaimsSubmission
from applire.schemas.profile import MasterProfileData, ProfessionalSummary
from applire.services.profile.reconcile.agent_bridge import submit_agent_claims
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import SetSummary
from applire.services.profile.reconcile.testimony_bridge import submit_testimony
from tests.support.profile_factory import make_master_profile

# The three Statement intakes (ADR-063 §5.3.19a's INTAKE axis, ADR-058 door
# parity: `interview`/`agent_interview` are the same act on the REST/MCP
# channels, `testimony` is the pasted-dossier door).
STATEMENT_SOURCES = ["interview", "agent_interview", "testimony"]

_ORIGINAL_SUMMARY = "Erfahrener Entwickler mit 10 Jahren."
_DIFFERING_SUMMARY = "Fifteen years leading pharmaceutical manufacturing IT."


class _QueueProvider:
    """Returns one queued reconcile payload per call; records every prompt.

    Same shape as the ``_QueueProvider`` in ``test_testimony_bridge.py`` /
    ``test_agent_claims.py`` — kept local rather than imported so this file
    stays self-contained (matches this test tree's own convention)."""

    def __init__(self, payloads: list[Any]) -> None:
        self.payloads = list(payloads)
        self.prompts: list[str] = []

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        self.prompts.append(prompt)
        item = self.payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _summary_payload(text: str) -> dict:
    return {
        "ops": [{"op": "set_summary", "lang": "en", "text": text}],
        "ambiguities": [],
        "denials": [],
    }


async def _seed_profile_with_summary(db):
    """A profile whose ``professional_summary.en`` slot is already populated —
    the shape ``_apply_set_summary`` gates on."""
    record = make_master_profile(
        profile_json={
            "personal_info": {"full_name": "Lena Fischer"},
            "professional_summary": {"en": _ORIGINAL_SUMMARY},
            "metadata": {
                "completeness_score": 0.5,
                "created_via": "cv_upload",
                "created_at": "2026-01-01T00:00:00Z",
                "last_updated": "2026-01-01T00:00:00Z",
            },
        }
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


# ── 1. The applier's drop path, over all three Statement sources ──────────────


@pytest.mark.parametrize("source", STATEMENT_SOURCES)
def test_a_statement_intake_drops_a_differing_summary_regardless_of_which_door(source):
    """Widens ``test_684_summary_positioning_seed.py``'s single-source pin
    (``source="interview"`` only) to all three ``_STATEMENT_SOURCES``
    members. Each must drop a differing write onto a populated slot with a
    ``not_applied`` receipt (``reason="summary_populated"``) and raise no
    conflict — the mechanism `_STATEMENT_SOURCES` names by literal string
    comparison, exercised for every literal it names."""
    profile = MasterProfileData(
        professional_summary=ProfessionalSummary(en=_ORIGINAL_SUMMARY)
    )
    ops = [SetSummary(lang="en", text=_DIFFERING_SUMMARY)]

    result = apply_ops(profile, ops, source)

    assert result.profile.professional_summary.en == _ORIGINAL_SUMMARY
    assert result.conflicts == []
    assert len(result.not_applied) == 1
    receipt = result.not_applied[0]
    assert receipt.section == "professional_summary"
    assert receipt.label == "en"
    assert receipt.reason == "summary_populated"
    assert not [c for c in result.changes if c.section == "professional_summary"]


# ── 2. One seam test per agent door ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_testimony_door_drops_a_differing_summary_and_receipts_it(async_db):
    """The MCP/UI ``submit_testimony`` door (``_SOURCE = "testimony"``) drives
    the REAL door path — reconcile → commit_ops → apply_ops — not
    ``apply_ops`` called directly. Pins that ``testimony_bridge.py``'s own
    ``_SOURCE`` constant is (and stays) a member of ``_STATEMENT_SOURCES``:
    a differing ``set_summary`` against the populated slot above is dropped,
    not disputed."""
    record = await _seed_profile_with_summary(async_db)
    provider = _QueueProvider([_summary_payload(_DIFFERING_SUMMARY)])

    result = await submit_testimony(
        "Also, I've always been drawn to hands-on manufacturing systems work.",
        async_db,
        provider,
    )

    # No conflict parked on the door's own wire result.
    assert result.conflicts == []
    assert result.confirmations == []
    # The witness's OWN not_applied channel (figure/op-rejection) is a
    # different mechanism — see module docstring — and stays empty here since
    # the queued op parsed cleanly and the testimony text carries no figures.
    assert result.not_applied == []
    assert result.status == "no_change"

    await async_db.refresh(record)
    assert record.profile_json["professional_summary"]["en"] == _ORIGINAL_SUMMARY
    history = record.profile_json["metadata"]["enrichment_history"]
    assert history[-1]["source"] == "testimony"
    assert not [c for c in history[-1]["changes"] if c.get("section") == "professional_summary"]
    receipts = [
        n for n in history[-1]["not_applied"] if n["reason"] == "summary_populated"
    ]
    assert len(receipts) == 1
    assert receipts[0]["section"] == "professional_summary"
    assert receipts[0]["label"] == "en"
    assert record.profile_json["metadata"]["pending_conflicts"] == []


@pytest.mark.asyncio
async def test_the_agent_claims_door_drops_a_differing_summary_and_receipts_it(async_db):
    """The MCP ``submit_claims`` door (``agent_bridge._SOURCE =
    "agent_interview"``) drives the REAL door path. Pins that
    ``agent_bridge.py``'s own ``_SOURCE`` constant is (and stays) a member of
    ``_STATEMENT_SOURCES``: a differing ``set_summary`` elicited as one claim
    against the populated slot above is dropped, not disputed."""
    record = await _seed_profile_with_summary(async_db)
    provider = _QueueProvider([_summary_payload(_DIFFERING_SUMMARY)])

    result = await submit_agent_claims(
        ClaimsSubmission(
            claims=[
                ClaimItem(
                    statement=(
                        "I've always been drawn to hands-on manufacturing "
                        "systems work."
                    )
                )
            ]
        ),
        None,
        async_db,
        provider,
    )

    claim_result = result.results[0]
    assert claim_result.status == "no_change"
    assert claim_result.conflicts == []
    assert claim_result.confirmations == []
    assert not [c for c in claim_result.changes if c.section == "professional_summary"]

    await async_db.refresh(record)
    assert record.profile_json["professional_summary"]["en"] == _ORIGINAL_SUMMARY
    history = record.profile_json["metadata"]["enrichment_history"]
    assert history[-1]["source"] == "agent_interview"
    receipts = [
        n for n in history[-1]["not_applied"] if n["reason"] == "summary_populated"
    ]
    assert len(receipts) == 1
    assert receipts[0]["section"] == "professional_summary"
    assert receipts[0]["label"] == "en"
    assert record.profile_json["metadata"]["pending_conflicts"] == []
