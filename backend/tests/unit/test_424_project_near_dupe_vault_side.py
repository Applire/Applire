# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#424 — the VAULT half, measured rather than assumed (Nougat build 1).

Bug #424 (`triage:document-harm`, charter run 14 `controlling_emma_de`, spotted
by the blind hiring manager) reports the LucaNet project rendered twice on the
delivered CV — once nested under the Schwarzwald work entry, once standalone.
Two mechanisms were proposed for the vault half over the issue's life:

* the 2026-08-03 triage: *"The likeliest producer is `classify_engagement_dupe`
  returning 'no match, not ambiguous' across an import-time and an
  interview-time mention of the same project."*
* the reconcile prompt's rule 2 (`ONE CONTAINER (#424)`), whose compliance PR
  #663 then measured on the real provider: **14/14 compliant**, refuting the
  double-EMISSION on that sample.

This file measures the remaining half — the classifier — on the issue's own
strings, on the population it would actually see. Both proposed vault-side
mechanisms are refuted, and what the classifier does INSTEAD turns out to matter
more than what it was accused of. A disproof is a success; the value here is
that the next reader does not re-derive the refuted hypothesis a third time.

**Nothing about the ASSEMBLY half is claimed here.** `services/cv.py::
_nest_projects`' bin-scoped dedupe is a different owner and a different file,
and #424 stays open on it.
"""
from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).resolve().parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import (  # noqa: E402
    MasterProfileData,
    ProfileMetadata,
    ProjectEntry,
)
from applire.services.profile.reconcile.apply import apply_ops  # noqa: E402
from applire.services.profile.reconcile.dedupe import (  # noqa: E402
    classify_engagement_dupe,
)
from applire.services.profile.reconcile.ops import UpsertProject  # noqa: E402

# The two strings from the issue's Ground truth section, verbatim.
NESTED_NAME = "Einführung LucaNet für Konsolidierung"
STANDALONE_NAME = (
    "Einführung LucaNet für Konsolidierung (GmbH + Vertriebsgesellschaft Schweiz)"
)


def _vault_holding_the_nested_lucanet() -> MasterProfileData:
    return MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Emma Vogt"},
            "work_experience": [
                {
                    "id": "w-schwarzwald",
                    "company": "Schwarzwald Präzision GmbH",
                    "role": "Controllerin",
                    "start_date": "2019-01",
                }
            ],
            "projects": [
                {
                    "id": "p-lucanet",
                    "name": NESTED_NAME,
                    "associated_experience": "w-schwarzwald",
                }
            ],
            "metadata": ProfileMetadata().model_dump(mode="json"),
        }
    )


def test_the_classifier_does_catch_the_issues_own_near_dupe_pair():
    """REFUTES the 2026-08-03 triage's hypothesis.

    The triage expected `classify_engagement_dupe` to return "no match, not
    ambiguous" for this pair, which would let the second entry append silently.
    Measured on the issue's own two strings it returns **AMBIGUOUS** — the
    "ask, never guess" arm — so the pair is caught, not missed.
    """
    verdict = classify_engagement_dupe(
        org=STANDALONE_NAME,
        role=None,
        start_date=None,
        existing=[ProjectEntry(name=NESTED_NAME)],
        org_getter=lambda p: p.name,
    )
    assert verdict.match is None
    assert [p.name for p in verdict.ambiguous] == [NESTED_NAME]


def test_even_an_exact_repeat_of_the_project_name_is_ambiguous_not_appended():
    """The stronger property, and the one that makes the append unreachable.

    A project carries no dates in this shape, so the classifier's MATCH arm
    (strong org + equal start month) cannot fire, and with no role evidence the
    role arm is not `_DISTINCT` either. Both roads lead to AMBIGUOUS. There is
    therefore no verdict, on this population, that appends a second row.
    """
    verdict = classify_engagement_dupe(
        org=NESTED_NAME,
        role=None,
        start_date=None,
        existing=[ProjectEntry(name=NESTED_NAME)],
        org_getter=lambda p: p.name,
    )
    assert verdict.match is None
    assert len(verdict.ambiguous) == 1


def test_a_second_lucanet_op_creates_no_second_row_and_parks_a_question():
    """End to end through the real applier: the vault does NOT gain a duplicate.

    This is #424's vault-side acceptance criterion, measured. It also names the
    behaviour that replaces the duplication — and that behaviour has its own
    defect, pinned in `test_confirmation_carried_bullets_are_lost.py`: the
    parked question has no resolution turn, so the second mention is LOST rather
    than duplicated. The two issues are one seam seen from two sides.
    """
    profile = _vault_holding_the_nested_lucanet()
    result = apply_ops(
        profile,
        [UpsertProject(ref="p1", name=STANDALONE_NAME, parent=None)],
        "interview",
    )

    assert len(result.profile.projects) == 1
    assert result.profile.projects[0].name == NESTED_NAME
    assert len(result.pending_confirmations) == 1
    assert "section" in result.pending_confirmations[0].context
    assert result.pending_confirmations[0].context["section"] == "projects"


def test_the_vault_has_exactly_one_write_path_for_a_project_row():
    """The absence claim behind the two tests above, proven by exhausting the
    POSITIVE set rather than by measuring the negative one.

    If a second appender existed anywhere in `services/`, the classifier's
    verdict would not be the whole story and both refutations would be worth
    only what that one path is worth. Document-side lists (`cv_budget`'s
    `surviving_projects`, `thumbnails`' sample profile) are not the vault and
    are excluded by construction — they never touch `MasterProfileData.projects`.
    """
    import ast

    root = Path(__file__).resolve().parents[2] / "applire"
    appenders: list[str] = []
    for path in root.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        if ".projects.append(" not in src:
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            segment = ast.get_source_segment(src, node) or ""
            # `profile.projects` / `new_profile.projects` — the vault aggregate.
            if "profile.projects.append(" in segment:
                appenders.append(f"{path.relative_to(root)}::{node.name}")
    assert appenders == [
        "services/profile/reconcile/apply.py::_apply_upsert_project"
    ], appenders
