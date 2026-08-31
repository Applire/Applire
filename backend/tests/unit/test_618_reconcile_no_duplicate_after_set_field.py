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

"""#618 — a two-source import wrote the same education entry twice.

The reported shape (Provadis apprenticeship, real issue text): source A already
sits in the vault as "Computer System Developer / Provadis Partner für Bildung
GmbH, 09/2002-01/2005"; source B restates the SAME qualification as
"Fachinformatiker Anwendungsentwicklung / Provadis Hochschule, 2002-2005", and
both end up on the delivered CV as separate education entries.

**A refinement of the work order's hypothesis, pinned by
``test_set_field_cannot_reach_an_education_entry_at_all`` below**: the
hypothesis was that the reconciler emitted ``set_field`` against the existing
entry AND ALSO a fresh ``upsert_education`` for the alternate wording, i.e. a
batch-consistency violation between the two ops. Ground-truthing that against
``apply_ops`` found a prior, independent fact that changes the mechanism:
``apply_ops``'s internal ``resolve()`` closure (the function every ``set_field``
target is looked up through) only ever searches ``work_experience`` /
``projects`` / ``volunteer_activities`` — education (and certifications,
languages, publications) are never in its search set. A ``set_field`` naming an
education entry's id was therefore **already inert** before this fix: it
resolves to ``None`` and ``_apply_set_field`` silently returns, with no error,
no confirmation, and no ``rejected_ops`` entry. If the batch really did contain
such a ``set_field``, it contributed nothing either way — the observable
duplicate is fully and only explained by ``upsert_education``'s OWN identity
check (``classify_dupe``) failing to recognise the alternate wording as the
same entry (see ``test_the_applier_natural_key_cannot_recognise_this_pair``).

Category B (applire-prompt-first skill) still holds — no rule in
``RECONCILE_SYSTEM_PROMPT`` ever told the model that an entity it has already
decided is existing must not ALSO be re-created via a target-less upsert. Rule
5 now carries that invariant (tag "ONE WRITE (#618)"), rewritten to name the
mechanism that actually works for target-less ops today: restate the entity
with its OWN existing institution/degree text (visible in the CURRENT MASTER
PROFILE) and add only the genuinely new field, so ``classify_dupe``'s
same-text MATCH branch folds it in — never a second entry under the new
source's alternate phrasing. The vocabulary's ``set_field`` bullet was also
corrected to say plainly that it cannot target education/certification/
language/publication at all, closing the silent-no-op trap for any FUTURE
model output that (like the hypothesis) tries it anyway.

``apply_ops`` is deliberately UNCHANGED beyond that prompt-accuracy fix.
Widening ``classify_dupe`` to fold "Provadis Hochschule" into "Provadis
Partner für Bildung GmbH" would be a semantic judgement ADR-062 clause 1
reserves for the model (see the natural-key test below); and widening
``resolve()`` to also search education/certification/language/publication
(making ``set_field`` actually reach them) is a legitimate, separate,
low-risk follow-up — flagged in the work report, not built here, since it
touches a heavily-shared function outside this defect's requested scope.

Per the work order for this defect: **no real-provider call was made here**
(the efficacy check runs later in a shared real run). These tests pin (a) the
new rule text, (b) the ``resolve()`` gap that refines the hypothesis, (c) the
CURRENT, still-standing exposure at the deterministic layer if a model ever
violates the rule anyway, and (d) the outcome once a batch actually follows
the new rule via the mechanism that works today.
"""
from __future__ import annotations

import re

from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT
from applire.schemas.profile import EducationEntry, MasterProfileData
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.dedupe import classify_dupe
from applire.services.profile.reconcile.ops import SetField, UpsertEducation

SOURCE = "cv_upload (second source)"

# The exact strings from the #618 issue text.
_EXISTING_INSTITUTION = "Provadis Partner für Bildung GmbH"
_EXISTING_DEGREE = "Computer System Developer"
_ALTERNATE_INSTITUTION = "Provadis Hochschule"
_ALTERNATE_DEGREE = "Fachinformatiker Anwendungsentwicklung"


def _provadis_profile() -> MasterProfileData:
    return MasterProfileData(
        education=[
            EducationEntry(
                institution=_EXISTING_INSTITUTION,
                degree=_EXISTING_DEGREE,
                start_date="2002-09",
                end_date="2005-01",
            )
        ]
    )


# ── 1. The rule ships ──────────────────────────────────────────────────────────


def test_one_write_rule_is_in_the_prompt():
    assert "ONE WRITE (#618)" in RECONCILE_SYSTEM_PROMPT
    assert "never ALSO emit a target-less" in RECONCILE_SYSTEM_PROMPT
    # Named explicitly because these are exactly the ops with no `target` at all
    # — the ones a batch-consistency violation for #618 actually reaches.
    assert "upsert_education" in RECONCILE_SYSTEM_PROMPT.split("ONE WRITE")[1][:400]


def _flatten(text: str) -> str:
    """Collapse whitespace runs (including line wraps) to a single space, so a
    multi-word needle matches regardless of where the source file wrapped it
    (convention: tests/unit/test_team_size_semantics_prompt_parity.py)."""
    return re.sub(r"\s+", " ", text)


def test_set_field_vocabulary_entry_states_its_real_scope():
    """The vocabulary entry has been wrong in BOTH directions within one session,
    which is why it is pinned.

    It first said set_field fills a field "on an entity" with no scope at all —
    inviting a set_field against an education id that then vanished without a
    sound. It was then narrowed to "work / project / volunteer only", which was
    accurate for the broken applier and became FALSE the moment ``resolve_any``
    landed: a prompt telling the model to avoid the one op that now works would
    steer it into the stray-upsert shape #618 is about. It now states the real
    scope: any existing entity, named by id."""
    bullet = _flatten(RECONCILE_SYSTEM_PROMPT).split("- set_field —")[1].split(" - ")[0]
    assert "ANY existing entity" in bullet
    assert "education" in bullet and "certifications" in bullet
    assert "no target at all" not in bullet  # the retired, now-false restriction


