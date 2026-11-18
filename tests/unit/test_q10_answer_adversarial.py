# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q10 — the adversarial pass on the posting-terms answer (2026-11-18).

* F1 — answers by one person are serialised (the profile row FOR UPDATE, one
  commit for skills + record) and the answer reports what the STORED profile
  holds;
* F8 — the person's own skills door (``PATCH /api/profile/skills``) keeps the
  stored confirmations: never erased by a body that omits them, never accepted
  from the client; deleting the skill still removes its confirmation;
* F5 — an answer that changes nothing leaves the receipt as it was;
* F6 — ``leave_open`` with ``confirmed`` is refused; an agent retry of a
  confirmed term is an idempotent success; a new skill takes the category of
  the skill the card showed beside it.
No Docker, no LLM. The Postgres lock itself is exercised live (report).
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("_q10_answer_adv", _HERE / "test_q10_posting_term_answer.py")
_answer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_answer)
db = _answer.db  # the fixture, re-exported for this module

HARNESS = _answer.HARNESS_USER_ID


async def _application(db, job):
    from applire.services.job import caller_link

    app = await caller_link(db, job.id, HARNESS)
    await db.refresh(app)
    return app


# ── F1: serialised, and the answer says what was stored ──────────────────────

@pytest.mark.asyncio
async def test_SEAM_the_answer_locks_the_persons_profile_row_before_it_reads_anything(db):
    from applire.services.posting_term_answer import answer_posting_terms

    job = await _answer._seed(db)
    seen: list = []
    real_execute = db.execute

    async def spy(stmt, *a, **kw):
        seen.append(stmt)
        return await real_execute(stmt, *a, **kw)

    real_refresh = db.refresh
    refreshed: list = []

    async def spy_refresh(obj, *a, **kw):
        refreshed.append((type(obj).__name__, kw.get("with_for_update")))
        return await real_refresh(obj, *a, **kw)

    with patch.object(db, "execute", spy), patch.object(db, "refresh", spy_refresh):
        await answer_posting_terms(db, job.id, HARNESS, confirmed=["Observability"], via="person")
    first = seen[0]
    assert getattr(first, "_for_update_arg", None) is not None
    assert first.column_descriptions[0]["name"] == "MasterProfile"
    assert ("Application", True) in refreshed


@pytest.mark.asyncio
async def test_SEAM_skills_and_the_record_commit_once_so_the_lock_is_held_across_both(db):
    from applire.services.posting_term_answer import answer_posting_terms

    job = await _answer._seed(db)
    real_commit = db.commit
    commits: list[int] = []

    async def count():
        commits.append(1)
        return await real_commit()

    with patch.object(db, "commit", count):
        state = await answer_posting_terms(db, job.id, HARNESS, confirmed=["Observability"], via="person")
    assert len(commits) == 1
    assert state.confirmed == ["Observability"]
    assert (await _application(db, job)).posting_terms["confirmed"] == ["Observability"]


@pytest.mark.asyncio
async def test_the_answer_reports_what_the_stored_profile_holds_not_what_was_asked(db):
    from applire.services import posting_term_answer as pta

    job = await _answer._seed(db)

    async def lost_write(section, value, db_, **kw):  # the skills write lands nothing
        await db_.flush()

    with patch("applire.services.profile.patch_profile_section", lost_write):
        state = await pta.answer_posting_terms(db, job.id, HARNESS, confirmed=["Observability"], via="person")
    assert state.confirmed == []
    assert "Observability" in {t["term"] for t in state.terms}          # still asked, not declined
    assert "Observability" not in state.declined
    # …and the record itself never names it (not only the read's filter)
    assert "Observability" not in ((await _application(db, job)).posting_terms or {}).get("confirmed", [])


@pytest.mark.asyncio
async def test_a_second_answer_after_the_first_keeps_both_skills(db):
    """The race's sequential form: the second answer reads the first's write."""
    from applire.services.posting_term_answer import answer_posting_terms

    job = await _answer._seed(db)
    await answer_posting_terms(db, job.id, HARNESS, confirmed=["Observability"], via="agent")
    state = await answer_posting_terms(db, job.id, HARNESS, confirmed=["Event-driven architecture"], via="agent")
    profile = await _answer._profile(db)
    assert _answer._skill(profile, "Observability")["confirmations"]
    assert _answer._skill(profile, "Event-driven architecture")["confirmations"]
    assert state.confirmed == ["Observability", "Event-driven architecture"]


# ── F8: the person's skills door ─────────────────────────────────────────────

async def _patch_skills_door(db, body):
    from applire.routers.profile import patch_section

    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    return await patch_section(
        "skills", request, db=db, current_user=MagicMock(id=HARNESS), provider=None,
        basis_updated_at=None,
    )


