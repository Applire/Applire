# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-23 (Hazelnut build 5, delivery run 5): the automatic page ladder silently
emptied the skills section.

The first render of an AI-engineering CV ran to 4 pages on a 2-page target. The
automatic ``target + 1`` ladder (rung 2) then cut every chip that
``skill_jd_tier_fn`` ranked tier 3, and the tier was a LITERAL match against the
posting's terms. The posting asked for "AI agents" and "developer tooling" and never
spelled Python, LangChain or Docker, so 27 written chips became 3 delivered chips.
The cut showed up only in the page-length row, and the card said only
"Kürzer geht es nicht".

Three facts close it (ADR-062 cl. 1: string occurrence, rank order and count, with
no judgement):
1. A chip that a CLAIMABLE ledger row's evidence names verbatim is relevant to this
   job (keyword tier 2).
2. Rung 2 cuts lowest-ranked first, in growing slices, and stops at the page limit.
   The automatic ladder never leaves fewer than ``AUTO_SKILL_FLOOR`` chips.
3. The page card's payload names what the automatic ladder already cut
   (``auto_cuts``).

Synthetic data only.
"""
import copy
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire.services.cv_page_ladder import AUTO_SKILL_FLOOR, OFF_JOB_SKILLS, LadderCut, ladder_steps

_JOB = {
    "required_skills": ["AI agents", "Developer tooling", "Software engineering"],
    "nice_to_have_skills": [],
    "keywords": ["DevOps"],
}
_LEDGER = [
    {"concept": "AI agents", "sources": ["required"], "claimable": True, "status": "partial",
     "surface_forms": ["AI agents"],
     "evidence": "Built an agent platform using LangGraph/LangChain for document review.",
     "adjacent_evidence": "Model Context Protocol server for the agent"},
    {"concept": "Developer tooling", "sources": ["required"], "claimable": True, "status": "partial",
     "surface_forms": ["Developer tooling"],
     "evidence": "Hands-on development with Python/FastAPI, PostgreSQL and Docker."},
    # a gap row's text never makes a chip relevant
    {"concept": "Kubernetes", "sources": ["required"], "claimable": False, "status": "gap",
     "surface_forms": ["Kubernetes"], "evidence": "No Kubernetes; some Terraform reading."},
]


def _tier():
    from applire.services.cv import skill_jd_tier_fn

    return skill_jd_tier_fn(_JOB, _LEDGER)


# --- 1. the rank reads the ledger's claimable evidence ------------------------------


def test_a_chip_the_claimable_evidence_names_is_relevant_to_the_job():
    t = _tier()
    for chip in ("Python", "LangChain", "LangGraph", "Docker", "FastAPI", "PostgreSQL"):
        assert t(chip) == 2, chip
    # the parenthetical alone, or the name without it, is the chip's form too
    assert t("Model Context Protocol (MCP)") == 2
    # a direct term match keeps its higher tier
    assert t("AI agents") == 0


def test_evidence_naming_is_whole_word_and_claimable_only():
    t = _tier()
    assert t("Terraform") == 3, "a gap row's text is not evidence"
    assert t("Java") == 3
    assert t("Post") == 3, "no match inside a longer word (PostgreSQL)"
    assert t("Golf") == 3
    from applire.services.cv import skill_jd_tier_fn

    assert skill_jd_tier_fn(_JOB, None)("Python") == 3, "no ledger, no evidence tier"


# --- 2. rung 2: lowest-ranked first, in slices, never below the floor ---------------


def _cv(skills: list[str]) -> dict:
    return {"contact": {"name": "Alex Muster", "email": "alex@example.com"},
            "summary": "S", "work_history": [], "projects": [], "skills": list(skills),
            "certifications": [], "education": []}


def _off_tier(skill: str) -> int:
    return 0 if skill.startswith("job") else 3


def test_the_skills_rung_cuts_lowest_ranked_first_in_slices():
    skills = ["job1", "job2"] + [f"off{i}" for i in range(1, 9)]  # 8 off-job chips
    steps = [s for s in ladder_steps(_cv(skills), _off_tier) if s.cut.kind == OFF_JOB_SKILLS]
    assert [s.cut.items for s in steps] == [
        ("off8", "off7"), ("off6", "off5"), ("off4", "off3"), ("off2", "off1"),
    ]
    assert steps[0].data["skills"] == ["job1", "job2"] + [f"off{i}" for i in range(1, 7)]


def test_the_skills_rung_never_goes_below_the_floor():
    skills = ["job1"] + [f"off{i}" for i in range(1, 12)]  # 12 chips, 11 off-job
    steps = [s for s in ladder_steps(_cv(skills), _off_tier, skill_floor=8)
             if s.cut.kind == OFF_JOB_SKILLS]
    cut = [x for s in steps for x in s.cut.items]
    assert len(cut) == 4 and steps[-1].data["skills"] == ["job1"] + [f"off{i}" for i in range(1, 8)]
    assert not [s for s in ladder_steps(_cv(skills[:8]), _off_tier, skill_floor=8)
                if s.cut.kind == OFF_JOB_SKILLS]


# --- the seam: the AUTOMATIC ladder honours the floor and stops at the limit ---------


async def _auto_ladder(data: dict, pages_of):
    """``_measure_and_condense`` with condense off (CV_PIPELINE=v2) over a
    stub render whose page count follows the record's data."""
    from applire.services.cv import CondenseContext, _measure_and_condense
    from applire.services.cv_budget import BudgetResult

    record = SimpleNamespace(id=uuid.uuid4(), user_id=None, tailored_data=copy.deepcopy(data),
                             section_overrides=None, content_snapshot=None)
    ctx = CondenseContext(
        budgets=BudgetResult(roles={}, tiers={}, target_pages=2, region="DACH"),
        target=2, condense=False, skill_tier=_off_tier,
    )
    with (
        patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html/>")),
        patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")),
        patch("applire.services.ats_audit.extract_text_and_pages",
              side_effect=lambda _pdf: ("text", pages_of(record.tailored_data))),
    ):
        measured = await _measure_and_condense(record, MagicMock(), ctx)
    return record, measured


