# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bug #801 (ADR-046 amended 2026-10-22): a model skill binding must not bind two
different things.

UX audit 2026-10-20: the reconciler's `match_existing` bound the technical skill
"REST APIs" to the id of the soft skill "Mentoring", and the import bridge wrote
"REST APIs" as Mentoring's alias. Replays of the persisted input pinned the
vector: the model meant "REST API design" every time it bound REST APIs (27/27);
the id it wrote slipped. The committer now refuses two facts — the op's
`existing` echo names a different entry than the target id, or the pair crosses
technical/soft — and a refused binding keeps the entry as an ordinary upsert.
"""
from __future__ import annotations

import pytest

from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.binding_guard import (
    refuse_false_skill_bindings,
    skill_binding_refusal,
)
from applire.services.profile.reconcile.ops import MatchExisting, UpsertSkill


def _vault() -> MasterProfileData:
    return MasterProfileData(
        work_experience=[WorkEntry(id="w1", company="Finleap Build GmbH", role="Backend Engineer",
                                   start_date="2017-06")],
        skills=[
            Skill(id="s-aws", name="AWS", category="technical"),
            Skill(id="s-ecs", name="ECS", category="technical"),
            Skill(id="s-rest", name="REST API design", category="technical"),
            Skill(id="s-sql", name="SQL performance tuning", category="technical"),
            Skill(id="s-ment", name="Mentoring", category="soft"),
            Skill(id="s-inc", name="Incident management", category="domain"),
        ],
    )


def _skill(profile, sid):
    return next(s for s in profile.skills if s.id == sid)


# ── the predicate (facts only) ──────────────────────────────────────────────

def test_technical_bound_to_soft_is_refused():
    target = _skill(_vault(), "s-ment")
    assert skill_binding_refusal("REST APIs", "technical", None, target) == "category_mismatch"


def test_soft_bound_to_technical_is_refused():
    target = _skill(_vault(), "s-rest")
    assert skill_binding_refusal("Mentoring", "soft", None, target) == "category_mismatch"


@pytest.mark.parametrize("incoming_cat,target_id", [
    ("domain", "s-rest"),      # technical <-> domain drifts between extractions
    ("technical", "s-inc"),
    ("soft", "s-inc"),         # domain <-> soft drifts too
    (None, "s-ment"),          # unknown category: no category fact
    ("technical", "s-aws"),
])
def test_other_category_pairs_are_not_refused(incoming_cat, target_id):
    target = _skill(_vault(), target_id)
    assert skill_binding_refusal("Something", incoming_cat, None, target) is None


def test_echo_naming_another_entry_is_refused():
    target = _skill(_vault(), "s-ecs")
    assert skill_binding_refusal("Amazon Web Services (AWS)", "technical", "AWS", target) == "target_mismatch"


@pytest.mark.parametrize("echo", ["ECS", " ecs ", "Elastic Container Service"])
def test_echo_naming_the_target_or_its_alias_passes(echo):
    target = _skill(_vault(), "s-ecs")
    target.aliases = ["Elastic Container Service"]
    assert skill_binding_refusal("Amazon ECS", "technical", echo, target) is None


def test_same_words_are_never_refused():
    """Identity by the same words is a fact; a category that drifted between two
    extractions of "Mentoring" must not split it."""
    target = _skill(_vault(), "s-ment")
    assert skill_binding_refusal("Mentoring", "technical", None, target) is None
    assert skill_binding_refusal("mentoring", "technical", "Mentoring", target) is None


def test_a_name_another_entry_carries_is_refused():
    """Captured 2026-10-08: "SQL" bound to PostgreSQL's id while "SQL" was its own entry."""
    vault = _vault()
    vault.skills.append(Skill(id="s-sql2", name="SQL", category="technical"))
    target = _skill(vault, "s-sql")
    assert skill_binding_refusal("SQL", "technical", None, target, vault.skills) == "name_taken"
    _skill(vault, "s-aws").aliases = ["Amazon ECS"]
    assert skill_binding_refusal("amazon ecs", None, None, _skill(vault, "s-ecs"), vault.skills) == "name_taken"
    assert skill_binding_refusal("Postgres", "technical", None, target, vault.skills) is None


