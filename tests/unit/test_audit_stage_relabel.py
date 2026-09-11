# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""The audit tails relabel the LLM-log stage (#538/#539 refuter observation).

`review_and_refine` sets the per-task stage contextvar to its chain id and
never resets it, so the audit tail's own LLM calls (Oracle sentence triage,
outcome critic) used to inherit the LAST chain's label — on the 2026-08-16
evidence runs they logged as ``cv_terminal_review`` / ``letter_terminal_review``,
poisoning every log-based per-chain count. These tests prove both audit
entrypoints stamp their own stage label before any provider call can happen:
the audit bodies are driven with broken inputs (every block fails and is
caught — the audits' own fail-open contract), so what remains observable is
exactly the stage relabel."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


@pytest.mark.asyncio
async def test_letter_audit_relabels_the_stage():
    from applire.providers.llm.debug_log import current_call_site, set_stage
    from applire.services.cover_letter import _update_ats_report_letter

    set_stage("letter_terminal_review")  # what the last chain left behind
    cl = MagicMock()
    cl.letter_data = {}
    cl.section_overrides = {}
    db = AsyncMock()
    db.get.return_value = None
    await _update_ats_report_letter(cl, db, pdf=b"not-a-pdf")

    assert current_call_site()[0] == "letter_audit", (
        "the audit tail must not inherit the terminal chain's stage label"
    )


@pytest.mark.asyncio
async def test_cv_audit_relabels_the_stage():
    from applire.providers.llm.debug_log import current_call_site, set_stage
    from applire.services.cv import _update_ats_report

    set_stage("cv_terminal_review")
    record = MagicMock()
    record.tailored_data = {}
    record.section_overrides = {}
    db = AsyncMock()
    db.get.return_value = None
    await _update_ats_report(record, db, measured=None, commit=False)

    assert current_call_site()[0] == "cv_audit", (
        "the audit tail must not inherit the terminal chain's stage label"
    )


@pytest.mark.asyncio
async def test_cv_audit_outcome_critic_call_runs_under_its_own_stage_label(monkeypatch):
    """Nougat build-2 contract 2 (the P2 helper leaks, this is a call-site fix):
    inside ``_update_ats_report``'s critic block, the critic's OWN LLM call
    (``run_pass_a``) must log as ``outcome_critic``, not the surrounding audit
    tail's ``cv_audit`` — and the stage must be back at ``cv_audit`` immediately
    after the call so a later call in the same function (e.g. the .docx audit
    block that follows) is not mislabelled either."""
    from applire.providers.llm.debug_log import current_call_site, set_stage
    from applire.schemas.outcome_critic import OutcomeCriticReport
    from applire.services import cv as cv_module

    set_stage("cv_terminal_review")  # what the last chain left behind

    seen: dict = {}

    async def fake_run_pass_a(**kwargs):
        seen["stage_during_call"] = current_call_site()[0]
        return OutcomeCriticReport(ran=True, reason=None, advisories=[])

    monkeypatch.setattr("applire.services.outcome_critic.run_pass_a", fake_run_pass_a)
    monkeypatch.setattr(cv_module, "get_provider", lambda: MagicMock())

    record = MagicMock()
    record.tailored_data = {"contact": {}}
    record.content_snapshot = {}
    record.section_overrides = {}
    db = AsyncMock()
    db.get.return_value = None

    await cv_module._update_ats_report(record, db, measured=None, commit=False)

    assert seen.get("stage_during_call") == "outcome_critic", (
        "the critic's own LLM call must log under `outcome_critic`, not the "
        "audit tail's `cv_audit`"
    )
    assert current_call_site()[0] == "cv_audit", (
        "the stage must be restored to `cv_audit` right after the critic call "
        "so a later call in this function is not mislabelled"
    )


@pytest.mark.asyncio
async def test_cv_tailoring_draft_call_is_labelled_cv_tailoring(monkeypatch):
    """Nougat build-2 contract 2: ``_tailor_cv_with_fallback``'s single-call
    fast-path draft — the pre-loop call that produces the INITIAL tailored CV
    before ``review_and_refine`` (``chain_id="cv_tailoring"``) is entered and
    starts labelling its own calls — must not inherit whatever stage a PRIOR
    chain in this asyncio task left behind. Confirmed by reading the code path
    (build-2 contract 2 handoff): no `set_stage`/`set_llm_log_stage` call
    exists anywhere between the start of `_render_cv_background` and this
    call, so before this fix the draft logged under the last chain's leftover
    label (or `""` on a clean task)."""
    from unittest.mock import AsyncMock as _AsyncMock

    from applire.providers.llm.debug_log import current_call_site, set_stage
    from applire.services import cv as cv_module

    set_stage("some_prior_chain")  # what a prior chain in this task left behind
    monkeypatch.setattr(
        cv_module, "_should_segment_upfront", _AsyncMock(return_value=False)
    )

    seen: dict = {}

    async def fake_aparse_json(*args, **kwargs):
        seen["stage_during_call"] = current_call_site()[0]
        return {"contact": {}}

    provider = MagicMock()
    provider.aparse_json = fake_aparse_json

    result = await cv_module._tailor_cv_with_fallback(
        job_analysis={},
        profile={},
        keyword_gaps=[],
        output_language="de",
        provider=provider,
    )

    assert result == {"contact": {}}
    assert seen.get("stage_during_call") == "cv_tailoring", (
        "the writer's initial draft call must be labelled `cv_tailoring`, not "
        "inherit a prior chain's stage"
    )


@pytest.mark.asyncio
async def test_letter_audit_outcome_critic_call_runs_under_its_own_stage_label(monkeypatch):
    """Nougat build-2 contract 2, the LETTER twin of the CV seam above.

    ``_update_ats_report_letter`` sets ``letter_audit`` at its head and
    ``set_stage`` is imperative, so Pass B's own calls were logged under the
    audit's label. Measured on the captured 2026-09-05 delivery run: every
    ``outcome_critic`` record in the chain carried ``letter_audit``/``cv_audit``
    instead — the 165/165 the build-2 inventory counted."""
    from applire.providers.llm.debug_log import current_call_site, set_stage
    from applire.schemas.outcome_critic import OutcomeCriticReport
    from applire.services import cover_letter as cl_module

    set_stage("letter_terminal_review")  # what the last chain left behind

    seen: dict = {}

    async def fake_run_pass_b(**kwargs):
        seen["stage_during_call"] = current_call_site()[0]
        return OutcomeCriticReport(ran=True, reason=None, advisories=[])

    async def _no_ledger(*a, **kw):
        return []

    monkeypatch.setattr("applire.services.outcome_critic.run_pass_b", fake_run_pass_b)
    monkeypatch.setattr(cl_module, "get_provider", lambda: MagicMock())
    monkeypatch.setattr(cl_module, "_latest_keyword_ledger", _no_ledger)

    record = MagicMock()
    record.letter_data = {"body": {"paragraphs": ["Sehr geehrte Damen und Herren,", "Text."]}}
    record.section_overrides = {}
    record.ats_report = None
    db = AsyncMock()
    db.get.return_value = None
    _empty = MagicMock()
    _empty.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=_empty)

    await cl_module._update_ats_report_letter(record, db, pdf=None)

    assert seen.get("stage_during_call") == "outcome_critic", (
        "Pass B's own LLM call must log under `outcome_critic`, not the letter "
        "audit tail's `letter_audit`"
    )
    assert current_call_site()[0] == "letter_audit", (
        "the stage must be restored to `letter_audit` right after Pass B"
    )