@pytest.mark.asyncio
async def test_the_automatic_ladder_cuts_only_the_slice_the_page_needs():
    # 4 pages until at most 22 chips remain, 3 pages after (limit = target + 1 = 3)
    skills = ["job1", "job2"] + [f"off{i}" for i in range(1, 25)]  # 26 chips
    record, measured = await _auto_ladder(_cv(skills), lambda d: 4 if len(d["skills"]) > 22 else 3)
    assert measured.page_count == 3
    kept = record.tailored_data["skills"]
    assert len(kept) >= 20, f"one slice suffices, not the section: {len(kept)} kept"
    assert [c.kind for c in measured.ladder_cuts] == [OFF_JOB_SKILLS]
    assert set(measured.ladder_cuts[0].items) == set(skills) - set(kept)


@pytest.mark.asyncio
async def test_the_automatic_ladder_never_empties_the_skills_section():
    # the page never fits: every rung runs, yet the section keeps the floor
    skills = ["job1"] + [f"off{i}" for i in range(1, 25)]
    record, measured = await _auto_ladder(_cv(skills), lambda d: 4)
    assert len(record.tailored_data["skills"]) == AUTO_SKILL_FLOOR
    assert record.tailored_data["skills"][0] == "job1"


# --- 3. the card's payload names the automatic cuts ---------------------------------


@pytest.mark.asyncio
async def test_the_page_card_names_what_the_automatic_ladder_cut(monkeypatch):
    import applire.services.ats_audit as ats_audit
    import applire.services.cv as cv
    from applire.services.cv import CondenseContext, MeasuredRender, page_overflow_public

    data = _cv(["job1"] * 1 + [f"off{i}" for i in range(1, 8)])
    record = SimpleNamespace(id=uuid.uuid4(), user_id=None, tailored_data=copy.deepcopy(data),
                             section_overrides=None, page_overflow=None)
    monkeypatch.setattr(cv, "get_cv_html", AsyncMock(return_value="<html/>"))
    monkeypatch.setattr(cv, "_html_to_pdf", AsyncMock(return_value=b"pdf"))
    monkeypatch.setattr(ats_audit, "extract_text_and_pages", lambda _pdf: ("", 3))
    auto = (LadderCut(OFF_JOB_SKILLS, ("Python", "Docker")), LadderCut(OFF_JOB_SKILLS, ("MATLAB",)))
    measured = MeasuredRender("", 3, True, 2, "DACH", ladder_cuts=auto)
    ctx = CondenseContext(budgets=MagicMock(region="DACH"), target=2, skill_tier=_off_tier)
    await cv._propose_page_cut(record, MagicMock(), ctx, measured)

    assert record.page_overflow["auto_cuts"] == [
        {"kind": OFF_JOB_SKILLS, "items": ["Python", "Docker", "MATLAB"]}
    ]
    record.content_snapshot = None
    public = page_overflow_public(record)
    assert public["auto_cuts"] == [{"kind": OFF_JOB_SKILLS, "items": ["Python", "Docker", "MATLAB"]}]
    # "Kürzer geht es nicht" is true: the proposal ladder found nothing shorter
    assert public["cut_pages"] is None and public["proposed"] == []
    from applire.schemas.cv import CVPageOverflow

    assert CVPageOverflow.model_validate(public).auto_cuts[0].items == ["Python", "Docker", "MATLAB"]


def test_an_older_row_without_auto_cuts_reads_as_none():
    from applire.services.cv import page_overflow_public

    record = SimpleNamespace(page_overflow={"target": 2, "actual": 3, "proposed": [], "choice": None},
                             section_overrides=None, tailored_data={}, content_snapshot=None)
    assert page_overflow_public(record)["auto_cuts"] == []
