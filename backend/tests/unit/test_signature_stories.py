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

"""E046 / ADR-055 — SignatureStory: schema, reconciler op, apply, vault index.

The vault gains the evidence class the schema used to flatten (vision v2 §3.4):
challenge → mechanism → outcome → benchmark, anchored to experiences via
``experience_refs``. Written ONLY through the ADR-046 reconciler
(``upsert_story``): normalized-title match → fill-empties, else APPEND —
deliberately NO RequestConfirmation in v1 (both resolution channels are
skill-shaped; a parked story would be silently dropped, a visible duplicate is
recoverable). Receipts carry the FULL story prose in ``FieldChange.new_value``
so the Oracle's receipt matching (blob containment) can attach record ids to
story evidence units.
"""
from __future__ import annotations

from datetime import datetime, timezone

from applire.schemas.profile import (
    EnrichmentRecord,
    FieldChange,
    MasterProfileData,
    ProfileMetadata,
    SignatureStory,
    WorkEntry,
)
from applire.services.profile import _VALID_SECTIONS
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import _parse_ops
from applire.services.profile.reconcile.ops import UpsertStory
from applire.services.profile.reconcile.stance import enforce_stance
from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT
from applire.services.oracle.matchers.vault import build_vault_index
from applire.services.profile.reconcile.import_bridge import (
    _BATCH_SECTION_GROUPS,
    _slice_incoming,
)

STORY = dict(
    title="SAP cutover rescue",
    challenge="The SAP migration was six weeks from a failed go-live.",
    mechanism="Rebuilt the interface layer around an event queue and ran daily cutover rehearsals.",
    outcome="Cutover succeeded with 30% less downtime than the previous attempt.",
    benchmark="The prior year's attempt was rolled back after 14 hours.",
)