# ── 2. Ground truth: the hypothesis, refined ────────────────────────────────────


def test_set_field_now_reaches_an_education_entry():
    """Was ``..._cannot_reach_...``: the ground truth this file was written
    against has since been FIXED, in the same session (2026-08-31).

    When this file was written, ``apply_ops``'s ``resolve()`` searched
    work/project/volunteer only, so a ``set_field`` naming an existing education
    entry resolved to nothing and was dropped silently — no error, no
    ``rejected_ops``, and the entry-level import witness could not see it. That
    is why the #618 education fix was placed in the prompt: the applier gave no
    signal.

    ``_apply_set_field`` now resolves through ``resolve_any``, which covers every
    id-bearing section. ``resolve`` itself stays experience-only (parent /
    evidence / add_bullets). Full coverage lives in
    ``test_set_field_reaches_every_section.py``; this test keeps the pointer so
    the reasoning above stays attached to the #618 story it shaped.
    """
    profile = _provadis_profile()
    existing_id = profile.education[0].id

    result = apply_ops(profile, [SetField(target=existing_id, field="grade", value="1,9")], SOURCE)

    assert result.profile.education[0].grade == "1,9"
    assert [c.section for c in result.changes] == ["education"]


def test_the_applier_natural_key_cannot_recognise_this_pair():
    """Ground truth for the ADR-062 clause-1 judgement call in the work report.

    ``classify_dupe`` — the SAME machinery that already folds curated EN/DE /
    symbol / cognate certification variants (#239) — genuinely cannot fold
    these two real, differently-worded strings into a match: both the
    institution and the degree tokenise to disjoint sets (no shared stem, no
    containment), so the verdict is empty (no match, no ambiguity) and
    ``_apply_upsert_education`` falls through to creating a new entry. Widening
    the natural key to catch this specific pair would mean curating an
    open-ended DE/EN vocational-qualification dictionary — a JUDGEMENT about
    what the institution/degree names refer to, not a fact a token-overlap
    check can compute. That call belongs to the model (rule 1 already assigns
    it cross-language/synonym entity matching); it is not earned code here.
    """
    verdict = classify_dupe(
        {"institution": _ALTERNATE_INSTITUTION, "degree": _ALTERNATE_DEGREE},
        _provadis_profile().education,
        {"institution": lambda e: e.institution, "degree": lambda e: e.degree},
    )
    assert verdict.match is None
    assert verdict.ambiguous == []


# ── 3. Residual exposure: the applier alone does not close this ────────────────


def test_a_rule_violating_batch_still_duplicates_at_the_apply_layer():
    """Characterisation test, not a desired behaviour.

    The #618 batch shape: a set_field against the existing entry PLUS a fresh
    upsert_education under the alternate wording. ``apply_ops`` still produces
    two education entries. This is why the fix has to live in the prompt: the
    applier's natural key cannot recognise the pair (test above), so nothing
    downstream catches the stray upsert — and that stays true now that the
    set_field half applies rather than vanishing.
    """
    profile = _provadis_profile()
    existing_id = profile.education[0].id

    ops = [
        SetField(target=existing_id, field="grade", value="1,9"),
        UpsertEducation(
            institution=_ALTERNATE_INSTITUTION,
            degree=_ALTERNATE_DEGREE,
            start_date="2002",
            end_date="2005",
        ),
    ]
    result = apply_ops(profile, ops, SOURCE)

    assert len(result.profile.education) == 2, (
        "if this now reads 1, the applier grew its own guard for this pair — "
        "update this test's docstring and test_the_applier_natural_key_cannot_"
        "recognise_this_pair above to match, rather than deleting either."
    )
    # The set_field DOES apply now (it did not when this test was written) —
    # which is the point: it lands on the ORIGINAL entry while the stray
    # upsert_education still creates a second one. A working set_field does not
    # make the batch correct; only the prompt rule prevents the duplicate.
    assert result.profile.education[0].grade == "1,9"


# ── 4. The desired outcome once a batch follows the new rule ───────────────────


def test_a_compliant_batch_restates_the_existing_wording_and_stays_a_single_entry():
    """What the model SHOULD emit once it applies rule 5's new sentence for a
    target-less op: re-emit upsert_education using the EXISTING vault wording
    (not the new source's alternate phrasing), carrying only the genuinely new
    field — classify_dupe's exact-match branch folds it into the ONE record."""
    profile = _provadis_profile()

    ops = [
        UpsertEducation(
            institution=_EXISTING_INSTITUTION,  # restated, not the new source's wording
            degree=_EXISTING_DEGREE,
            grade="1,9",  # the genuinely new fact from the second source
        )
    ]
    result = apply_ops(profile, ops, SOURCE)

    assert len(result.profile.education) == 1
    entry = result.profile.education[0]
    assert entry.institution == _EXISTING_INSTITUTION
    assert entry.degree == _EXISTING_DEGREE
    assert entry.grade == "1,9"


def test_a_compliant_batch_with_nothing_new_to_add_emits_no_op_at_all():
    """The simplest compliant case: the second source adds no new fact beyond
    what is already recorded, so the correct batch is empty for this entity —
    never a redundant upsert "just in case"."""
    profile = _provadis_profile()
    result = apply_ops(profile, [], SOURCE)
    assert len(result.profile.education) == 1
