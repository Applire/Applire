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


# ── D-16 (delivery run 3) — an ask about an already-attached project ─────────


class _AskerShape3:
    """The run-3 shape: no section, no handles in the options, the work id in
    the context. In ``ops`` or in ``ambiguities``."""

    def __init__(self, where="ambiguities", options=None):
        self.where = where
        self.options = options or ["The Nordwerk experience", "Systems Engineer role", "Both / different project"]

    async def aparse_json(self, prompt, **kw):
        ask = {
            "op": "request_confirmation",
            "question": f"Does the 2020-12 to 2021-07 project “{PROJECT}” belong under the Nordwerk experience or the Systems Engineer role?",
            "options": self.options,
            "context": {"project": PROJECT, "existing_work_id": "id_w1", "project_dates": "2020-12 to 2021-07"},
        }
        if self.where == "ops":
            return {"ops": [ask], "ambiguities": [], "denials": []}
        return {"ops": [], "ambiguities": [ask], "denials": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["ambiguities", "ops"])
async def test_an_ask_about_a_project_already_attached_to_a_held_position_is_not_raised(where):
    from applire.services.profile.reconcile.ops import RequestConfirmation

    result = await reconcile(_profile(assoc=W1), {"answer": "x"}, "cv_upload", _AskerShape3(where), "de")

    assert result.ambiguities == []
    assert not [o for o in result.ops if isinstance(o, RequestConfirmation)]


@pytest.mark.asyncio
async def test_the_same_ask_about_a_project_held_under_a_company_string_is_still_asked():
    result = await reconcile(_profile(assoc="Nordwerk AG"), {"answer": "x"}, "cv_upload", _AskerShape3(), "de")

    [rc] = result.ambiguities
    assert rc.option_keys == []  # no handles to bind on: the model's own ask (residual)


@pytest.mark.asyncio
async def test_a_section_less_ask_with_handles_is_rebuilt():
    result = await reconcile(
        _profile(), {"answer": "x"}, "cv_upload",
        _AskerShape3(options=["Systems Engineer (id_w1)", "Head of Lab Systems (id_w2)"]), "de",
    )

    [rc] = result.ambiguities
    assert rc.option_keys == [f"entry:{W1}", f"entry:{W2}"]
    assert rc.question_i18n["de"].startswith("Zu welcher Position")


def test_a_parked_ask_about_an_attached_project_is_not_shown_on_the_hub():
    from applire.services.profile.health import assess_health

    profile = _profile(assoc=W1)
    profile.metadata = ProfileMetadata(pending_confirmations=[PendingConfirmation(
        confirmation_id="c-3", question="Does the project belong under …?", options=["A", "B"],
        context={"project": PROJECT, "existing_work_id": "id_w1"},
    )])
    assert not [i for i in assess_health(profile).issues if i.thread == "confirmation"]

    profile.projects[0].associated_experience = "Nordwerk AG"  # not attached to a held position
    assert [i.thread for i in assess_health(profile).issues if i.thread == "confirmation"] == ["confirmation"]


class _ImportAsker(_AskerShape3):
    """Delivery run 3 exactly: the import brings the project in, places it
    under a held position AND asks where it belongs, in one batch."""

    def __init__(self, parent="id_w1"):
        super().__init__()
        self.parent = parent

    async def aparse_json(self, prompt, **kw):
        data = await super().aparse_json(prompt, **kw)
        data["ops"] = [{"op": "upsert_project", "ref": "p9", "name": PROJECT, "parent": self.parent,
                        "start_date": "2020-12", "end_date": "2021-07"}]
        return data


@pytest.mark.asyncio
async def test_an_ask_about_a_project_the_same_batch_attaches_to_a_held_position_is_not_raised():
    profile = _profile()
    profile.projects = []
    result = await reconcile(profile, {"answer": "x"}, "cv_upload", _ImportAsker(), "de")

    assert result.ambiguities == []


@pytest.mark.asyncio
async def test_the_same_batch_with_a_standalone_project_still_asks():
    profile = _profile()
    profile.projects = []
    result = await reconcile(profile, {"answer": "x"}, "cv_upload", _ImportAsker(parent=None), "de")

    assert len(result.ambiguities) == 1


# ── D-25 (delivery run 5) — a parent whose dates cannot hold the project ─────


class _ParentPicker:
    """An import batch that brings the project in under ``parent``."""

    def __init__(self, parent, start="2020-12", end="2021-07", ask=False):
        self.parent, self.start, self.end, self.ask = parent, start, end, ask

    async def aparse_json(self, prompt, **kw):
        ops = [{"op": "upsert_project", "ref": "p9", "name": "Neues Projekt", "parent": self.parent,
                "start_date": self.start, "end_date": self.end}]
        amb = [{"question": "Which role?", "options": ["A", "B"],
                "context": {"project": "Neues Projekt"}}] if self.ask else []
        return {"ops": ops, "ambiguities": amb, "denials": []}


def _profile_three():
    from applire.schemas.profile import WorkEntry

    profile = _profile()
    profile.projects = []
    profile.work_experience.append(
        WorkEntry(id="44444444-4444-4444-8444-444444444444", company="Nordwerk AG", role="Director",
                  start_date="2024-12", is_current=True))
    profile.work_experience[1].is_current = False
    profile.work_experience[1].end_date = "2024-11"
    return profile


def _upsert(result):
    from applire.services.profile.reconcile.ops import UpsertProject

    [op] = [o for o in result.ops if isinstance(o, UpsertProject)]
    return op


@pytest.mark.asyncio
async def test_a_parent_whose_dates_do_not_touch_the_project_is_dropped_and_asked():
    result = await reconcile(_profile_three(), {"answer": "x"}, "cv_upload", _ParentPicker("id_w3"), "de")

    assert _upsert(result).parent is None
    [rc] = result.ambiguities
    assert rc.option_keys == [f"entry:{W1}", f"entry:{W2}"]  # the two positions the dates fit
    assert rc.context["kind"] == "project_parent" and rc.context["project"] == "Neues Projekt"


@pytest.mark.asyncio
async def test_an_overlapping_parent_is_kept_and_nothing_is_asked():
    result = await reconcile(_profile_three(), {"answer": "x"}, "cv_upload", _ParentPicker("id_w1"), "de")

    assert _upsert(result).parent == W1
    assert result.ambiguities == []


@pytest.mark.asyncio
async def test_an_undated_project_is_not_checked():
    result = await reconcile(_profile_three(), {"answer": "x"}, "cv_upload",
                             _ParentPicker("id_w3", start=None, end=None), "de")

    assert _upsert(result).parent == "44444444-4444-4444-8444-444444444444"


@pytest.mark.asyncio
async def test_one_fitting_position_leaves_the_project_standalone_without_a_question():
    result = await reconcile(_profile_three(), {"answer": "x"}, "cv_upload",
                             _ParentPicker("id_w3", start="2019-01", end="2019-06"), "de")

    assert _upsert(result).parent is None
    assert result.ambiguities == []


@pytest.mark.asyncio
async def test_the_models_own_ask_about_the_project_is_not_doubled():
    result = await reconcile(_profile_three(), {"answer": "x"}, "cv_upload", _ParentPicker("id_w3", ask=True), "de")

    assert len(result.ambiguities) == 1
