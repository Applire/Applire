# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""786:L113 / L135 (Hazelnut build 2) — SEAM: the v2 CV writer gets the hedged span view.

Only the letter passed ``derived_spans="hedge"`` (#747, ruling T-1), so the CV writer
read a ``computed`` skill span as a plain count and stated it flat — "6 years of Apache
Kafka" on a 5.6-year dated span (v2 gate corpus 5c2f056b, Oracle ``unbacked``), and the
letter copied the CV. Under ``CV_PIPELINE=v2`` the writer's profile AND the review loop's
``source_material`` now carry ``years_experience_derived``; v1 is byte-identical (R-3).
Harness: ``test_593_prompt_facing_profile_view._run_cv`` (the real background task).
"""
from __future__ import annotations

import json
from datetime import date

import pytest

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_seam593", Path(__file__).with_name("test_593_prompt_facing_profile_view.py")
)
_seam593 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_seam593)
_profile_json, _run_cv = _seam593._profile_json, _seam593._run_cv


def _start_years_ago(years: float) -> str:
    today = date.today()
    y, m = divmod(today.year * 12 + (today.month - 1) - int(round(years * 12)), 12)
    return f"{y:04d}-{m + 1:02d}"


def _profile() -> dict:
    p = _profile_json()
    p["work_experience"][0]["start_date"] = _start_years_ago(5.6)
    p["work_experience"][0].setdefault("technologies", []).append("Apache Kafka")
    p["skills"] = [
        {"name": "Apache Kafka", "category": "technical", "years_experience": 6,
         "source": "computed", "status": "confirmed"},
        {"name": "Go", "category": "technical", "years_experience": 4,
         "source": "llm_estimated", "status": "confirmed"},
        {"name": "Python", "category": "technical", "years_experience": 9,
         "source": "transcribed", "status": "confirmed"},
    ]
    return p


def _skills(profile_block: dict) -> dict:
    return {s["name"]: s for s in profile_block.get("skills") or []}


@pytest.mark.asyncio
async def test_SEAM_v2_writer_and_loop_source_get_the_hedged_span(monkeypatch):
    monkeypatch.setenv("CV_PIPELINE", "v2")
    captured = await _run_cv(_profile())
    skills = _skills(captured["writer_profile"])
    kafka = skills["Apache Kafka"]
    assert "years_experience" not in kafka
    assert kafka["years_experience_derived"]["at_least"] == 5
    assert "years_experience" not in skills["Go"]                 # llm_estimated: dropped
    assert skills["Python"]["years_experience"] == 9              # the candidate's own number
    sources = [s for s in captured.get("loop_sources", []) if s]
    assert sources and all('"years_experience_derived"' in s for s in sources)


@pytest.mark.asyncio
async def test_SEAM_v1_writer_keeps_the_plain_view(monkeypatch):
    monkeypatch.setenv("CV_PIPELINE", "v1")
    captured = await _run_cv(_profile())
    skills = _skills(captured["writer_profile"])
    assert skills["Apache Kafka"]["years_experience"] == 6
    assert "years_experience_derived" not in json.dumps(captured["writer_profile"])
