# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-31 (Hazelnut build 5, round 9): a vault-linked project has ONE container.

Delivery run 7: the writer put a project the vault ties to a role
(``associated_experience``) in the top-level ``projects`` list; the v2 compose
rendered it there while the reviewers, reading the vault, called it the role's nested
project; the corrector wrote a nested copy and kept the first; the duplicate-project
fold kept both copies' near-twin bullets and the round cap shipped that round. The
container is a vault fact, so code places it — the same rule v1's ``_nest_projects``
holds (#672 L100) — and the fold merges a shared fact once.
"""
import copy
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.bullet_redundancy_signal import _project_issue_text  # noqa: E402
from applire.services.cv_v2 import place_linked_projects  # noqa: E402

VAULT = {
    "work_experience": [{"id": "w1", "company": "Nivalo GmbH"}, {"id": "w2", "company": "Brenner AG"}],
    "projects": [
        {"id": "p1", "name": "Rollout Werk Süd", "associated_experience": "w1"},
        {"id": "p2", "name": "Ehrenamt Jugendtraining", "associated_experience": None},
    ],
}


def _prose(top, nested=None):
    return {
        "work": [{"id": "w1", "bullets": ["Leitete die Fertigung."], "projects": nested or []},
                 {"id": "w2", "bullets": ["Optimierte die Montage."], "projects": []}],
        "projects": top,
        "skills": [],
    }


def test_a_top_level_linked_project_moves_under_its_role():
    prose = _prose([{"name": "Rollout Werk Süd", "bullets": ["Drei Linien in sieben Monaten."]},
                    {"name": "Ehrenamt Jugendtraining", "bullets": ["Trainiert die U12."]}])
    snapshot = copy.deepcopy(prose)
    out = place_linked_projects(prose, VAULT)
    assert prose == snapshot, "never mutates the draft"
    assert [p["name"] for p in out["projects"]] == ["Ehrenamt Jugendtraining"]
    assert out["work"][0]["projects"] == [
        {"name": "Rollout Werk Süd", "bullets": ["Drei Linien in sieben Monaten."]}
    ]


def test_a_second_copy_merges_into_the_nested_one_and_drops_only_a_verbatim_repeat():
    prose = _prose(
        [{"name": "rollout werk süd", "bullets": ["Drei Linien in sieben Monaten.",
                                                  "Null Abweichungen im Audit."]}],
        nested=[{"name": "Rollout Werk Süd", "bullets": ["Drei Linien in sieben Monaten."]}],
    )
    out = place_linked_projects(prose, VAULT)
    assert out["projects"] == []
    (proj,) = out["work"][0]["projects"]
    assert proj["bullets"] == ["Drei Linien in sieben Monaten.", "Null Abweichungen im Audit."]


def test_an_unknown_name_or_a_link_to_no_entry_of_the_draft_stays_put():
    vault = copy.deepcopy(VAULT)
    vault["projects"][0]["associated_experience"] = "w9"  # no such entry in the draft
    prose = _prose([{"name": "Rollout Werk Süd", "bullets": ["x"]},
                    {"name": "Rollout South Plant", "bullets": ["y"]}])  # translated heading
    assert place_linked_projects(prose, vault) == prose
    assert place_linked_projects(prose, VAULT)["projects"] == [
        {"name": "Rollout South Plant", "bullets": ["y"]}
    ]


def test_the_fold_merges_a_shared_fact_once():
    text = _project_issue_text("Nivalo / Leiter", "Rollout", "Projekte", "Rollout")
    assert "A fact both copies state, in any wording, is written ONCE" in text
    assert "nothing is lost and nothing is said twice" in text
    assert "fold into it every bullet" not in text
