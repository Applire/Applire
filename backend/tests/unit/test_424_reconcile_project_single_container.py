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

"""#424 — a project rendered twice: nested under its job AND standalone.

The reported shape (real issue text): "Einführung LucaNet für Konsolidierung"
appears twice in the delivered CV — once nested under the Schwarzwald work
entry, once as its own top-level entry in ``projects[]``. ``tailored_data.
projects`` (the writer's own output) is ``[]`` for this document, so the
duplicate is NOT a writer/tailoring defect — the assembly's deterministic
nesting step (``services/cv.py::_nest_projects``) places every VAULT
``ProjectEntry`` exactly once, by its own ``associated_experience``; it renders
two containers only because the VAULT holds two separate ``ProjectEntry``
records for the one real project — one with ``associated_experience`` set to
the Schwarzwald work id (nested), one with it null (standalone).

Category B (applire-prompt-first skill): no rule in ``RECONCILE_SYSTEM_PROMPT``
ever told the model "a project stated once belongs in exactly one
upsert_project op." Rule 2 now carries that invariant (tag "ONE CONTAINER
(#424)"). This is a PROMPT fix; ``apply_ops`` is deliberately UNCHANGED.
``_apply_upsert_project``'s existing near-dupe guard (``classify_engagement_
dupe``, #177/#181) is NOT a reliable backstop for this shape — see
``test_the_engagement_dupe_guard_can_silently_duplicate_a_project`` below for
the reproduced gap in its own, already-documented role/date heuristic. Closing
THAT gap is a separate, pre-existing design tradeoff (its own docstring cites
the #177/#181 review) and is out of scope for this fix.

Per the work order for this defect: **no real-provider call was made here**
(the efficacy check runs later in a shared real run). These tests pin (a) the
new rule's presence in the shipped prompt, (b) the CURRENT, still-standing
exposure at the deterministic layer if a model ever violates the rule anyway,
and (c) the desired outcome once a batch actually follows the new rule.
"""
from __future__ import annotations

from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT
from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.dedupe import classify_engagement_dupe
from applire.services.profile.reconcile.ops import AddBullets, UpsertProject

SOURCE = "interview"

# The exact project name from the #424 issue text.
_PROJECT_NAME = "Einführung LucaNet für Konsolidierung"


def _schwarzwald_profile() -> MasterProfileData:
    return MasterProfileData(
        work_experience=[
            WorkEntry(
                company="Schwarzwald Präzision GmbH",
                role="Financial Controller",
                start_date="2018-01",
                is_current=True,
            )
        ]
    )


# ── 1. The rule ships ──────────────────────────────────────────────────────────


def test_one_container_rule_is_in_the_prompt():
    assert "ONE CONTAINER (#424)" in RECONCILE_SYSTEM_PROMPT
    assert "never both" in RECONCILE_SYSTEM_PROMPT
    assert "not a second project" in RECONCILE_SYSTEM_PROMPT


# ── 2. Residual exposure: the applier alone does not close this ────────────────


def test_a_rule_violating_batch_still_duplicates_at_the_apply_layer():
    """Characterisation test, not a desired behaviour.

    If a model ever ignores rule 2's new "ONE CONTAINER" sentence and emits TWO
    upsert_project ops for the same real project — one nested under its job,
    one standalone, each carrying an explicit but different-sounding role text
    — ``apply_ops`` (unchanged by this fix on purpose) still produces two
    project entries, exactly as #424 reports the delivered CV. This is why the
    fix has to live in the prompt: nothing downstream catches it reliably (see
    the guard-gap test below).
    """
    profile = _schwarzwald_profile()
    schwarzwald_id = profile.work_experience[0].id

    ops = [
        UpsertProject(
            ref="p1", target=None, name=_PROJECT_NAME, parent=schwarzwald_id,
            role="Projektleiter", start_date="2019",
        ),
        AddBullets(target="p1", achievements=[
            "Konzernkonsolidierung von Excel auf LucaNet umgestellt"
        ]),
        # The SAME project, restated later in the same batch with no parent and
        # a different role text — the shape that survives classify_engagement_
        # dupe's DISTINCT branch (see the guard-gap test below).
        UpsertProject(
            ref="p2", target=None, name=_PROJECT_NAME, parent=None,
            role="Financial Controller",
        ),
    ]
    result = apply_ops(profile, ops, SOURCE)

    assert len(result.profile.projects) == 2, (
        "if this now reads 1, the applier grew its own cross-container guard — "
        "update this test's docstring and the guard-gap test below to match, "
        "rather than deleting either."
    )
    nested = [p for p in result.profile.projects if p.associated_experience]
    standalone = [p for p in result.profile.projects if not p.associated_experience]
    assert len(nested) == 1 and len(standalone) == 1
    assert nested[0].name == standalone[0].name == _PROJECT_NAME


def test_the_engagement_dupe_guard_can_silently_duplicate_a_project():
    """Ground truth for the "does the applier need to harden further" question.

    ``classify_engagement_dupe`` merges on a same-org + same-start-month match,
    and asks (RequestConfirmation) when org matches but dates/role are weak
    evidence — but its own, pre-existing #177/#181 design silently APPENDS a
    new entry when the org is a strong match yet the role text reads as clearly
    DISTINCT. A project restated once with an explicit role ("Projektleiter")
    and again with a different explicit role (here, the surrounding job's own
    title bleeding into the second mention) reproduces exactly that silent
    branch — for the SAME project name. This is a real, reproducible gap, but
    it is not this fix's to close: deciding whether "Projektleiter" and
    "Financial Controller" describe the same project role, for the SAME named
    project, is the identity judgement ADR-062 clause 1 reserves for the model
    — the same reason the #618 education pair is not foldable in code either.
    """
    class _FakeProject:
        def __init__(self, name: str, role: str | None, start_date: str | None):
            self.name = name
            self.role = role
            self.start_date = start_date

    existing = [_FakeProject(_PROJECT_NAME, "Projektleiter", "2019")]
    verdict = classify_engagement_dupe(
        org=_PROJECT_NAME, role="Financial Controller", start_date=None,
        existing=existing, org_getter=lambda p: p.name,
    )
    assert verdict.match is None
    assert verdict.ambiguous == []  # DISTINCT — the silent-append branch


# ── 3. The desired outcome once a batch follows the new rule ───────────────────


def test_a_compliant_batch_with_a_single_project_op_lands_in_one_container():
    """What the model SHOULD emit once it applies rule 2's new sentence: ONE
    upsert_project (nested under Schwarzwald), with any further facts about the
    same project folded via add_bullets against the SAME ref — never a second,
    standalone upsert_project for it."""
    profile = _schwarzwald_profile()
    schwarzwald_id = profile.work_experience[0].id

    ops = [
        UpsertProject(
            ref="p1", target=None, name=_PROJECT_NAME, parent=schwarzwald_id,
            role="Projektleiter", start_date="2019",
        ),
        AddBullets(target="p1", achievements=[
            "Konzernkonsolidierung von Excel auf LucaNet umgestellt"
        ]),
    ]
    result = apply_ops(profile, ops, SOURCE)

    assert len(result.profile.projects) == 1
    proj = result.profile.projects[0]
    assert proj.name == _PROJECT_NAME
    assert proj.associated_experience == schwarzwald_id
    assert "Konzernkonsolidierung von Excel auf LucaNet umgestellt" in proj.achievements