def test_match_existing_onto_a_neighbour_of_an_existing_name_leaves_no_receipt():
    vault = _vault()
    vault.skills.append(Skill(id="s-sql2", name="SQL", category="technical"))
    applied = apply_ops(vault, [MatchExisting(target="s-sql", incoming="SQL")], "interview")
    assert applied.matched == []


def test_prepass_rewrites_a_name_taken_binding_onto_the_entry_of_that_name():
    vault = _vault()
    vault.skills.append(Skill(id="s-sql2", name="SQL", category="technical"))
    incoming = MasterProfileData(skills=[Skill(name="SQL", category="technical")])
    out, refused = refuse_false_skill_bindings(incoming, vault, [MatchExisting(target="s-sql", incoming="SQL")])
    assert refused[0]["reason"] == "name_taken" and out[0].target is None
    applied = apply_ops(vault, out, "cv_upload")
    assert [s.name for s in applied.profile.skills].count("SQL") == 1  # merged into its own entry


# ── caller 1: the applier's target check, every door ────────────────────────

def test_turn_door_targeted_upsert_across_categories_is_not_merged():
    op = UpsertSkill(name="REST APIs", category="technical", target="s-ment", evidence=["w1"])
    applied = apply_ops(_vault(), [op], "interview")
    assert _skill(applied.profile, "s-ment").experience_refs == []
    assert "REST APIs" in [s.name for s in applied.profile.skills]  # kept as its own entry
    assert applied.matched == []


def test_turn_door_targeted_upsert_with_a_slipped_id_is_not_merged():
    op = UpsertSkill(name="Amazon Web Services (AWS)", category="technical", target="s-ecs",
                     existing="AWS", evidence=["w1"])
    applied = apply_ops(_vault(), [op], "interview")
    assert _skill(applied.profile, "s-ecs").experience_refs == []
    assert not any(m.entity_id == "s-ecs" for m in applied.matched)


def test_turn_door_targeted_upsert_with_a_matching_echo_still_merges():
    op = UpsertSkill(name="Amazon Web Services (AWS)", category="technical", target="s-aws",
                     existing="AWS", evidence=["w1"])
    applied = apply_ops(_vault(), [op], "interview")
    assert _skill(applied.profile, "s-aws").experience_refs == ["w1"]
    assert [(m.entity_id, m.incoming) for m in applied.matched] == [("s-aws", "Amazon Web Services (AWS)")]


# ── caller 2: the match_existing receipt, every door ────────────────────────

def test_match_existing_with_a_slipped_id_leaves_no_receipt():
    op = MatchExisting(target="s-ecs", incoming="Amazon Web Services (AWS)", existing="AWS")
    applied = apply_ops(_vault(), [op], "interview")
    assert applied.matched == []


def test_match_existing_with_a_matching_echo_is_receipted():
    op = MatchExisting(target="s-aws", incoming="Amazon Web Services (AWS)", existing="AWS")
    applied = apply_ops(_vault(), [op], "interview")
    assert [(m.entity_id, m.incoming) for m in applied.matched] == [("s-aws", "Amazon Web Services (AWS)")]


# ── caller 3: the import pre-pass ───────────────────────────────────────────

def _incoming() -> MasterProfileData:
    return MasterProfileData(skills=[
        Skill(name="REST APIs", category="technical", proficiency="intermediate"),
        Skill(name="Mentoring", category="soft"),
        Skill(name="Amazon Web Services (AWS)", category="technical"),
    ])


def test_prepass_rewrites_a_refused_match_existing_into_the_document_skill():
    ops = [MatchExisting(target="s-ment", incoming="REST APIs")]
    out, refused = refuse_false_skill_bindings(_incoming(), _vault(), ops)
    assert len(out) == 1 and isinstance(out[0], UpsertSkill)
    assert (out[0].name, out[0].category, out[0].proficiency, out[0].target) == (
        "REST APIs", "technical", "intermediate", None)
    assert [(r["incoming"], r["target"], r["reason"]) for r in refused] == [
        ("REST APIs", "s-ment", "category_mismatch")]


def test_prepass_strips_the_target_of_a_refused_upsert():
    ops = [UpsertSkill(name="Amazon Web Services (AWS)", category="technical", target="s-ecs",
                       existing="AWS")]
    out, refused = refuse_false_skill_bindings(_incoming(), _vault(), ops)
    assert out[0].target is None and out[0].existing is None and out[0].name == "Amazon Web Services (AWS)"
    assert refused[0]["reason"] == "target_mismatch"


