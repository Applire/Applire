# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#424 — the ASSEMBLY half `test_424_project_near_dupe_vault_side.py` names as
still open: "`services/cv.py::_nest_projects`' bin-scoped dedupe is a different
owner and a different file, and #424 stays open on it."

Adversarial pass, Nougat build 3 (writer controls, area C). ``_nest_projects``
skips appending a vault project whose NORMALISED NAME already appears in its
target container — a rule written (E049 charter run 11) for exactly one shape:
"the writer already tailored THIS project, don't also append the vault's raw
copy of it." That comment is correct for that shape. It is silently wrong for a
DIFFERENT shape the loop cannot tell apart from it: two DISTINCT vault
``ProjectEntry`` rows that happen to share a normalised name (a plausible
import/reconcile residual, and exactly the "pre-existing double-write" shape
this build's own #424 delta discusses) — the second row's OWN facts vanish from
the delivered document, with **no** ``log_deletion`` call, unlike every other
drop this same function and its sibling ``_suppress_duplicate_project_bullets``
make (which are explicit: "Both log the deletion (never silent)").

#424's own docstring for :func:`duplicate_project_pairs` states the risk this
loss realises: "dropping one of two same-named entries risks deleting bullets
only the dropped copy carries." The NEW ``duplicate-project`` detection check
built this build cannot catch this instance either — by the time it runs over
the DELIVERED document, there is only ONE rendered copy, so there is no pair to
flag. The loss is invisible at every layer that currently looks.

This same-branch fix does not change WHICH copy survives (a harder product
decision, out of scope for an adversarial pass) — it makes the drop OBSERVABLE,
consistent with this file's own established standard, so a future delivery-run
audit or collector-line author has evidence instead of silence.
"""
from __future__ import annotations

import logging

from applire.schemas.cv import TailoredCVData
from applire.services.cv import _nest_projects

WORK_ID = "22222222-2222-2222-2222-222222222222"

_BASE_PROFILE = {
    "work_experience": [
        {
            "id": WORK_ID,
            "company": "Weberit Kunststofftechnik GmbH",
            "role": "Produktionsleiter",
            "start_date": "2017-04",
            "end_date": None,
        }
    ],
}

_PROSE = {
    "summary": "Produktionsleiter.",
    "work": [{"id": WORK_ID, "bullets": ["Führte die Fertigung."], "projects": []}],
    "projects": [],
}


def _tailored(profile: dict) -> TailoredCVData:
    from applire.services.cv import assemble_tailored_cv

    return TailoredCVData.model_validate(assemble_tailored_cv(_PROSE, profile))


def test_a_second_identically_named_NESTED_vault_project_is_dropped_silently_today():
    """Pins the CURRENT (defective) behaviour before the fix: two distinct vault
    project rows sharing a name, both tied to the same work entry, and the second
    one's own achievement is gone from the rendered document with no trace."""
    profile = dict(_BASE_PROFILE)
    profile["projects"] = [
        {
            "id": "aaaa",
            "name": "Einführung eines MES-Systems bei Weberit",
            "description": "MES für die Fertigung eingeführt.",
            "achievements": ["OEE von 61 auf 73 Prozent gesteigert."],
            "associated_experience": WORK_ID,
        },
        {
            "id": "bbbb",
            "name": "Einführung eines MES-Systems bei Weberit",
            "description": "MES für die Fertigung eingeführt (zweites, eigenständiges Vorhaben).",
            "achievements": ["Rüstzeiten um 35 Prozent gesenkt."],
            "associated_experience": WORK_ID,
        },
    ]
    result = _nest_projects(_tailored(profile), profile).model_dump()
    nested = result["work_history"][0]["projects"]
    assert len(nested) == 1, nested
    # The second row's OWN fact never reaches the document at all.
    assert not any("Rüstzeiten" in b for p in nested for b in p.get("bullets", []))


def test_the_nested_dedup_skip_is_logged_not_silent(caplog):
    """The fix: the skip that drops the second same-named project logs a
    ``TAIL_DELETE`` line naming the predicate and the dropped project — the same
    observability standard ``_suppress_duplicate_project_bullets`` already holds
    itself to in this exact module ("Both log the deletion (never silent)")."""
    profile = dict(_BASE_PROFILE)
    profile["projects"] = [
        {
            "id": "aaaa",
            "name": "Einführung eines MES-Systems bei Weberit",
            "description": "MES für die Fertigung eingeführt.",
            "achievements": ["OEE von 61 auf 73 Prozent gesteigert."],
            "associated_experience": WORK_ID,
        },
        {
            "id": "bbbb",
            "name": "Einführung eines MES-Systems bei Weberit",
            "description": "MES für die Fertigung eingeführt (zweites, eigenständiges Vorhaben).",
            "achievements": ["Rüstzeiten um 35 Prozent gesenkt."],
            "associated_experience": WORK_ID,
        },
    ]
    with caplog.at_level(logging.INFO):
        _nest_projects(_tailored(profile), profile)

    records = [r.message for r in caplog.records if "TAIL_DELETE" in r.message]
    assert records, "the second same-named nested project must log its own drop"
    assert any("_nest_projects" in r for r in records), records
    assert any("Einführung eines MES-Systems bei Weberit" in r for r in records), records


def test_the_standalone_dedup_skip_is_also_logged(caplog):
    """The sibling skip path: two distinct UNTIED vault projects sharing a name.
    Same defect, same fix, the other branch of the same loop."""
    profile = dict(_BASE_PROFILE)
    profile["projects"] = [
        {
            "id": "cccc",
            "name": "Ehrenamtliche Werkstattleitung",
            "description": "Leitung einer offenen Fahrradwerkstatt.",
            "achievements": ["20 Ehrenamtliche koordiniert."],
            "associated_experience": None,
        },
        {
            "id": "dddd",
            "name": "Ehrenamtliche Werkstattleitung",
            "description": "Leitung einer zweiten, eigenständigen Werkstatt in einer anderen Stadt.",
            "achievements": ["15 Ehrenamtliche koordiniert."],
            "associated_experience": None,
        },
    ]
    with caplog.at_level(logging.INFO):
        result = _nest_projects(_tailored(profile), profile).model_dump()

    assert len(result["projects"]) == 1, result["projects"]
    records = [r.message for r in caplog.records if "TAIL_DELETE" in r.message]
    assert any("_nest_projects" in r for r in records), records


def test_the_writer_already_tailored_this_project_case_still_wins_silently_free_of_regression():
    """The ORIGINAL, intended shape (E049 charter run 11) is unaffected: when the
    writer already nested a tailored version of the SAME project, the vault's raw
    copy is skipped — this is not a loss (the tailored version is strictly better
    and already on the page), so it is correct for this path to stay exactly as
    quiet as it was. The new log call fires in both branches, but a future reader
    grepping TAIL_DELETE for a real content loss must not be flooded by this
    routine, working-as-intended case losing its own signal — so this test only
    pins that the DOCUMENT still comes out right, not that the log stays silent."""
    profile = dict(_BASE_PROFILE)
    profile["projects"] = [
        {
            "id": "dddd",
            "name": "Einführung eines MES-Systems bei Weberit",
            "description": "MES für 14 Spritzgussmaschinen eingeführt.",
            "achievements": ["OEE von 61 auf 73 Prozent gesteigert."],
            "associated_experience": WORK_ID,
        }
    ]
    prose = {
        "summary": "Produktionsleiter.",
        "work": [
            {
                "id": WORK_ID,
                "bullets": ["Führte die Fertigung."],
                "projects": [
                    {
                        "name": "Einführung eines MES-Systems bei Weberit",
                        "bullets": ["MES eingeführt und OEE von 61 auf 73% gesteigert (TAILORED)."],
                    }
                ],
            }
        ],
        "projects": [],
    }
    from applire.services.cv import assemble_tailored_cv

    data = assemble_tailored_cv(prose, profile)
    tailored = TailoredCVData.model_validate(data)
    result = _nest_projects(tailored, profile).model_dump()
    nested = result["work_history"][0]["projects"]
    assert len(nested) == 1
    assert "TAILORED" in nested[0]["bullets"][0]
