# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#764 (ADR-061 cl. 7 amended 2026-10-16, FMEA SF-VAULT.16, ruling H-2).

The Health check for skill spans laundered to ``transcribed`` before the
2026-10-07 fix: a transcribed span that (i) no source text states and (ii)
equals phase 1's own re-derivation from the role dates.

Done-when (issue): finds a seeded laundered span; finds the laundered spans on
a CAPTURED pre-fix vault (the 2026-09-19 blind Kaile probe — a synthetic
persona, prompt-view shape, ``backend/tests/files/span_provenance/``); silent
on a clean one. Plus every honest shape the gate exists to keep silent — above
all a candidate's own figure the re-derivation would INFLATE.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from applire.schemas.profile import MasterProfileData
from applire.services.profile.health import assess_health
from applire.services.profile.span_provenance import laundered_spans

TODAY = date(2026, 10, 16)
_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "files" / "span_provenance" / "kaile_probe_2026-09-19_vault.json"
)


def _profile(skills, *, bullets=None, history=None, denied=None):
    meta = {}
    if history is not None:
        meta["enrichment_history"] = history
    if denied is not None:
        meta["denied_concepts"] = denied
    blob = {
        "personal_info": {"name": "Lena Brandt"},
        "work_experience": [
            {
                "id": "w1",
                "company": "Talwerk Feinmechanik GmbH",
                "role": "Controllerin",
                "start_date": "2019-02",
                "responsibilities": bullets or ["Monatsabschluss mit SAP CO und SAP FI verantwortet."],
            }
        ],
        "skills": skills,
    }
    if meta:
        blob["metadata"] = meta
    return MasterProfileData.model_validate(blob)


def _skill(name, years, source, **kw):
    return {"id": f"s-{name}", "name": name, "years_experience": years, "source": source, **kw}


# ── finds ────────────────────────────────────────────────────────────────────


def test_seeded_laundered_span_is_found():
    """SAP CO: roles naming it span 02/2019 → 7.6 y on 2026-09-08 and 7.7 y
    today, phase 1 stores 8 at both ends; the vault says 8 'transcribed' and no
    text states eight."""
    p = _profile([_skill("SAP CO", 8, "transcribed")])
    (span,) = laundered_spans(p, TODAY)
    assert (span.skill_id, span.skill_name, span.span_years, span.recomputed_years) == (
        "s-SAP CO", "SAP CO", 8, 8,
    )


def _kvp_profile(years, *, bullets=None, denied=None, start="2017-04", end=None):
    blob = {
        "personal_info": {"name": "Lena Brandt"},
        "work_experience": [{
            "id": "w1", "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiterin",
            "start_date": start, "end_date": end,
            "responsibilities": bullets or ["KVP-Routinen und SMED-Workshops eingefuehrt."],
        }],
        "skills": [_skill("KVP", years, "transcribed")],
    }
    if denied:
        blob["metadata"] = {"denied_concepts": denied}
    return MasterProfileData.model_validate(blob)


def test_a_value_one_below_an_open_roles_rederivation_is_not_flagged():
    """Ruling ADV-2 (exact match only): KVP since 04/2017 re-derives 10 today;
    a stored 9 — laundered or honest — is not flagged, because its recompute
    would RAISE the number (the adversarial pass's Django 7 → 8)."""
    assert laundered_spans(_kvp_profile(9), TODAY) == []
    assert [s.span_years for s in laundered_spans(_kvp_profile(10), TODAY)] == [10]


def test_an_honest_value_one_below_a_closed_roles_span_is_never_flagged():
    p = _kvp_profile(5, start="2011-08", end="2017-03")
    assert laundered_spans(p, TODAY) == []
    assert [s.span_years for s in laundered_spans(_kvp_profile(6, start="2011-08", end="2017-03"), TODAY)] == [6]


@pytest.mark.parametrize("start,end", [("2017-04", None), ("2019-02", None), ("2011-08", "2017-03"), ("2020-01", "2024-06")])
@pytest.mark.parametrize("delta", [-2, -1, 0, 1, 2])
def test_PROPERTY_a_flag_never_moves_the_number(start, end, delta):
    """ADV-2: whatever N is, a flagged span's recompute target equals N —
    open and closed roles alike — so the action can only change the label."""
    from applire.services.skill_enrichment import stored_span_years

    probe = _kvp_profile(1, start=start, end=end)
    c = stored_span_years(probe, "KVP", TODAY)
    n = max(1, c + delta)
    for span in laundered_spans(_kvp_profile(n, start=start, end=end), TODAY):
        assert span.recomputed_years == span.span_years == n


def test_an_open_roles_value_below_todays_rederivation_is_silent():
    p = _profile([_skill("SAP CO", 7, "transcribed")])
    assert laundered_spans(p, TODAY) == []


def test_an_employer_tenure_in_a_denial_dossier_does_not_silence_a_skill():
    """Adversarial finding 2 (corpus vault 2026-09-13): "Meine neun Jahre bei
    Weberit …" is about the employer, not KVP; it silenced five laundered nines."""
    denied = [{
        "concept": "Lebensmittelverpackung",
        "statement": "Meine zehn Jahre bei Weberit sind Kunststofftechnik, keine Lebensmittel.",
        "source": "interview",
        "date": "2026-09-01T00:00:00+00:00",
    }]
    assert [s.skill_name for s in laundered_spans(_kvp_profile(10, denied=denied), TODAY)] == ["KVP"]


