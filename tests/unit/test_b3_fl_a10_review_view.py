# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 3, fix round WP-FL — A-10: the review loop's vault view
carries no `status: "confirmed"` (ADR-078 amended 2026-10-23, clause 4).

The adversarial pass delivered "Kubernetes is also a confirmed skill." The
letter's `grounding_source` (reviewer + corrector, every round) is built over
`exclude_unconfirmed`, so every skill/language/certification in it IS confirmed
and the 52 `"status": "confirmed"` lines say nothing — the reviewer read them as
candidate material and the corrector copied the word. Replay of the captured
reviewer→corrector chain (luna, thinking off, n=5): 5/5 "confirmed" as
captured, 0/5 with the status dropped (no prompt rule added).

Three tiers: the view's semantics · the ONE call site that narrows (seam,
driving the real background task) · byte-identity of the views that must not
change (CV writer, the reconcile prompt every interview turn writes through,
the gap prompt the interview questions come from).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire.services.prompt_view import prompt_profile_view

# Fixtures and harnesses of the two existing view seam suites (tests/unit is
# not a package; its directory is on sys.path under pytest's prepend mode).
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_747_letter_hedge_view_seam import (  # noqa: E402,F401 — pytest fixtures
    letter_db,
    letter_seeded,
)
from test_593_prompt_facing_profile_view import _run_cv  # noqa: E402
from test_593_prompt_facing_profile_view import _profile_json as _profile_593  # noqa: E402


def _vault() -> dict:
    p = _profile_593()
    p["skills"] = [
        {"name": "Kubernetes", "category": "technical", "source": "transcribed", "status": "confirmed"},
        {"name": "Helm", "category": "technical", "source": "transcribed", "status": "unconfirmed"},
    ]
    p["languages"] = [{"language": "Deutsch", "level": "Muttersprache", "status": "confirmed"}]
    p["certifications"] = [{"name": "CKA", "status": "confirmed"}]
    return p


# ── tier 1 — the view ───────────────────────────────────────────────────────


def test_drop_confirmed_removes_only_the_confirmed_status_of_the_three_lists():
    src = _vault()
    view = prompt_profile_view(src, entry_status="drop_confirmed")
    assert view["skills"][0] == {"name": "Kubernetes", "category": "technical", "source": "transcribed"}
    # any other value is information and stays
    assert view["skills"][1]["status"] == "unconfirmed"
    assert "status" not in view["languages"][0]
    assert "status" not in view["certifications"][0]
    # the input is never mutated
    assert src["skills"][0]["status"] == "confirmed"


def test_the_default_keeps_every_status():
    view = prompt_profile_view(_vault())
    assert [s.get("status") for s in view["skills"]] == ["confirmed", "unconfirmed"]
    assert view["languages"][0]["status"] == "confirmed"


@pytest.mark.parametrize("value", [None, "x", [], {"skills": "not a list"}, {"skills": [1, None]}])
def test_malformed_shapes_never_raise(value):
    prompt_profile_view(value, entry_status="drop_confirmed")


# ── tier 2 — the one call site that narrows (seam) ─────────────────────────


async def _run_letter(letter_seeded):
    db, job, cl = letter_seeded
    captured: dict = {}

    async def fake_loop(**kw):
        captured.setdefault("sources", []).append(kw.get("source"))
        return kw["draft"]

    def fake_writer_prompt(**kw):
        captured["cv_data"] = kw.get("cv_data")
        return "PROMPT"

    provider = MagicMock()
    provider.aparse_json = AsyncMock(return_value={"body": {"paragraphs": [
        "Sehr geehrte Damen und Herren,", "x", "Mit freundlichen Grüßen"]}})
    with patch("applire.services.cover_letter.AsyncSessionLocal") as sl, \
         patch("applire.services.cover_letter.get_provider", return_value=provider), \
         patch("applire.services.cover_letter.build_cover_letter_prompt", side_effect=fake_writer_prompt), \
         patch("applire.services.cover_letter.review_and_refine", new=AsyncMock(side_effect=fake_loop)), \
         patch("applire.services.cover_letter_pdf.render_pdf",
               new=AsyncMock(side_effect=RuntimeError("no browser in unit test"))):
        sl.return_value.__aenter__.return_value = db
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)
    return captured