def test_prepass_leaves_genuine_bindings_and_other_ops_alone():
    ops = [
        MatchExisting(target="s-ment", incoming="Mentoring", existing="Mentoring"),
        MatchExisting(target="s-aws", incoming="Amazon Web Services (AWS)", existing="AWS"),
        UpsertSkill(name="REST APIs", category="technical", target="s-rest", existing="REST API design"),
    ]
    out, refused = refuse_false_skill_bindings(_incoming(), _vault(), ops)
    assert out == ops and refused == []


def test_prepass_without_one_document_entry_drops_the_refused_binding():
    """A refused match_existing whose incoming name is not exactly ONE document
    skill carried no entry to begin with (the binder's exactly-one rule): it is
    removed, never turned into a skill under the model's own spelling — the
    witness still accounts for every document entry."""
    ops = [MatchExisting(target="s-ecs", incoming="Amazon Web Services", existing="AWS")]
    out, refused = refuse_false_skill_bindings(_incoming(), _vault(), ops)
    assert out == []
    assert refused[0]["reason"] == "target_mismatch"


# ── seams: the audit's own binding batch through the real import bridge ─────

import copy  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

_FIX = Path(__file__).resolve().parents[3] / "tests" / "files" / "801_cross_category_alias"
_MENTORING = "8def9047-4bd7-4421-ad1b-e9b27a07ed9d"
_REST_DESIGN = "af85b558-a27f-43e8-b8ea-a7ffc795245b"


def _audit_inputs():
    vault = MasterProfileData.model_validate(json.loads((_FIX / "vault_before_second_import.json").read_text()))
    incoming = MasterProfileData.model_validate(json.loads((_FIX / "incoming_linkedin.json").read_text()))
    batch = json.loads((_FIX / "audit_binding_batch.json").read_text())
    return vault, incoming, batch


class _Provider:
    """Answers the reconcile call with the audit's batch; every other call
    (the restatement check) gets an empty answer."""

    def __init__(self, batch):
        self.batch = batch

    async def aparse_json(self, prompt, *, system=None, **_kw):
        if system and "profile reconciler" in system:
            return copy.deepcopy(self.batch)
        return {}


def _assert_801_refused(result):
    merged = result.merged_profile
    mentoring = next(s for s in merged.skills if s.id == _MENTORING)
    assert "REST APIs" not in (mentoring.aliases or [])
    assert mentoring.experience_refs == []  # the REST job's evidence never lands on Mentoring
    assert not any(m.incoming == "REST APIs" and m.entity_id == _MENTORING for m in result.matched)
    # the entry is kept: its own skill (or a question about it), never lost
    names = {s.name for s in merged.skills} | {a for s in merged.skills for a in (s.aliases or [])}
    asked = any("REST APIs" in json.dumps(p.model_dump(mode="json"), ensure_ascii=False)
                for p in result.pending_confirmations)
    assert "REST APIs" in names or asked
    assert not any("REST APIs" in json.dumps(n.model_dump(mode="json"), ensure_ascii=False)
                   for n in result.not_applied)
    # the genuine pair of the same batch still binds and is aliased
    aws = next(s for s in merged.skills if s.name == "AWS")
    assert "Amazon Web Services (AWS)" in (aws.aliases or [])


@pytest.mark.asyncio
async def test_seam_fast_path_refuses_the_audit_binding():
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    vault, incoming, batch = _audit_inputs()
    result = await reconcile_import(vault, incoming, "cv_upload", _Provider(batch))
    _assert_801_refused(result)


@pytest.mark.asyncio
async def test_seam_fast_path_refuses_the_match_existing_face_too():
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    vault, incoming, batch = _audit_inputs()
    batch["ops"][0] = {"op": "match_existing", "target": _MENTORING, "incoming": "REST APIs"}
    result = await reconcile_import(vault, incoming, "cv_upload", _Provider(batch))
    _assert_801_refused(result)


