# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q5 (b), RULING Q5-1 (2026-11-15): a project the person's own words
create without a position is asked once — closed answers are the held positions
whose dates overlap the project, plus "Eigenständiges Projekt (keiner Position
zugeordnet)"; the answer writes ``associated_experience``; "standalone" writes
nothing.

Captured shape (delivery 1b run 3, synthetic persona Katrin Hoffmann): the answer
"Das habe ich 2022 im Sondermaschinenbereich eingeführt …" became
``upsert_project`` "Einführung der Percentage-of-Completion-Bewertung", start
2022, ``parent: null``; the delivered CV then nested it under w1 and the Oracle
graded the bullet misattributed. Synthetic profile below."""
from __future__ import annotations

import pytest

from applire.schemas.profile import MasterProfileData, PendingConfirmation, ProfileMetadata, ProjectEntry, WorkEntry
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.ops import ResolveConfirmation, UpsertProject

W1, W2, W3 = ("78903cb3-0000-4000-8000-000000000001", "cbaabffc-0000-4000-8000-000000000002",
              "8056f83e-0000-4000-8000-000000000003")
POC = "Einführung der Percentage-of-Completion-Bewertung"


def _profile() -> MasterProfileData:
    return MasterProfileData(work_experience=[
        WorkEntry(id=W1, company="Schwarzwald Präzision GmbH", role="Financial Controller",
                  start_date="2019-02", is_current=True),
        WorkEntry(id=W2, company="Kinzig Metall AG", role="Junior Controller",
                  start_date="2015-09", end_date="2019-01"),
        WorkEntry(id=W3, company="Ortenau Treuhand", role="Werkstudentin Rechnungswesen",
                  start_date="2013-04", end_date="2015-08"),
    ])


class _Reconciler:
    def __init__(self, *, parent=None, start="2022", end=None, target=None, name=POC, ask=False):
        self.op = {"op": "upsert_project", "ref": "p3", "name": name, "parent": parent,
                   "target": target, "start_date": start, "end_date": end}
        self.ask = ask

    async def aparse_json(self, prompt, **kw):
        amb = [{"question": "Which role?", "options": ["A", "B"], "context": {"project": POC}}] if self.ask else []
        return {"ops": [self.op], "ambiguities": amb, "denials": []}


async def _run(source="interview", profile=None, **kw):
    return await reconcile(profile or _profile(), {"answer": "Das habe ich 2022 eingeführt."}, source,
                           _Reconciler(**kw), "de")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["interview", "agent_interview", "testimony"])
async def test_a_new_project_without_a_position_is_asked_once(source):
    result = await _run(source)
    [up] = [o for o in result.ops if isinstance(o, UpsertProject)]
    assert up.parent is None  # the project is created standalone until answered
    [rc] = result.ambiguities
    assert rc.question_i18n["de"] == f"Zu welcher Position gehört das Projekt „{POC}“?"
    assert rc.option_keys == [f"entry:{W1}", "standalone"]  # only the position the dates overlap
    assert [o["de"] for o in rc.options_i18n] == [
        "Financial Controller · Schwarzwald Präzision GmbH (2019-02 – heute)",
        "Eigenständiges Projekt (keiner Position zugeordnet)",
    ]
    assert rc.context["kind"] == "project_parent" and rc.context["project"] == POC


@pytest.mark.asyncio
async def test_an_undated_project_offers_every_position():
    [rc] = (await _run(start=None)).ambiguities
    assert rc.option_keys == [f"entry:{W1}", f"entry:{W2}", f"entry:{W3}", "standalone"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [
    {"source": "cv_upload"},                     # an import carries its own structure
    {"parent": "id_w1"},                         # the reconciler placed it
    {"target": "id_p1"},                         # an existing project
    {"start": "2009", "end": "2010"},             # no held position overlaps
])
async def test_no_ask_outside_the_case(kw):
    assert (await _run(**kw)).ambiguities == []


@pytest.mark.asyncio
async def test_a_held_project_of_that_name_is_never_asked_again():
    profile = _profile()
    profile.projects = [ProjectEntry(id="p-held", name=POC, associated_experience=None)]
    assert (await _run(profile=profile)).ambiguities == []


@pytest.mark.asyncio
@pytest.mark.parametrize("held,spoken", [
    ("POC Bewertung", "POC-Bewertung"),          # the reviewer's probe (adv-2a F7)
    (POC, POC.replace("-", " ").upper()),
    (POC, f"{POC}."),
])
async def test_a_spelling_variant_of_a_held_project_is_not_asked(held, spoken):
    """MD-Q11 F7: the compose lift's own name key decides "the same project"."""
    profile = _profile()
    profile.projects = [ProjectEntry(id="p-held", name=held, associated_experience=None)]
    assert (await _run(profile=profile, name=spoken)).ambiguities == []


@pytest.mark.asyncio
async def test_a_different_project_is_still_asked():
    profile = _profile()
    profile.projects = [ProjectEntry(id="p-held", name="POC Bewertung", associated_experience=None)]
    assert len((await _run(profile=profile, name="POC Bewertung Anlagenbau")).ambiguities) == 1


@pytest.mark.asyncio
async def test_the_models_own_ask_is_not_doubled():
    result = await _run(ask=True)
    assert len(result.ambiguities) == 1


def _parked(rc) -> MasterProfileData:
    profile = _profile()
    profile.projects = [ProjectEntry(id="p-new", name=POC, associated_experience=None, start_date="2022")]
    profile.metadata = ProfileMetadata(pending_confirmations=[PendingConfirmation(
        confirmation_id="c-1", question=rc.question, options=list(rc.options), context=dict(rc.context),
        question_i18n=rc.question_i18n, options_i18n=rc.options_i18n, option_keys=list(rc.option_keys),
    )])
    return profile


@pytest.mark.asyncio
async def test_the_answer_writes_the_position_and_standalone_writes_nothing():
    [rc] = (await _run()).ambiguities
    profile = _parked(rc)
    _q, shown = profile.metadata.pending_confirmations[0].rendered("de")
    out = apply_ops(profile, [ResolveConfirmation(confirmation_id="c-1", chosen_option=shown[0])], "review")
    assert out.profile.projects[0].associated_experience == W1

    profile = _parked(rc)
    out = apply_ops(profile, [ResolveConfirmation(confirmation_id="c-1", chosen_option=shown[1])], "review")
    assert out.profile.projects[0].associated_experience is None
    assert [i.reason for i in out.not_applied] == ["confirmation_already_present"]
    assert out.profile.metadata.pending_confirmations == []
