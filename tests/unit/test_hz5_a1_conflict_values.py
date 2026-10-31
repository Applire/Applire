# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5 U-1r (ruling A1-U1r; ADR-046 amended 2026-10-28): a bullet
conflict declares the two values it disputes, and the engine checks only that
declaration.

The delivery re-run parked a "Konflikt" between two DIFFERENT bullets of one
role (a mentoring duty against leading the team's delivery), and between two
tellings of one rollout whose figures agree. Rule 13 already asks the model to
"name the two different VALUES". The op now carries them, and code checks them:

- missing values, equal values, or a value absent from its own bullet mean the
  conflict is NOT raised;
- its incoming bullet goes the ``add_bullets`` way (on an import, through the
  restatement check);
- a scalar conflict is untouched.

Synthetic analogs of the delivery shapes only.
"""
import pytest

from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.services.profile.reconcile.conflict_values import route_unchecked_conflicts, unchecked_reason
from applire.services.profile.reconcile.ops import AddBullets, FlagConflict

W = "44444444-4444-4444-8444-444444444444"
MENTOR_HELD = "Provided guidance and mentoring to the platform team."
MENTOR_NEW = "Led a team of engineers building the warehouse control platform for three plants."
ROLLOUT_HELD = "Drove the scanner rollout, reaching go-live at four depots six months after kick-off."
ROLLOUT_NEW = "Led the scanner rollout at 4 depots in 6 months as programme manager, saving EUR 2M."
TARGET_HELD = "The platform targets a 70% reduction in manual review effort."
TARGET_NEW = "Lead the review automation project, targeting a 60%+ efficiency improvement."


def _c(existing, incoming, values, field="responsibilities"):
    return FlagConflict(target=W, field=field, existing=existing, incoming=incoming, values=values)


@pytest.mark.parametrize("values, reason", [
    (None, "values_missing"),
    (["mentoring"], "values_missing"),
    (["", "led a team"], "values_missing"),
    (["4 depots", "4 Depots"], "values_equal"),
    (["seven months", "6 months"], "value_not_in_bullet"),
])
def test_a_declaration_the_code_cannot_check_is_named(values, reason):
    assert unchecked_reason(_c(ROLLOUT_HELD, ROLLOUT_NEW, values)) == reason


def test_the_real_target_pair_is_a_checked_conflict():
    """Negative control (ruling condition 3): two different values, each in its own bullet."""
    op = _c(TARGET_HELD, TARGET_NEW, ["70% reduction in manual review effort", "60%+ efficiency improvement"],
            field="achievements")
    assert unchecked_reason(op) is None
    assert route_unchecked_conflicts([op]) == [op]


@pytest.mark.parametrize("held, new", [(MENTOR_HELD, MENTOR_NEW), (ROLLOUT_HELD, ROLLOUT_NEW)],
                         ids=["two-duties", "same-work"])
def test_an_unchecked_bullet_conflict_becomes_an_add_bullets_of_the_incoming_bullet(held, new):
    [op] = route_unchecked_conflicts([_c(held, new, None)])
    assert op == AddBullets(target=W, responsibilities=[new])


def test_a_scalar_conflict_is_untouched():
    op = FlagConflict(target=W, field="end_date", existing="2021-05", incoming="2021-07")
    assert route_unchecked_conflicts([op]) == [op]


class _Import:
    """The reconciler raises both delivery shapes without values; the
    restatement check answers per bullet (``restated``: which incoming texts it
    names as saying nothing new)."""

    def __init__(self, restated: set[str]):
        self.restated = restated

    async def aparse_json(self, prompt, **kw):
        system = (kw.get("system") or "").lower()
        if "profile reconciler" in system:
            return {"ops": [
                {"op": "flag_conflict", "target": "id_w1", "field": "responsibilities",
                 "existing": MENTOR_HELD, "incoming": MENTOR_NEW},
                {"op": "flag_conflict", "target": "id_w1", "field": "achievements",
                 "existing": ROLLOUT_HELD, "incoming": ROLLOUT_NEW},
                {"op": "flag_conflict", "target": "id_w1", "field": "achievements",
                 "existing": TARGET_HELD, "incoming": TARGET_NEW,
                 "values": ["70% reduction in manual review effort", "60%+ efficiency improvement"]},
            ], "ambiguities": [], "denials": []}
        if "you check bullet points of one person's professional profile" in system:
            lines = prompt.splitlines()
            items = []
            for line in lines:
                line = line.strip()
                if ".n" in line.split(":", 1)[0] and line.split(": ", 1)[1] in self.restated:
                    items.append({"new": line.split(":", 1)[0], "held": line.split(":", 1)[0].split(".")[0] + ".h1",
                                  "added": ""})
            return {"items": items}
        return {}


def _held() -> MasterProfileData:
    return MasterProfileData(work_experience=[WorkEntry(
        id=W, company="Nordwerk AG", role="Head of Platform", start_date="2019-01", end_date="2023-06",
        responsibilities=[MENTOR_HELD], achievements=[ROLLOUT_HELD, TARGET_HELD],
    )])


def _bullets(result) -> list[str]:
    w = result.merged_profile.work_experience[0]
    return list(w.responsibilities) + list(w.achievements)


@pytest.mark.asyncio
async def test_seam_two_duties_end_as_two_bullets_and_no_conflict():
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    result = await reconcile_import(_held(), _held().model_copy(), source="cv_upload", provider=_Import(set()))

    bullets = _bullets(result)
    assert MENTOR_HELD in bullets and MENTOR_NEW in bullets  # nothing lost
    assert len(bullets) == len(set(bullets))  # nothing doubled
    assert [c.existing_value for c in result.conflicts] == [TARGET_HELD]  # the real one stays


@pytest.mark.asyncio
async def test_seam_a_retelling_the_restatement_check_names_is_receipted_not_written():
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    result = await reconcile_import(
        _held(), _held().model_copy(), source="cv_upload", provider=_Import({ROLLOUT_NEW}))

    bullets = _bullets(result)
    assert ROLLOUT_HELD in bullets and ROLLOUT_NEW not in bullets
    assert [r.text for r in result.restated] == [ROLLOUT_NEW]
    assert [c.existing_value for c in result.conflicts] == [TARGET_HELD]