def _profile(**kwargs) -> MasterProfileData:
    return MasterProfileData(
        work_experience=[WorkEntry(id="w-1", company="Acme GmbH", role="Backend Engineer")],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# US257 — schema
# ---------------------------------------------------------------------------


def test_signature_story_defaults():
    s = SignatureStory(**STORY)
    assert s.id  # generated uuid
    assert s.benchmark == STORY["benchmark"]
    assert s.experience_refs == []
    assert s.source is None


def test_profile_round_trips_stories():
    p = _profile(signature_stories=[SignatureStory(**STORY, experience_refs=["w-1"])])
    dumped = p.model_dump(mode="json")
    reloaded = MasterProfileData.model_validate(dumped)
    assert reloaded.signature_stories[0].title == STORY["title"]
    assert reloaded.signature_stories[0].experience_refs == ["w-1"]


def test_legacy_profile_without_key_loads_empty():
    reloaded = MasterProfileData.model_validate({"work_experience": []})
    assert reloaded.signature_stories == []


def test_signature_stories_is_a_patchable_section():
    assert "signature_stories" in _VALID_SECTIONS


# ---------------------------------------------------------------------------
# US258 — op parsing, prompt vocabulary, apply semantics
# ---------------------------------------------------------------------------


def test_parse_ops_accepts_upsert_story_and_drops_invalid():
    ops = _parse_ops(
        [
            {"op": "upsert_story", **STORY, "evidence": ["w-1"]},
            {"op": "upsert_story"},  # missing required fields -> dropped
            {"op": "upsert_skill", "name": "Python"},
        ]
    )
    kinds = [o.op for o in ops]
    assert kinds == ["upsert_story", "upsert_skill"]
    assert ops[0].evidence == ["w-1"]


def test_prompt_vocabulary_teaches_upsert_story():
    assert "upsert_story" in RECONCILE_SYSTEM_PROMPT


def test_apply_appends_new_story_with_full_prose_receipt():
    p = _profile()
    result = apply_ops(p, [UpsertStory(**STORY, evidence=["w-1"])], "interview")
    stories = result.profile.signature_stories
    assert len(stories) == 1
    assert stories[0].id
    assert stories[0].experience_refs == ["w-1"]
    assert stories[0].source == "interview"
    (change,) = [c for c in result.changes if c.section == "signature_stories"]
    # Receipt rule (adversarial M4): new_value carries the FULL prose so the
    # Oracle's blob matching can attach this record to story evidence units.
    assert isinstance(change.new_value, dict)
    assert change.new_value["outcome"] == STORY["outcome"]
    assert change.new_value["mechanism"] == STORY["mechanism"]


def test_apply_title_match_fills_empty_benchmark_only():
    existing = SignatureStory(**{**STORY, "benchmark": None})
    p = _profile(signature_stories=[existing])
    op = UpsertStory(
        title="sap cutover RESCUE",  # normalized-title identity
        challenge="A different retelling that must NOT overwrite.",
        mechanism="Also different.",
        outcome="Also different.",
        benchmark="The prior year's attempt was rolled back after 14 hours.",
    )
    result = apply_ops(p, [op], "interview")
    stories = result.profile.signature_stories
    assert len(stories) == 1  # merged, not appended
    assert stories[0].challenge == STORY["challenge"]  # non-empty prose untouched
    assert stories[0].benchmark == op.benchmark  # gap filled
    assert result.pending_confirmations == []


def test_apply_near_dupe_title_appends_never_confirms():
    p = _profile(signature_stories=[SignatureStory(**STORY)])
    op = UpsertStory(
        title="SAP cutover",  # containment near-dupe, NOT equality
        challenge="c",
        mechanism="m",
        outcome="o",
    )
    result = apply_ops(p, [op], "interview")
    # v1 posture (adversarial M3): append-over-confirm — both confirmation
    # resolution channels are skill-shaped dead ends.
    assert len(result.profile.signature_stories) == 2
    assert result.pending_confirmations == []


def test_apply_does_not_mutate_input_profile():
    p = _profile()
    apply_ops(p, [UpsertStory(**STORY)], "interview")
    assert p.signature_stories == []


def test_stance_guards_stories_but_never_token_grounds_their_prose():
    """US261 superseded the E046 posture ("entity upserts are outside stance
    scope"): stories are now guarded — denials over the prose plus figure
    grounding on outcome/benchmark (both corpus-bearing sources, PO ruling
    2026-07-19). Prose itself stays paraphrasable; a story whose figures ARE
    in the turn passes while a denied skill op still dies."""
    from applire.services.profile.reconcile.ops import UpsertSkill

    story_op = UpsertStory(**STORY)
    skill_op = UpsertSkill(name="Kubernetes")
    turn = {
        "answer": (
            "I have never used Kubernetes. The cutover succeeded with 30% "
            "less downtime; the prior year's attempt was rolled back after "
            "14 hours."
        )
    }
    kept = enforce_stance(
        [story_op, skill_op], denials=["Kubernetes"], new_info=turn, source="interview"
    )
    assert story_op in kept
    assert skill_op not in kept
    # An ungrounded outcome figure now drops the story (ADR-055 gap closed).
    assert (
        enforce_stance(
            [story_op],
            denials=[],
            new_info={"answer": "I have never used Kubernetes."},
            source="interview",
        )
        == []
    )


# ---------------------------------------------------------------------------
# US258 — segmented-import slicing must not drop stories (latent-gap close)
# ---------------------------------------------------------------------------


def test_batch_section_groups_cover_signature_stories():
    covered = {name for group in _BATCH_SECTION_GROUPS for name in group}
    assert "signature_stories" in covered


def test_slice_incoming_carries_stories():
    incoming = MasterProfileData(signature_stories=[SignatureStory(**STORY)])
    slices = _slice_incoming(incoming)
    assert any(s.signature_stories for s in slices)


# ---------------------------------------------------------------------------
# US259 — Oracle vault index
# ---------------------------------------------------------------------------


def test_vault_index_contains_story_units_with_figures():
    p = _profile(signature_stories=[SignatureStory(**STORY)])
    index = build_vault_index(p)
    outcome_units = [u for u in index.units if u.path == "signature_stories[0].outcome"]
    assert len(outcome_units) == 1
    assert outcome_units[0].figures, "the 30% figure must be extracted from the outcome"
    # every prose field is a unit
    paths = {u.path for u in index.units}
    assert {"signature_stories[0].title", "signature_stories[0].challenge",
            "signature_stories[0].mechanism", "signature_stories[0].benchmark"} <= paths


def test_vault_index_attaches_receipts_to_story_units():
    story = SignatureStory(**STORY)
    rec = EnrichmentRecord(
        timestamp=datetime.now(timezone.utc),
        source="interview",
        changes=[
            FieldChange(
                section="signature_stories",
                field=story.title,
                action="added",
                new_value=story.model_dump(mode="json"),
            )
        ],
    )
    p = _profile(
        signature_stories=[story],
        metadata=ProfileMetadata(enrichment_history=[rec]),
    )
    index = build_vault_index(p)
    outcome_unit = next(u for u in index.units if u.path == "signature_stories[0].outcome")
    assert rec.id in outcome_unit.receipt_ids
