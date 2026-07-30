# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Tobias Rosenbaum
"""#328 — ``_apply_role_facts`` must be the ONLY writer of the quantified role
facts, structurally rather than by instruction.

The three fields (``team_size`` / ``budget_managed`` / ``industry_context``) are
rendered as document **furniture** — a labelled per-role sub-header line — which
presents them to a recruiter as authoritative structured data rather than as
prose the reader discounts as authored. That makes a wrong value here worse than
a wrong sentence, so the write must fail SAFE.

``_apply_role_facts``'s docstring claims the writer "can never mint or invent a
figure" because the writer prompt's schema does not mention these fields. That
is an **instruction**, not a guarantee: ``TailoredWorkEntry`` carries the fields,
Pydantic's default ``extra`` policy accepts them, and this repository's own
history records a prompt schema acting as a dead control (#229 — the import
door's schema listed fields the extractor never populated).

So the property under test is not "the model is well-behaved". It is: whatever
appears on the tailored entry when this pass runs, the vault decides — and where
the vault is silent, the field is silent.
"""

import pytest

from applire.schemas.cv import TailoredCVData, TailoredWorkEntry
from applire.services.cv import _apply_role_facts


def _tailored(**work_overrides) -> TailoredCVData:
    entry = {
        "id": "w1",
        "company": "Beispiel GmbH",
        "role": "Leiter Produktion",
        "start_date": "2019-03",
        "bullets": ["Shopfloor-Management etabliert"],
    }
    entry.update(work_overrides)
    return TailoredCVData.model_validate({
        "contact": {"name": "Synthetic Candidate"},
        "summary": "",
        "skills": [],
        "work_history": [entry],
        "education": [],
    })


def test_writer_minted_facts_are_cleared_when_the_vault_is_silent():
    """The load-bearing case. The vault holds NO quantified facts for this role;
    the draft carries values anyway. They must not reach the page."""
    tailored = _tailored(
        team_size=999,
        budget_managed="EUR 50m",
        industry_context="Raumfahrt",
    )
    profile_json = {
        "work_experience": [
            {"id": "w1", "company": "Beispiel GmbH", "role": "Leiter Produktion"}
        ]
    }

    result = _apply_role_facts(tailored, profile_json)
    got = result.work_history[0]

    assert got.team_size is None, "a writer-minted team size survived into furniture"
    assert got.budget_managed is None, "a writer-minted budget survived into furniture"
    assert got.industry_context is None, "a writer-minted industry survived into furniture"


def test_writer_minted_facts_are_cleared_when_no_vault_entry_matches():
    """An id mismatch must also fail safe — not leave the draft's own values in
    place. Otherwise a re-keyed or hand-edited entry becomes a laundering path."""
    tailored = _tailored(team_size=999, budget_managed="EUR 50m")
    profile_json = {"work_experience": [{"id": "OTHER", "company": "X", "role": "Y"}]}

    got = _apply_role_facts(tailored, profile_json).work_history[0]

    assert got.team_size is None
    assert got.budget_managed is None


def test_writer_minted_facts_are_cleared_when_the_vault_has_no_work_history():
    """Empty vault must not be an early-return that preserves minted values."""
    tailored = _tailored(team_size=999, industry_context="Raumfahrt")

    got = _apply_role_facts(tailored, {"work_experience": []}).work_history[0]

    assert got.team_size is None
    assert got.industry_context is None


def test_partial_vault_facts_overwrite_the_rest():
    """The vault supplies team_size only; a minted budget must still be cleared."""
    tailored = _tailored(team_size=1, budget_managed="EUR 50m invented")
    profile_json = {
        "work_experience": [{"id": "w1", "team_size": 38, "industry_context": "Anlagenbau"}]
    }

    got = _apply_role_facts(tailored, profile_json).work_history[0]

    assert got.team_size == 38
    assert got.industry_context == "Anlagenbau"
    assert got.budget_managed is None, "minted budget survived alongside real vault facts"


def test_vault_facts_are_copied_through():
    """The happy path still works — this guard must not become a blanket eraser."""
    tailored = _tailored()
    profile_json = {
        "work_experience": [{
            "id": "w1",
            "team_size": 38,
            "budget_managed": "ca. 6 Mio. EUR",
            "industry_context": "Anlagenbau",
        }]
    }

    got = _apply_role_facts(tailored, profile_json).work_history[0]

    assert (got.team_size, got.budget_managed, got.industry_context) == (
        38, "ca. 6 Mio. EUR", "Anlagenbau",
    )


def test_team_size_zero_is_preserved_not_treated_as_absent():
    """0 is a real answer ("no direct reports") and must survive a truthiness test."""
    tailored = _tailored()
    profile_json = {"work_experience": [{"id": "w1", "team_size": 0}]}

    assert _apply_role_facts(tailored, profile_json).work_history[0].team_size == 0


def test_a_non_integer_vault_team_size_does_not_reach_the_page():
    """The vault is authoritative but not trusted to be well-typed."""
    tailored = _tailored()
    profile_json = {"work_experience": [{"id": "w1", "team_size": "circa vierzig"}]}

    assert _apply_role_facts(tailored, profile_json).work_history[0].team_size is None


def test_input_is_not_mutated():
    tailored = _tailored(team_size=999)
    profile_json = {"work_experience": [{"id": "w1", "team_size": 38}]}

    _apply_role_facts(tailored, profile_json)

    assert tailored.work_history[0].team_size == 999, "input was mutated in place"


@pytest.mark.parametrize("bad_profile", [{}, {"work_experience": None}])
def test_malformed_profile_still_fails_safe(bad_profile):
    tailored = _tailored(budget_managed="EUR 50m invented")

    got = _apply_role_facts(tailored, bad_profile).work_history[0]

    assert got.budget_managed is None