@pytest.mark.asyncio
async def test_SEAM_the_skills_door_neither_erases_nor_accepts_a_confirmation(db):
    await _answer._confirmed_by_person(db)
    stored = copy.deepcopy(_answer._skill(await _answer._profile(db), "Observability")["confirmations"])
    skills = [{k: v for k, v in s.items() if k != "confirmations"}
              for s in (await _answer._profile(db))["skills"]]
    forged = {"name": "Grafana", "category": "technical", "status": "confirmed",
              "confirmations": [{"name": "Grafana", "via": "person", "on": "2000-01-01",
                                 "posting": "Made Up GmbH"}]}
    altered = [dict(s) for s in skills]
    await _patch_skills_door(db, altered + [forged])
    profile = await _answer._profile(db)
    assert _answer._skill(profile, "Observability")["confirmations"] == stored     # not erased
    assert not _answer._skill(profile, "Grafana").get("confirmations")              # not accepted


@pytest.mark.asyncio
async def test_SEAM_the_skills_door_keeps_the_stored_record_against_an_altered_one(db):
    await _answer._confirmed_by_person(db)
    profile = await _answer._profile(db)
    stored = copy.deepcopy(_answer._skill(profile, "Observability")["confirmations"])
    body = copy.deepcopy(profile["skills"])
    for s in body:
        for c in s.get("confirmations") or []:
            c.update(via="agent", on="2000-01-01", posting="Other GmbH")
    await _patch_skills_door(db, body)
    assert _answer._skill(await _answer._profile(db), "Observability")["confirmations"] == stored


@pytest.mark.asyncio
async def test_SEAM_deleting_the_skill_on_the_skills_door_removes_its_confirmation(db):
    from applire.services.posting_term_answer import posting_terms_state

    job = await _answer._confirmed_by_person(db)
    profile = await _answer._profile(db)
    await _patch_skills_door(db, [s for s in profile["skills"] if s["name"] != "Observability"])
    profile = await _answer._profile(db)
    assert _answer._skill(profile, "Observability") is None
    assert not any(s.get("confirmations") for s in profile["skills"])
    # the term is the posting's word again: asked again, and off the receipt
    state = await posting_terms_state(db, job.id, HARNESS)
    assert "Observability" in {t["term"] for t in state.terms}
    assert state.confirmed == []


# ── F5: an answer that changes nothing keeps the receipt ─────────────────────

@pytest.mark.asyncio
async def test_a_second_empty_answer_keeps_the_receipt(db):
    from applire.services.posting_term_answer import answer_posting_terms

    job = await _answer._seed(db)
    first = await answer_posting_terms(db, job.id, HARNESS, confirmed=["Observability"], via="person")
    again = await answer_posting_terms(db, job.id, HARNESS, confirmed=[], via="person")
    assert again.confirmed == ["Observability"]
    assert again.answered_at == first.answered_at


@pytest.mark.asyncio
async def test_an_agent_confirm_then_the_card_keeps_both_on_the_receipt(db):
    from applire.services.posting_term_answer import answer_posting_terms

    job = await _answer._seed(db)
    await answer_posting_terms(db, job.id, HARNESS, confirmed=["Observability"], via="agent")
    state = await answer_posting_terms(db, job.id, HARNESS, confirmed=["Event-driven architecture"], via="person")
    assert state.confirmed == ["Observability", "Event-driven architecture"]


# ── F6: the agent door's record details ──────────────────────────────────────

@pytest.mark.asyncio
async def test_SEAM_leave_open_with_confirmed_terms_is_refused_and_changes_nothing(db):
    from mcp.server.fastmcp.exceptions import ToolError

    job = await _answer._seed(db)
    before = copy.deepcopy(await _answer._profile(db))
    with pytest.raises(ToolError, match="cannot be combined"):
        await _answer._call("confirm_posting_terms",
                            {"job_id": str(job.id), "confirmed": ["Observability"], "leave_open": True}, db)
    assert (await _answer._profile(db))["skills"] == before["skills"]
    assert not (await _application(db, job)).posting_terms


@pytest.mark.asyncio
async def test_SEAM_an_agent_retry_of_a_confirmed_term_is_an_idempotent_success(db):
    job = await _answer._seed(db)
    args = {"job_id": str(job.id), "confirmed": ["Observability"]}
    await _answer._call("confirm_posting_terms", args, db)
    out = await _answer._call("confirm_posting_terms", args, db)
    assert out["confirmed"] == ["Observability"]
    profile = await _answer._profile(db)
    assert [s["name"] for s in profile["skills"]].count("Observability") == 1
    assert len(_answer._skill(profile, "Observability")["confirmations"]) == 1


@pytest.mark.asyncio
async def test_a_confirm_after_leave_open_takes_the_term_out_of_left_open(db):
    job = await _answer._seed(db)
    await _answer._call("confirm_posting_terms", {"job_id": str(job.id), "leave_open": True}, db)
    out = await _answer._call("confirm_posting_terms", {"job_id": str(job.id), "confirmed": ["Observability"]}, db)
    assert "Observability" not in out["left_open"] and out["confirmed"] == ["Observability"]


