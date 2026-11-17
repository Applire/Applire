# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q10 — SEAMS: the generation-seam stamp (``stamp_for_generation``) at
each call site, driving the real background task once and asserting on what the
downstream reader received.

* CV    — ``cv._render_cv_background``: a posting term is not "coverage already
          achieved" for the terminal corrector (harness of test_hz8_w_cv_durations).
* letter — ``cover_letter._render_cover_letter_background``: the writer receives the
          stamped row (harness of test_b6_l_seam_letter).
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest

_HERE = Path(__file__).parent


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_q10_{name}", _HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_hz8 = _load("test_hz8_w_cv_durations")
_b6l = _load("test_b6_l_seam_letter")
letter_db = _b6l.letter_db  # the fixture, re-exported for this module

#: the vault says "Engineering leader shipping production systems." — the posting's
#: "Engineering leadership" is a word the person never wrote.
_CV_LEDGER = [{
    "concept": "Engineering leadership", "surface_forms": ["Engineering leadership"], "claimable": True,
    "status": "direct", "sources": ["required"], "fit_weight": 1.0, "evidence": "Engineering leader.",
}]


@pytest.mark.asyncio
async def test_SEAM_cv_generation_stamps_the_posting_term_before_the_coverage_lists(monkeypatch):
    prompts = await _hz8._terminal_corrector_prompts(monkeypatch, _CV_LEDGER)
    assert prompts, "the terminal review's corrector prompt was never built"
    line = next(ln for ln in prompts[0].splitlines() if ln.startswith("  - Engineering leadership"))
    assert "MET IN THE PROFILE'S WORDS" in line and "[forms:" not in line
    achieved = prompts[0].split("COVERAGE ALREADY ACHIEVED", 1)
    assert len(achieved) == 1 or "Engineering leadership" not in achieved[1]


_LETTER_LEDGER = [
    _b6l._row("Apache Camel", "direct", claimable=True, evidence="Built 40 partner interfaces on Apache Camel"),
    # the vault describes the integration work, never in the posting's words
    _b6l._row("Enterprise integration architecture", "direct", claimable=True,
              evidence="Designed the LIMS-MES-ERP integration",
              backing={"quote": "Designed the LIMS-MES-ERP integration for clinical manufacturing",
                       "unit_path": "work_experience[0].responsibilities[0]"}),
]


@pytest.mark.asyncio
async def test_SEAM_letter_generation_hands_the_writer_the_stamped_row(letter_db, monkeypatch):
    from applire.services import posting_terms as pt

    captured = await _b6l._run(letter_db, _LETTER_LEDGER, _b6l.JD_3, monkeypatch)
    ledger = captured["writer_kwargs"]["keyword_ledger"]
    row = next(r for r in ledger if r["concept"] == "Enterprise integration architecture")
    assert pt.is_posting_term_open(row)
    camel = next(r for r in ledger if r["concept"] == "Apache Camel")
    assert not pt.is_posting_term_open(camel)
