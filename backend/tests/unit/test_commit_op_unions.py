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

"""ADR-063 amended 2026-08-09 clause 1 / #480 PR 1 — the two op unions, split
by EMITTER, and the live hole the split closes.

`DemoteSkill` shipped inside `ReconcileOp` (#485) while its own docstring says
the reconciler LLM never emits it — and `engine._parse_ops` validates *raw model
JSON* against that union. So a hallucinated ``{"op": "demote_skill", "name":
"Kubernetes"}`` in the model's output passed validation and demoted a real,
attested vault skill to ``denied``: a negative statement about the candidate
that nobody testified to. Proposed FMEA row SF-VAULT.10.

The split:

* ``ReconcileOp``  — the union raw model output is validated against. No
  ``DemoteSkill``.
* ``DecisionOp``   — adapter-only ops, constructed as typed objects by
  deterministic code. Today: ``DemoteSkill``.
* ``CommitOp``     — ``ReconcileOp | DecisionOp``; what ``apply_ops`` and
  ``commit_ops`` accept.

Governing rule (ADR-063 amended, clause 1): *never widen an op the model can
emit with a more powerful parameter* — and never leave an adapter-only op in
the model's own vocabulary.
"""
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from applire.services.profile.reconcile.ops import (
    CommitOp,
    DemoteSkill,
    ReconcileOp,
    UpsertSkill,
)

_HALLUCINATED_DEMOTE: dict[str, Any] = {
    "op": "demote_skill",
    "name": "Kubernetes",
    "declared_denial": "Kubernetes",
}


class _Provider:
    """Returns one canned reconcile payload; no network, no credits."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        return self.payload


def _profile_with_confirmed_skill(name: str = "Kubernetes"):
    from applire.schemas.profile import MasterProfileData

    return MasterProfileData.model_validate(
        {
            "personal_info": {"full_name": "Daniel Kovač"},
            "skills": [{"name": name, "category": "technical", "status": "confirmed"}],
            "metadata": {},
        }
    )


# ── The unions themselves ─────────────────────────────────────────────────────


def test_reconcile_op_union_refuses_demote_skill():
    """The model-emittable union no longer carries the adapter-only op."""
    adapter: TypeAdapter = TypeAdapter(ReconcileOp)
    with pytest.raises(ValidationError):
        adapter.validate_python(_HALLUCINATED_DEMOTE)


def test_commit_op_union_accepts_demote_skill():
    """The committer's union still carries it — deterministic emission is the
    only way in, and it must survive the trip to ``apply_ops``."""
    adapter: TypeAdapter = TypeAdapter(CommitOp)
    parsed = adapter.validate_python(_HALLUCINATED_DEMOTE)
    assert isinstance(parsed, DemoteSkill)


def test_commit_op_union_still_accepts_every_model_emittable_op():
    """The split is behaviour-identical for all other ops."""
    adapter: TypeAdapter = TypeAdapter(CommitOp)
    reconcile_adapter: TypeAdapter = TypeAdapter(ReconcileOp)
    samples: list[dict[str, Any]] = [
        {"op": "upsert_work", "ref": "w1", "company": "X", "role": "Y"},
        {"op": "upsert_project", "ref": "p1", "name": "N"},
        {"op": "upsert_volunteer", "ref": "v1", "organization": "O", "role": "R"},
        {"op": "add_bullets", "target": "w1"},
        {"op": "upsert_skill", "name": "Python"},
        {"op": "upsert_certification", "name": "AWS"},
        {"op": "upsert_language", "language": "German"},
        {"op": "upsert_education", "institution": "TUM", "degree": "BSc"},
        {"op": "upsert_publication", "title": "T"},
        {
            "op": "upsert_story",
            "title": "T",
            "challenge": "c",
            "mechanism": "m",
            "outcome": "o",
        },
        {"op": "set_field", "target": "w1", "field": "end_date", "value": "2020"},
        {"op": "set_personal_info", "field": "name", "value": "Max"},
        {"op": "set_summary", "lang": "de", "text": "Hallo"},
        {
            "op": "flag_conflict",
            "target": "w1",
            "field": "company",
            "existing": "A",
            "incoming": "B",
        },
        {"op": "request_confirmation", "question": "Which one?"},
    ]
    for sample in samples:
        assert type(adapter.validate_python(sample)) is type(
            reconcile_adapter.validate_python(sample)
        )


def test_upsert_skill_still_cannot_carry_denied_status():
    """The parametrised form ADR-063 leaves open stays shut: `denied` is not in
    the model's vocabulary through ANY door."""
    adapter: TypeAdapter = TypeAdapter(CommitOp)
    with pytest.raises(ValidationError):
        adapter.validate_python({"op": "upsert_skill", "name": "Go", "status": "denied"})


# ── The live hole, at the parse seam ──────────────────────────────────────────


def test_hallucinated_demote_skill_in_model_output_is_dropped():
    """SF-VAULT.10 regression. Raw model JSON claiming a demotion never reaches
    the applier."""
    from applire.services.profile.reconcile.engine import _parse_ops

    ops = _parse_ops(
        [
            _HALLUCINATED_DEMOTE,
            {"op": "upsert_skill", "name": "Go", "category": "technical"},
        ]
    )

    assert [type(o) for o in ops] == [UpsertSkill]
    assert not any(isinstance(o, DemoteSkill) for o in ops)


def test_dropped_hallucination_is_logged(caplog):
    from applire.services.profile.reconcile.engine import _parse_ops

    with caplog.at_level("DEBUG", logger="applire.services.profile.reconcile.engine"):
        _parse_ops([_HALLUCINATED_DEMOTE])

    assert any("demote_skill" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_reconcile_drops_hallucinated_demotion_end_to_end():
    """Through the whole engine: a model that emits `demote_skill` for a skill
    the candidate never retracted leaves that skill alone."""
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.engine import reconcile

    profile = _profile_with_confirmed_skill()
    provider = _Provider({"ops": [_HALLUCINATED_DEMOTE], "ambiguities": [], "denials": []})

    result = await reconcile(profile, {"answer": "I love Kubernetes."}, "testimony", provider)

    assert [o for o in result.ops if isinstance(o, DemoteSkill)] == []
    applied = apply_ops(profile, list(result.ops), "testimony")
    assert applied.profile.skills[0].status == "confirmed"
    assert applied.demotions == []


# ── The deterministic emission path is unaffected ─────────────────────────────


@pytest.mark.asyncio
async def test_deterministic_demotion_path_still_emits_and_applies():
    """The retraction the candidate ACTUALLY made still demotes: the emitter
    constructs typed `DemoteSkill` objects and never crosses the parse seam."""
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.engine import reconcile

    profile = _profile_with_confirmed_skill()
    provider = _Provider({"ops": [], "ambiguities": [], "denials": ["Kubernetes"]})

    result = await reconcile(
        profile,
        {"answer": "Scratch that — I have never touched Kubernetes."},
        "testimony",
        provider,
    )

    assert [type(o) for o in result.ops] == [DemoteSkill]
    applied = apply_ops(profile, list(result.ops), "testimony")
    assert applied.profile.skills[0].status == "denied"
    assert applied.demotions and applied.changes == []
