# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""M5.1.2 / M5.1.3 — hermetic smoke test for the opt-in extraction probe.

`scripts/extraction_probe.py` is never a CI job (it needs a provider key). This
keeps it from rotting the way `tests/unit/test_model_matrix_harness.py` keeps
the model-matrix harness honest: the fixtures load, the production prompt
builders are the ones it calls, and each scorer actually fires on the shape it
claims to measure — asserted in BOTH directions, so a scorer that always says
"clean" cannot pass.
"""

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "extraction_probe", REPO_ROOT / "scripts" / "extraction_probe.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


ep = _load_probe()


def test_every_probe_names_a_committed_fixture():
    for name, cfg in ep.PROBES.items():
        path = ep.FIXTURE_DIR / cfg["fixture"]
        assert path.is_file(), f"{name} names a missing fixture"
        assert path.read_text(encoding="utf-8").strip()


def test_the_fixtures_are_synthetic():
    """E060 §3.1's architecture boundary — the same assertion the model-matrix
    fixtures carry. A real CV must never reach a committed fixture."""
    for cfg in ep.PROBES.values():
        text = (ep.FIXTURE_DIR / cfg["fixture"]).read_text(encoding="utf-8")
        assert "example.com" in text
        assert "Jonas Weiler" in text


@pytest.mark.parametrize("door", ["agent", "web"])
@pytest.mark.parametrize("probe", sorted(ep.PROBES))
def test_the_probe_builds_the_production_prompt_for_each_door(door, probe):
    """The builders come from the prompt modules themselves — a reimplementation
    would measure a prompt nothing ships."""
    raw = (ep.FIXTURE_DIR / ep.PROBES[probe]["fixture"]).read_text(encoding="utf-8")
    system, user = ep.build_prompt(door, raw)
    assert len(system) > 5000
    assert raw.splitlines()[0] in user


def test_per_entry_tech_fires_on_a_backfilled_tool():
    cfg = ep.PROBES["per_entry_tech"]
    bad = {
        "work_history": [
            {"company": "Kaltenbach Kunststofftechnik GmbH", "technologies": ["SAP PP"]},
            {"company": "Rheinstahl Umformtechnik GmbH", "technologies": ["SAP PP"]},
        ]
    }
    m = ep.score_per_entry_tech(bad, "agent", cfg)
    assert m["backfill_violation"] is True
    assert m["owning_role_kept_the_tool"] is True
    assert m["valid_entries_violation"] is False


def test_per_entry_tech_is_clean_on_the_correct_shape():
    """The other direction — a scorer that always violates measures nothing."""
    cfg = ep.PROBES["per_entry_tech"]
    good = {
        "work_history": [
            {"company": "Kaltenbach Kunststofftechnik GmbH", "technologies": []},
            {"company": "Rheinstahl Umformtechnik GmbH", "technologies": ["SAP PP", "MS Excel"]},
        ]
    }
    m = ep.score_per_entry_tech(good, "agent", cfg)
    assert m["backfill_violation"] is False
    assert m["owning_role_kept_the_tool"] is True
    assert m["valid_entries_violation"] is False


def test_valid_entries_fires_on_a_company_less_shell_entry():
    cfg = ep.PROBES["per_entry_tech"]
    bad = {
        "work_history": [
            {"company": "Kaltenbach Kunststofftechnik GmbH", "technologies": []},
            {"company": "Rheinstahl Umformtechnik GmbH", "technologies": ["SAP PP"]},
            {"company": "", "role": "stellvertretender Werkleiter"},
        ]
    }
    m = ep.score_per_entry_tech(bad, "agent", cfg)
    assert m["empty_company_entries"] == 1
    assert m["entry_count_violation"] is True
    assert m["valid_entries_violation"] is True


def test_the_web_door_scorer_reads_work_experience():
    """The two doors name the same section differently — the scorer must read
    the door's own key or it would score every web run as zero entries."""
    cfg = ep.PROBES["per_entry_tech"]
    data = {"work_experience": [
        {"company": "Kaltenbach Kunststofftechnik GmbH", "technologies": ["SAP PP"]},
        {"company": "Rheinstahl Umformtechnik GmbH", "technologies": ["SAP PP"]},
    ]}
    m = ep.score_per_entry_tech(data, "web", cfg)
    assert m["n_entries"] == 2
    assert m["backfill_violation"] is True


def test_cert_issuer_fires_only_on_an_issuer_the_source_never_states():
    cfg = ep.PROBES["cert_issuer"]
    data = {"certifications": [
        {"name": "Herstellerschulung Spritzgiessmaschinen", "issuing_organization": "Hersteller"},
        {"name": "Ausbildereignung (AEVO)", "issuing_organization": "IHK Koblenz"},
        {"name": "ISO 9001 Lead Auditor", "issuing_organization": "TUEV Rheinland"},
        {"name": "REFA-Grundschein"},
        {"name": "Staplerschein", "issuing_organization": None},
    ]}
    m = ep.score_cert_issuer(data, "agent", cfg)
    assert m["invented_issuer_violation"] is True
    assert [i["name"] for i in m["invented_issuers"]] == ["Herstellerschulung Spritzgiessmaschinen"]
    assert m["cert_count_violation"] is False


def test_cert_issuer_is_clean_when_no_issuer_is_invented():
    cfg = ep.PROBES["cert_issuer"]
    data = {"certifications": [
        {"name": "Herstellerschulung Spritzgiessmaschinen"},
        {"name": "Ausbildereignung (AEVO)", "issuing_organization": "IHK Koblenz"},
        {"name": "ISO 9001 Lead Auditor", "issuing_organization": "TÜV Rheinland"},
        {"name": "REFA-Grundschein"},
        {"name": "Staplerschein"},
    ]}
    m = ep.score_cert_issuer(data, "agent", cfg)
    assert m["invented_issuer_violation"] is False
    assert m["cert_count_violation"] is False


def test_summarise_reports_one_rate_per_violation_key():
    records = [
        {"metrics": {"backfill_violation": True, "valid_entries_violation": False,
                     "entry_count_violation": False}},
        {"metrics": {"backfill_violation": False, "valid_entries_violation": False,
                     "entry_count_violation": False}},
        {"error": "boom"},
    ]
    s = ep.summarise(records, "per_entry_tech")
    assert s["n"] == 3
    assert s["errors"] == 1
    assert s["rates"]["backfill_violation"] == 0.5


def test_dry_run_needs_no_provider(capsys):
    assert ep.main(["--probe", "per_entry_tech", "--door", "agent", "--dry-run"]) == 0
    assert "probe=per_entry_tech" in capsys.readouterr().out