@pytest.mark.asyncio
async def test_seam_segmented_path_refuses_the_audit_binding():
    from applire.exceptions import LLMTruncatedError
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    vault, incoming, batch = _audit_inputs()

    class _Truncating(_Provider):
        calls = 0

        async def aparse_json(self, prompt, *, system=None, **kw):
            if system and "profile reconciler" in system:
                type(self).calls += 1
                if type(self).calls == 1:
                    raise LLMTruncatedError("cap")
            return await super().aparse_json(prompt, system=system, **kw)

    result = await reconcile_import(vault, incoming, "cv_upload", _Truncating(batch))
    _assert_801_refused(result)


@pytest.mark.asyncio
async def test_seam_baseline_without_the_guard_writes_the_alias(monkeypatch):
    """The seam test is built on a shape ONLY the guard rescues: with the
    pre-pass and the applier check neutralised, the audit's alias comes back."""
    from applire.services.profile.reconcile import binding_guard, import_bridge
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    monkeypatch.setattr(import_bridge, "refuse_false_skill_bindings", lambda inc, prof, ops: (list(ops), []))
    monkeypatch.setattr(binding_guard, "skill_binding_refusal", lambda *a, **k: None)
    vault, incoming, batch = _audit_inputs()
    result = await reconcile_import(vault, incoming, "cv_upload", _Provider(batch))
    mentoring = next(s for s in result.merged_profile.skills if s.id == _MENTORING)
    assert "REST APIs" in (mentoring.aliases or [])
    assert mentoring.experience_refs == ["ad3edd8f-5409-4961-b6b7-a4c1a05289db"]  # the audit's vault


def _merge_vault() -> MasterProfileData:
    """The refused entry near-dupe-merges into "REST-APIs", so no sibling named
    "REST APIs" exists to make the alias writer's sibling refusal hide a binder
    that read the UNREWRITTEN batch."""
    return MasterProfileData(skills=[
        Skill(id="s-rest2", name="REST-APIs", category="technical"),
        Skill(id="s-ment", name="Mentoring", category="soft"),
    ])


@pytest.mark.asyncio
async def test_seam_fast_path_witness_and_alias_writer_read_the_rewritten_batch():
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    incoming = MasterProfileData(skills=[Skill(name="REST APIs", category="technical")])
    batch = {"ops": [{"op": "match_existing", "target": "s-ment", "incoming": "REST APIs"}]}
    result = await reconcile_import(_merge_vault(), incoming, "cv_upload", _Provider(batch))
    mentoring = next(s for s in result.merged_profile.skills if s.id == "s-ment")
    assert mentoring.aliases == []
    assert [s.name for s in result.merged_profile.skills] == ["REST-APIs", "Mentoring"]  # merged, kept
    assert result.not_applied == []


@pytest.mark.asyncio
async def test_seam_segmented_path_witness_and_alias_writer_read_the_rewritten_batch():
    from applire.exceptions import LLMTruncatedError
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    incoming = MasterProfileData(skills=[Skill(name="REST APIs", category="technical")])
    batch = {"ops": [{"op": "match_existing", "target": "s-ment", "incoming": "REST APIs"}]}

    class _Truncating(_Provider):
        calls = 0

        async def aparse_json(self, prompt, *, system=None, **kw):
            if system and "profile reconciler" in system:
                type(self).calls += 1
                if type(self).calls == 1:
                    raise LLMTruncatedError("cap")
            return await super().aparse_json(prompt, system=system, **kw)

    result = await reconcile_import(_merge_vault(), incoming, "cv_upload", _Truncating(batch))
    mentoring = next(s for s in result.merged_profile.skills if s.id == "s-ment")
    assert mentoring.aliases == []


# ── the prompt and the op schema ask for the echo ───────────────────────────

def test_prompt_and_schema_ask_for_the_echo():
    from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT
    from applire.services.profile.reconcile.schema_out import reconcile_json_schema_param

    assert "REQUIRED: target, existing, incoming." in RECONCILE_SYSTEM_PROMPT
    assert "With target, also give\n  existing: that skill's name" in RECONCILE_SYSTEM_PROMPT
    defs = json.dumps(reconcile_json_schema_param())
    schema = reconcile_json_schema_param()
    found = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("properties", {}).get("op", {}).get("const") == "match_existing" or \
               "match_existing" in json.dumps(node.get("properties", {}).get("op", {})):
                found.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    assert found and all("existing" in n.get("required", []) for n in found), defs[:200]
