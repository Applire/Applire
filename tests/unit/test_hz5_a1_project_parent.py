# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5 D-8: the reconciler asked which position a project belongs
to. The hub showed the question in English inside the German interface, with
options like "Engineer (id_w1)" (prompt handles). After the answer, the
project's ``associated_experience`` was still the company string, because a
model-asked confirmation was bookkeeping only.

The fix has two halves (``reconcile/project_parent.py``): the engine rebuilds
the ask as confirmation family 6 (both languages, ``entry:<id>`` keys), and
the applier writes the chosen position's id. Synthetic profile only.
"""
import re

import pytest

from applire.schemas.profile import (
    MasterProfileData,
    PendingConfirmation,
    ProfileMetadata,
    ProjectEntry,
    WorkEntry,
)
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.ops import ResolveConfirmation

W1, W2 = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
PROJECT = "Labor-Informationssystem Rollout"


def _profile(assoc: str | None = "Nordwerk AG") -> MasterProfileData:
    return MasterProfileData(
        work_experience=[
            WorkEntry(id=W1, company="Nordwerk AG", role="Systems Engineer",
                      start_date="2018-10", end_date="2021-05"),
            WorkEntry(id=W2, company="Nordwerk AG", role="Head of Lab Systems",
                      start_date="2021-05", is_current=True),
        ],
        projects=[ProjectEntry(id="33333333-3333-4333-8333-333333333333", name=PROJECT,
                               associated_experience=assoc)],
    )


class _Asker:
    """Replies like the replay: an English question, options with handles."""

    def __init__(self, options):
        self.options = options

    async def aparse_json(self, prompt, **kw):
        return {
            "ops": [],
            "ambiguities": [{
                "op": "request_confirmation",
                "question": f"Should “{PROJECT}” be linked to the Systems Engineer role or the Head of Lab Systems role?",
                "options": self.options,
                "context": {"section": "projects", "project": PROJECT, "associated_experience": "Nordwerk AG"},
            }],
            "denials": [],
        }


async def _ask(options):
    result = await reconcile(_profile(), {"answer": "x"}, "testimony", _Asker(options), "de")
    [rc] = result.ambiguities
    return rc


@pytest.mark.asyncio
async def test_the_ask_is_rebuilt_in_both_languages_without_handles():
    rc = await _ask(["Systems Engineer (id_w1)", "Head of Lab Systems (id_w2)"])

    assert rc.option_keys == [f"entry:{W1}", f"entry:{W2}"]
    assert rc.question_i18n["de"] == f"Zu welcher Position gehört das Projekt „{PROJECT}“?"
    assert rc.options_i18n[0]["de"] == "Systems Engineer · Nordwerk AG (2018-10 – 2021-05)"
    assert rc.options_i18n[1]["de"] == "Head of Lab Systems · Nordwerk AG (2021-05 – heute)"
    assert rc.options_i18n[1]["en"].endswith("(2021-05 – present)")
    shown = [rc.question, *rc.options, *rc.question_i18n.values(),
             *(v for o in rc.options_i18n for v in o.values())]
    assert not [s for s in shown if re.search(r"\bid_[a-z]\d+\b", s)]
    assert rc.context["kind"] == "project_parent"


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    ["Systems Engineer", "Head of Lab Systems"],               # no handle: nothing to bind on
    ["Systems Engineer (id_w1)", "Something else (id_w9)"],    # a handle that maps to nothing
    ["Systems Engineer (id_w1)", "Systems Engineer (id_w1)"],  # the same position twice
    ["Systems Engineer (id_w1 or id_w2)", "Head of Lab Systems (id_w2)"],
], ids=["no-handle", "unknown", "duplicate", "two-handles"])
async def test_an_ask_the_code_cannot_bind_stays_the_models(options):
    rc = await _ask(options)

    assert rc.option_keys == [] and rc.question_i18n is None
    assert rc.options == options


def _parked(rc) -> MasterProfileData:
    profile = _profile()
    profile.metadata = ProfileMetadata(pending_confirmations=[PendingConfirmation(
        confirmation_id="c-1", question=rc.question, options=list(rc.options), context=dict(rc.context),
        question_i18n=rc.question_i18n, options_i18n=rc.options_i18n, option_keys=list(rc.option_keys),
    )])
    return profile


@pytest.mark.asyncio
async def test_the_german_answer_writes_the_positions_id():
    rc = await _ask(["Systems Engineer (id_w1)", "Head of Lab Systems (id_w2)"])
    profile = _parked(rc)
    _q, shown = profile.metadata.pending_confirmations[0].rendered("de")

    out = apply_ops(profile, [ResolveConfirmation(confirmation_id="c-1", chosen_option=shown[0])], "review")

    assert out.profile.projects[0].associated_experience == W1
    assert any(c.section == "projects" and c.field == "associated_experience" and c.new_value == W1
               for c in out.changes)
    assert out.profile.metadata.pending_confirmations == []


@pytest.mark.asyncio
async def test_an_answer_naming_the_held_parent_writes_nothing_and_says_so():
    rc = await _ask(["Systems Engineer (id_w1)", "Head of Lab Systems (id_w2)"])
    profile = _parked(rc)
    profile.projects[0].associated_experience = W2

    out = apply_ops(profile, [ResolveConfirmation(confirmation_id="c-1", chosen_option=rc.options[1])], "review")

    assert out.profile.projects[0].associated_experience == W2
    assert [i.reason for i in out.not_applied] == ["confirmation_already_present"]


@pytest.mark.asyncio
async def test_a_vanished_project_is_receipted_not_silent():
    rc = await _ask(["Systems Engineer (id_w1)", "Head of Lab Systems (id_w2)"])
    profile = _parked(rc)
    profile.projects = []

    out = apply_ops(profile, [ResolveConfirmation(confirmation_id="c-1", chosen_option=rc.options[0])], "review")

    assert [(i.section, i.label, i.reason) for i in out.not_applied] == [
        ("projects", PROJECT, "confirmation_unresolvable")]


@pytest.mark.asyncio
async def test_the_hub_answers_it_in_place_in_the_readers_language():
    from applire.services.profile.health import _confirmation_issue

    rc = await _ask(["Systems Engineer (id_w1)", "Head of Lab Systems (id_w2)"])
    issue = _confirmation_issue(_parked(rc).metadata.pending_confirmations[0])

    assert issue.question_i18n["de"].startswith("Zu welcher Position")
    assert issue.options and [o["de"] for o in issue.options_i18n][1].endswith("heute)")
