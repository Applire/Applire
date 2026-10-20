# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-095 cl. 3 — every registry entry carries a stability tier.

``internal`` (founder ruling B2-3) carries no 1.x promise and is excluded from
the contract snapshot; ``essential`` is what a fresh install sets. The tiers are
stamped from two tuples, like ``PANEL_KEYS`` — so the failure to guard is a list
naming the wrong thing (System-FMEA ``SF-CONTRACT.6``).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from applire import settings_registry as reg  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]


def _generator():
    spec = importlib.util.spec_from_file_location(
        "generate_env_example", _REPO / "scripts" / "generate_env_example.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_entry_has_a_known_tier():
    assert {e.tier for e in reg.all_settings()} <= set(reg.TIERS)


def test_the_tier_lists_are_what_the_registry_says():
    by_tier = {t: {e.env_var for e in reg.all_settings() if e.tier == t} for t in reg.TIERS}
    assert by_tier["internal"] == set(reg.INTERNAL_KEYS)
    assert by_tier["essential"] == set(reg.ESSENTIAL_KEYS)


def test_the_b2_3_pipeline_switches_are_internal():
    """The founder's ruled set (B2-3) is in the exemption whatever R-1 adds."""
    for name in ("CV_PIPELINE", "LETTER_PIPELINE", "PROMPT_CONTEXT"):
        assert reg.get(name).tier == "internal"


def test_promised_settings_exclude_exactly_the_internal_tier():
    promised = {e.env_var for e in reg.promised_settings()}
    assert promised.isdisjoint(reg.INTERNAL_KEYS)
    assert promised | set(reg.INTERNAL_KEYS) == {e.env_var for e in reg.all_settings()}


def test_an_unregistered_name_in_a_tier_list_is_an_error():
    with pytest.raises(ValueError, match="unregistered"):
        reg._apply_tiers((), ("NO_SUCH_VARIABLE",))
    with pytest.raises(ValueError, match="unregistered"):
        reg._apply_tiers(("NO_SUCH_VARIABLE",), ())


def test_a_name_in_both_lists_is_an_error():
    with pytest.raises(ValueError, match="both"):
        reg._apply_tiers(("CV_PIPELINE",), ("CV_PIPELINE",))


def test_an_unknown_tier_is_refused_at_construction():
    with pytest.raises(ValueError, match="unknown tier"):
        reg.SettingEntry(
            env_var="X_TIER_PROBE", source="config", default="", description="x",
            introduced_in="1.0.0", tier="stable",
        )


def test_the_template_puts_every_internal_entry_in_the_final_block_only():
    text = _generator().render()
    head, sep, tail = text.partition("# Internal — no stability promise")
    assert sep, "the generated template has no internal block"
    for e in reg.all_settings():
        if not e.in_env_example:
            continue
        line = f"{e.env_var}={e.shown_value}"
        if e.tier == "internal":
            assert line in tail and line not in head, e.env_var
        else:
            assert line in head and line not in tail, e.env_var


def test_the_quick_start_names_every_essential_entry():
    text = _generator().render()
    quick = text.split("# Quick start", 1)[1].split("# ====", 1)[0]
    for name in reg.ESSENTIAL_KEYS:
        assert name in quick, name