@pytest.mark.asyncio
async def test_SEAM_letter_grounding_source_carries_no_confirmed_status(letter_seeded):
    captured = await _run_letter(letter_seeded)
    sources = [s for s in captured.get("sources", []) if s]
    assert sources, "the letter review loop was never called with a grounding source"
    for src in sources:
        obj, _ = json.JSONDecoder().raw_decode(src)
        skills = (obj.get("profile") or {}).get("skills") or []
        assert {s["name"] for s in skills} >= {"SAP CO", "HGB"}  # content is still there
        assert all("status" not in s for s in skills), skills
        assert '"status": "confirmed"' not in json.dumps(obj["profile"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_SEAM_the_letter_writers_view_is_not_narrowed(letter_seeded):
    """The kwarg is passed at grounding_source only — the writer's no-CV
    `cv_data` (the other letter rendering) keeps its statuses."""
    captured = await _run_letter(letter_seeded)
    skills = (captured.get("cv_data") or {}).get("skills") or []
    assert skills and all(s.get("status") == "confirmed" for s in skills), skills


# ── tier 3 — byte-identity of the views that must not change ───────────────


@pytest.mark.asyncio
async def test_PIN_cv_writer_view_is_byte_identical_to_the_default_view():
    """The CV writer's profile is the DEFAULT view of what it was handed —
    byte for byte — and every confirmed entry still says so."""
    from applire.services import prompt_view as pv

    real = pv.prompt_profile_view
    calls: list = []

    def spy(profile_json, **kw):
        out = real(profile_json, **kw)
        calls.append((json.loads(json.dumps(profile_json)), kw, out))
        return out

    with patch.object(pv, "prompt_profile_view", side_effect=spy):
        captured = await _run_cv(_vault())
    writer = captured["writer_profile"]
    match = [c for c in calls if c[2] is writer]
    assert match, "the CV writer's profile did not come from prompt_profile_view"
    handed, kw, _ = match[0]
    assert "entry_status" not in kw
    got = json.dumps(writer, ensure_ascii=False, sort_keys=True)
    assert got == json.dumps(real(handed), ensure_ascii=False, sort_keys=True)
    assert all(s.get("status") == "confirmed" for s in writer["skills"]), writer["skills"]
    assert writer["languages"][0]["status"] == "confirmed"


def test_PIN_reconcile_prompt_every_interview_turn_writes_through_is_unchanged():
    from applire.prompts.reconcile import build_reconcile_prompt
    from applire.schemas.profile import MasterProfileData

    profile = MasterProfileData.model_validate(_vault())
    prompt = build_reconcile_prompt(profile, {"answer": "Ich habe Kubernetes betrieben."}, "interview")
    expected = json.dumps(prompt_profile_view(profile.model_dump(mode="json")), ensure_ascii=False)
    assert '"status": "confirmed"' in expected
    narrowed = json.dumps(
        prompt_profile_view(profile.model_dump(mode="json"), entry_status="drop_confirmed"),
        ensure_ascii=False,
    )
    assert narrowed not in prompt
    assert '"status": "confirmed"' in prompt


def test_PIN_gap_prompt_the_interview_questions_come_from_keeps_its_statuses():
    from applire.prompts.gap_analysis import build_user_prompt
    from applire.services.gap_inference import PreClassification

    profile = _vault()
    prompt = build_user_prompt(
        {"role_title": "Platform Engineer", "required_skills": ["Kubernetes"],
         "nice_to_have_skills": [], "keywords": []},
        profile, PreClassification(),
    )
    block = prompt.split("CANDIDATE PROFILE:")[1]
    assert '"status": "confirmed"' in block
    assert '"status": "unconfirmed"' in block