@pytest.mark.asyncio
async def test_a_new_skill_takes_the_category_of_the_skill_the_card_showed_beside_it(db):
    from applire.services.posting_term_answer import answer_posting_terms

    vault = copy.deepcopy(_answer.VAULT)
    vault["skills"][0]["category"] = "domain"                 # Prometheus, shown for Observability
    job = await _answer._seed(db, vault=vault)
    await answer_posting_terms(db, job.id, HARNESS,
                               confirmed=["Observability", "Event-driven architecture"], via="person")
    profile = await _answer._profile(db)
    assert _answer._skill(profile, "Observability")["category"] == "domain"
    # nothing shown beside it → the schema's default, not a stamp of our own
    assert _answer._skill(profile, "Event-driven architecture")["category"] == "technical"


# ── delivery tier F-D1: the agent's answer is a full answer, "no" included ───

@pytest.mark.asyncio
async def test_SEAM_an_agent_answer_records_when_via_which_door_and_what_was_confirmed(db):
    job = await _answer._seed(db)
    out = await _answer._call("confirm_posting_terms", {"job_id": str(job.id), "confirmed": ["Observability"]}, db)
    record = (await _application(db, job)).posting_terms
    assert record["via"] == "agent" and record["answered_at"]
    assert record["confirmed"] == ["Observability"] and out["answered_at"] == record["answered_at"]


@pytest.mark.asyncio
async def test_SEAM_the_agent_passes_on_the_persons_no_and_the_card_never_asks_it_again(db):
    from applire.services.posting_term_answer import posting_terms_state

    job = await _answer._seed(db)
    out = await _answer._call("confirm_posting_terms", {
        "job_id": str(job.id), "confirmed": ["Observability"], "declined": ["Event-driven architecture"]}, db)
    assert out["declined"] == ["Event-driven architecture"] and out["may_generate"] is True
    assert _answer._skill(await _answer._profile(db), "Event-driven architecture") is None
    record = (await _application(db, job)).posting_terms
    assert record["declined"] == ["Event-driven architecture"] and record["via"] == "agent"
    web = await posting_terms_state(db, job.id, HARNESS)              # the card's own read
    assert web.terms == []


@pytest.mark.asyncio
async def test_a_term_the_agent_names_neither_way_stays_open(db):
    job = await _answer._seed(db)
    out = await _answer._call("confirm_posting_terms",
                              {"job_id": str(job.id), "declined": ["Event-driven architecture"]}, db)
    assert [t["term"] for t in out["terms"]] == ["Observability"] and out["may_generate"] is False
    assert out["confirmed"] == [] and out["left_open"] == []


@pytest.mark.asyncio
async def test_a_decline_after_leave_open_takes_the_term_out_of_left_open(db):
    job = await _answer._seed(db)
    await _answer._call("confirm_posting_terms", {"job_id": str(job.id), "leave_open": True}, db)
    out = await _answer._call("confirm_posting_terms",
                              {"job_id": str(job.id), "declined": ["Observability"]}, db)
    assert out["left_open"] == ["Event-driven architecture"] and out["declined"] == ["Observability"]


@pytest.mark.asyncio
async def test_SEAM_repeating_a_decline_is_an_idempotent_success(db):
    job = await _answer._seed(db)
    args = {"job_id": str(job.id), "declined": ["Observability"]}
    first = await _answer._call("confirm_posting_terms", args, db)
    again = await _answer._call("confirm_posting_terms", args, db)
    assert again["declined"] == ["Observability"] and again["answered_at"] == first["answered_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("args, message", [
    ({"confirmed": ["Observability"], "declined": ["observability"]}, "both confirmed and declined"),
    ({"declined": ["Observability"], "leave_open": True}, "cannot be combined"),
    ({"declined": ["Kubernetes"]}, "not on the list: Kubernetes"),
])
async def test_SEAM_a_contradictory_or_off_list_decline_is_refused_and_changes_nothing(db, args, message):
    from mcp.server.fastmcp.exceptions import ToolError

    job = await _answer._seed(db)
    before = copy.deepcopy(await _answer._profile(db))
    with pytest.raises(ToolError, match=message):
        await _answer._call("confirm_posting_terms", {"job_id": str(job.id), **args}, db)
    assert (await _answer._profile(db))["skills"] == before["skills"]
    assert not (await _application(db, job)).posting_terms


@pytest.mark.asyncio
async def test_SEAM_a_confirmed_term_cannot_be_declined_afterwards(db):
    from mcp.server.fastmcp.exceptions import ToolError

    job = await _answer._seed(db)
    await _answer._call("confirm_posting_terms", {"job_id": str(job.id), "confirmed": ["Observability"]}, db)
    with pytest.raises(ToolError, match="already confirmed for this posting: Observability"):
        await _answer._call("confirm_posting_terms", {"job_id": str(job.id), "declined": ["Observability"]}, db)
    assert _answer._skill(await _answer._profile(db), "Observability")["confirmations"]
    assert "Observability" not in (await _application(db, job)).posting_terms.get("declined", [])