def test_a_statement_naming_the_skill_next_to_the_duration_silences_it():
    p = _kvp_profile(10, bullets=["Seit zehn Jahren verantworte ich KVP an der Linie."])
    assert laundered_spans(p, TODAY) == []


def test_captured_pre_fix_vault_flags_the_two_laundered_spans():
    blob = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    p = MasterProfileData.model_validate(blob)
    found = sorted((s.skill_name, s.span_years, s.recomputed_years) for s in laundered_spans(p, TODAY))
    assert found == [("SAP CO", 8, 8), ("SAP FI", 8, 8)]


def test_the_captured_vault_is_silent_once_the_spans_are_computed():
    blob = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    for s in blob["skills"]:
        if s["name"] in ("SAP CO", "SAP FI"):
            s["source"] = "computed"
    assert laundered_spans(MasterProfileData.model_validate(blob), TODAY) == []


def test_the_health_hub_carries_the_issue_with_its_structured_fields():
    p = _profile([_skill("SAP CO", 8, "transcribed")])
    issues = [i for i in assess_health(p).issues if i.thread == "span_provenance"]
    assert len(issues) == 1
    issue = issues[0]
    assert issue.id == "span_provenance:SAP CO"
    assert issue.profile_mismatch_severity == "review"
    assert (issue.skill_id, issue.skill_name, issue.span_years) == ("s-SAP CO", "SAP CO", 8)
    assert issue.recomputed_years in (8, 9)  # the real clock; 9 only from 2027-08


# ── silent ───────────────────────────────────────────────────────────────────


def test_silent_on_a_clean_vault_with_computed_spans():
    p = _profile([_skill("SAP CO", 8, "computed"), _skill("SAP FI", 8, "computed")])
    assert laundered_spans(p, TODAY) == []
    assert not [i for i in assess_health(p).issues if i.thread == "span_provenance"]


def test_silent_when_a_bullet_states_the_span():
    p = _profile(
        [_skill("SAP CO", 8, "transcribed")],
        bullets=["Seit acht Jahren Monatsabschluss mit SAP CO und SAP FI."],
    )
    assert laundered_spans(p, TODAY) == []


def test_silent_when_a_denial_statement_states_the_span():
    p = _profile(
        [_skill("SAP CO", 8, "transcribed")],
        denied=[{
            "concept": "SAP S/4HANA",
            "statement": "S/4HANA nie eingesetzt, aber 8 Jahre SAP CO im ECC.",
            "source": "interview",
            "date": "2026-09-01T00:00:00+00:00",
        }],
    )
    assert laundered_spans(p, TODAY) == []


def test_silent_when_the_enrichment_trail_holds_the_candidates_words():
    """An interview answer recorded in the trail (a bullet's new value)."""
    p = _profile(
        [_skill("SAP CO", 8, "transcribed")],
        history=[{
            "timestamp": "2026-09-20T10:00:00+00:00",
            "source": "interview",
            "changes": [{
                "section": "work_experience", "field": "responsibilities", "action": "merged",
                "new_value": ["Acht Jahre SAP CO im Werkscontrolling."],
            }],
        }],
    )
    assert laundered_spans(p, TODAY) == []


def test_silent_when_the_candidate_set_the_span_by_hand():
    p = _profile(
        [_skill("SAP CO", 8, "transcribed")],
        history=[{
            "timestamp": "2026-09-21T10:00:00+00:00",
            "source": "manual_edit",
            "changes": [{
                "section": "skills", "field": "SAP CO", "action": "updated",
                "old_value": {"name": "SAP CO", "years_experience": 6},
                "new_value": {"name": "SAP CO", "years_experience": 8},
            }],
        }],
    )
    assert laundered_spans(p, TODAY) == []


def test_the_rationale_is_system_text_and_never_counts_as_a_statement():
    p = _profile(
        [_skill("SAP CO", 8, "transcribed")],
        history=[{
            "timestamp": "2026-09-21T10:00:00+00:00",
            "source": "cv_upload",
            "changes": [{
                "section": "skills", "field": "name", "action": "added",
                "new_value": "SAP CO", "rationale": "reconcile: 8 Jahre",
            }],
        }],
    )
    assert len(laundered_spans(p, TODAY)) == 1


def test_never_flags_a_figure_the_rederivation_would_inflate():
    """H-2 option A: the candidate's own '6 Jahre SAP CO' (CV text long deleted)
    against roles spanning 7.7 years — a recompute would raise THEIR number."""
    p = _profile([_skill("SAP CO", 6, "transcribed")])
    assert laundered_spans(p, TODAY) == []


def test_never_flags_a_span_above_the_rederivation():
    p = _profile([_skill("SAP CO", 10, "transcribed")])
    assert laundered_spans(p, TODAY) == []


def test_silent_when_no_dated_role_names_the_skill():
    p = _profile([_skill("Kostenrechnung", 8, "transcribed")])
    assert laundered_spans(p, TODAY) == []


@pytest.mark.parametrize("extra", [{"category": "language"}, {"status": "denied"}])
def test_language_and_denied_skills_are_out_of_scope(extra):
    p = _profile([_skill("SAP CO", 8, "transcribed", **extra)])
    assert laundered_spans(p, TODAY) == []


def test_a_transcribed_skill_without_a_span_is_silent():
    p = _profile([_skill("SAP CO", None, "transcribed")])
    assert laundered_spans(p, TODAY) == []
