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

"""Segmented CV tailoring — outline-then-expand (ADR-047 §1 / E036 US189).

The segmented path generates the CV in pieces (outline + per-work-entry + per-section
calls, each under SEGMENT_MAX_TOKENS) so no single call needs a large output.

E049 / ADR-067: this module now pins the ORCHESTRATION contract only — that
``generate_cv_segmented`` returns the shared PROSE shape (``summary``, id-keyed
``work``, ``skills``, top-level ``projects``), that every call stays under budget,
that there is exactly one call per work entry, and that NO education/languages
call is made (that section LLM call is retired — transcription is copied from the
vault at assembly). The former ``assemble_segmented_cv`` pure-assembly tests below
are DELETED, not rewritten here: assembly is now ``assemble_tailored_cv``, the ONE
join both generation paths share (ADR-066), and its contract (vault-order document
order, fail-closed unknown ids, omitted-entry handling, education/languages carried
wholesale) is already fully pinned in ``tests/unit/test_cv_assembly.py`` — this
module must not duplicate that coverage.

Hermetic: mock/spy providers, no LLM, no DB.
"""


import pytest


class _SegmentSpyProvider:
    """Routes aparse_json by the section's system-prompt role phrase and records every
    max_tokens budget. Absorbs the full ABC signature via **kwargs (AGENTS.md).

    E049/ADR-067: there is no "education writer" branch — the education/languages
    section LLM call is retired (transcription is copied from the vault at
    assembly, never routed through an LLM). If ``generate_cv_segmented`` ever made
    that call again, it would hit the ``raise AssertionError`` below rather than
    silently pass.
    """

    def __init__(self):
        self.budgets: list[int] = []
        self.systems: list[str] = []
        self.work_calls = 0

    async def aparse_json(self, prompt, *, system=None, max_tokens=4096, **kwargs):
        self.budgets.append(max_tokens)
        s = (system or "").lower()
        self.systems.append(s)
        if "outline planner" in s:
            return {"role_order": ["w1", "w0"], "summary_angle": "delivery focus",
                    "skills_focus": ["Python"], "per_role_themes": {}}
        if "work experience writer" in s:
            self.work_calls += 1
            # Factual fields (company/role/dates) come from the profile deterministically
            # (ADR-040); the section writer only returns tailored bullets + nested projects.
            return {"bullets": ["shipped things"], "projects": []}
        if "summary writer" in s:
            return {"summary": "A tailored summary."}
        if "skills writer" in s:
            return {"skills": ["Python", "Leadership"]}
        if "projects writer" in s:
            return {"projects": []}
        raise AssertionError(f"unexpected segmented system prompt: {s!r}")

    async def acomplete(self, prompt, *, max_tokens=4096, **kwargs):
        return "stub"

    async def embed(self, text, **kwargs):
        return None


_PROFILE = {
    "personal_info": {"name": "Marcus Berg", "email": "m@example.com", "location": "Berlin"},
    "work_experience": [
        {"id": "w0", "company": "Acme", "role": "Engineer", "start_date": "2018-01",
         "end_date": "2020-01", "bullets": ["a"]},
        {"id": "w1", "company": "Globex", "role": "Senior Engineer", "start_date": "2020-01",
         "end_date": "2023-01", "bullets": ["b"]},
    ],
    "education": [], "skills": ["Python"], "projects": [],
    # Carried wholesale by assemble_tailored_cv (ADR-067 clause 3) — used below to
    # confirm the real-mock-provider test still sees languages after assembly, even
    # though no LLM call produces them any more.
    "languages": [{"language": "German", "level": "native"}],
}

_JOB = {"role_title": "Lead Engineer", "required_skills": ["Python"], "keywords": ["Python"]}


@pytest.mark.asyncio
async def test_segmented_orchestrator_keeps_every_call_under_the_segment_budget():
    """No single segmented call may request more than SEGMENT_MAX_TOKENS — that is the
    whole point of segmentation (ADR-047 §1)."""
    from applire.constants import SEGMENT_MAX_TOKENS
    from applire.services.cv import generate_cv_segmented

    spy = _SegmentSpyProvider()
    await generate_cv_segmented(_JOB, _PROFILE, [], output_language="en", provider=spy)

    assert spy.budgets, "expected segmented calls"
    assert all(b <= SEGMENT_MAX_TOKENS for b in spy.budgets)


@pytest.mark.asyncio
async def test_segmented_orchestrator_calls_one_work_section_per_entry():
    """Per-work-entry segmentation: one section call for each work-experience entry,
    and — E049/ADR-067 — exactly outline + N work + summary + skills + projects calls,
    with NO education/languages call (that section LLM call is retired)."""
    from applire.services.cv import generate_cv_segmented

    spy = _SegmentSpyProvider()
    await generate_cv_segmented(_JOB, _PROFILE, [], output_language="en", provider=spy)

    assert spy.work_calls == len(_PROFILE["work_experience"]) == 2
    # outline(1) + work(2) + summary(1) + skills(1) + projects(1) = 6, no education call.
    assert len(spy.systems) == 6


@pytest.mark.asyncio
async def test_segmented_orchestrator_threads_ledger_into_summary_call():
    """#235 — the summary section call must receive the SAME keyword_ledger passed to
    generate_cv_segmented, not build a ledger-blind prompt while the other sections
    (outline/work/skills) do thread it."""
    from applire.services.cv import generate_cv_segmented

    ledger = [
        {"concept": "AI", "surface_forms": ["AI", "Artificial Intelligence"], "claimable": True,
         "status": "direct", "sources": ["required"], "fit_weight": 1.0,
         "evidence": "Led the AI platform team for 3 years"},
    ]

    captured_prompts: list[tuple[str, str]] = []

    class _CapturingSpy(_SegmentSpyProvider):
        async def aparse_json(self, prompt, *, system=None, **kwargs):
            captured_prompts.append((system or "", prompt))
            return await super().aparse_json(prompt, system=system, **kwargs)

    spy = _CapturingSpy()
    await generate_cv_segmented(
        _JOB, _PROFILE, [], output_language="en", provider=spy, keyword_ledger=ledger,
    )

    summary_prompts = [p for s, p in captured_prompts if "summary writer" in s.lower()]
    assert summary_prompts, "expected exactly one summary-section call"
    assert "AI" in summary_prompts[0]
    assert "Led the AI platform team for 3 years" in summary_prompts[0]


@pytest.mark.asyncio
async def test_segmented_orchestrator_returns_the_shared_prose_shape():
    """E049/ADR-067: generate_cv_segmented returns the same PROSE shape the
    single-call writer does — summary / id-keyed work (bullets + nested projects)
    / skills / top-level projects — never a full TailoredCVData. Assembling it onto
    the profile (assemble_tailored_cv, ADR-066's shared join) must then validate,
    with contact sourced deterministically from the profile (not LLM-generated —
    ADR-040)."""
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import assemble_tailored_cv, generate_cv_segmented

    spy = _SegmentSpyProvider()
    prose = await generate_cv_segmented(_JOB, _PROFILE, [], output_language="en", provider=spy)

    assert prose["summary"] == "A tailored summary."
    assert {w["id"] for w in prose["work"]} == {"w0", "w1"}
    assert prose["skills"] == ["Python", "Leadership"]

    cv = TailoredCVData.model_validate(assemble_tailored_cv(prose, _PROFILE))
    assert cv.contact.name == "Marcus Berg"
    assert len(cv.work_history) == 2
    assert cv.summary == "A tailored summary."
    assert cv.skills == ["Python", "Leadership"]


@pytest.mark.asyncio
async def test_segmented_orchestrator_keeps_reverse_chronological_work_order():
    """Document order stays deterministic reverse-chronological (single-call rule 2
    parity), NOT whatever the outline's role_order suggests. E049/ADR-067: order is
    no longer this function's concern at all — ``generate_cv_segmented`` iterates
    ``work_src`` (already sorted by the caller) to build ``prose["work"]``, and
    ``assemble_tailored_cv`` is what makes the vault's sorted order the document
    order (this is what retired ``_enforce_work_order``, pinned in
    ``test_cv_assembly.py``). Here we only confirm the prose ids come back in that
    same sorted order, regardless of the stub outline's coincidental role_order."""
    from applire.services.cv import generate_cv_segmented

    spy = _SegmentSpyProvider()
    prose = await generate_cv_segmented(_JOB, _PROFILE, [], output_language="en", provider=spy)

    # Globex (2020–2023) is more recent than Acme (2018–2020) → w1 (Globex) first,
    # regardless of the stub outline returning role_order ["w1","w0"] coincidentally.
    assert [w["id"] for w in prose["work"]] == ["w1", "w0"]


@pytest.mark.asyncio
async def test_segmented_orchestrator_runs_against_the_real_mock_provider():
    """The segmented chains in MockLLMProvider must all return schema-valid slices —
    drives the orchestrator through the actual mock the CI/integration tests use,
    then through the real assembly join (ADR-066)."""
    from applire.providers.llm.mock import MockLLMProvider
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import assemble_tailored_cv, generate_cv_segmented

    prose = await generate_cv_segmented(
        _JOB, _PROFILE, [], output_language="de", provider=MockLLMProvider()
    )
    cv = TailoredCVData.model_validate(assemble_tailored_cv(prose, _PROFILE))

    assert cv.summary
    assert len(cv.work_history) == 2
    assert cv.skills
    # Carried wholesale from the profile at assembly (ADR-067 clause 3) — no LLM
    # education/languages call exists any more to have produced this.
    assert cv.languages


# ---------------------------------------------------------------------------
# Fallback wiring: single-call fast path → segmented on truncation/timeout/small cap
# (ADR-047 §1/§2 — this is the US188 "switch to segmented instead of doubling" path)
# ---------------------------------------------------------------------------


class _SingleCallFails:
    """Raises on the single-call CV tailoring system prompt, delegates the segmented
    section chains to the real MockLLMProvider."""

    def __init__(self, exc):
        from applire.providers.llm.mock import MockLLMProvider
        self._exc = exc
        self._mock = MockLLMProvider()
        self.single_calls = 0

    async def aparse_json(self, prompt, *, system=None, max_tokens=4096, **kwargs):
        if "career consultant" in (system or "").lower():
            self.single_calls += 1
            raise self._exc
        return await self._mock.aparse_json(prompt, system=system, max_tokens=max_tokens, **kwargs)

    async def acomplete(self, *a, **k):
        return "x"

    async def embed(self, *a, **k):
        return None


@pytest.mark.asyncio
async def test_truncated_single_call_falls_back_to_segmented():
    """A single-call truncation switches to segmented mode (not budget-doubling) and still
    yields a complete, valid CV — the PQ-run-4 'no CV' fix (ADR-047). E049/ADR-067:
    ``_tailor_cv_with_fallback`` returns the PROSE shape either way, so this test
    assembles it onto the profile before validating as TailoredCVData."""
    from applire.exceptions import LLMTruncatedError
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import _tailor_cv_with_fallback, assemble_tailored_cv

    provider = _SingleCallFails(LLMTruncatedError("hit the cap"))
    prose = await _tailor_cv_with_fallback(
        _JOB, _PROFILE, [], output_language="de", provider=provider
    )
    cv = TailoredCVData.model_validate(assemble_tailored_cv(prose, _PROFILE))

    assert provider.single_calls == 1  # tried the fast path once
    assert len(cv.work_history) == 2  # then completed via segmentation


@pytest.mark.asyncio
async def test_timed_out_single_call_falls_back_to_segmented():
    from applire.exceptions import LLMTimeoutError
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import _tailor_cv_with_fallback, assemble_tailored_cv

    provider = _SingleCallFails(LLMTimeoutError("too slow"))
    prose = await _tailor_cv_with_fallback(
        _JOB, _PROFILE, [], output_language="de", provider=provider
    )
    assert TailoredCVData.model_validate(assemble_tailored_cv(prose, _PROFILE))


@pytest.mark.asyncio
async def test_successful_single_call_skips_segmentation():
    """The happy path on a capable model stays a single call — segmentation is the
    fallback, not the default (ADR-047: happy path stays fast)."""
    from applire.providers.llm.mock import MockLLMProvider
    from applire.services.cv import _tailor_cv_with_fallback

    class _CountingMock(MockLLMProvider):
        def __init__(self):
            super().__init__()
            self.systems: list[str] = []

        async def aparse_json(self, prompt, *, system=None, **kwargs):
            self.systems.append((system or "").lower())
            return await super().aparse_json(prompt, system=system, **kwargs)

    provider = _CountingMock()
    await _tailor_cv_with_fallback(_JOB, _PROFILE, [], output_language="de", provider=provider)

    # the single-call consultant chain ran; no segmented section chain was touched
    assert any("career consultant" in s for s in provider.systems)
    assert not any("outline planner" in s for s in provider.systems)


@pytest.mark.asyncio
async def test_small_declared_cap_segments_upfront(monkeypatch):
    """When the operator declares a cap below the single-call ceiling, skip the doomed
    first call and segment straight away (ADR-047 §5 spirit, reactive-floor variant)."""
    import applire.config as cfg
    from applire.providers.llm.mock import MockLLMProvider
    from applire.services.cv import _tailor_cv_with_fallback

    monkeypatch.setattr(cfg.settings, "llm_max_output_tokens", 4096)

    class _CountingMock(MockLLMProvider):
        def __init__(self):
            super().__init__()
            self.systems: list[str] = []

        async def aparse_json(self, prompt, *, system=None, **kwargs):
            self.systems.append((system or "").lower())
            return await super().aparse_json(prompt, system=system, **kwargs)

    provider = _CountingMock()
    await _tailor_cv_with_fallback(_JOB, _PROFILE, [], output_language="de", provider=provider)

    assert any("outline planner" in s for s in provider.systems)  # went straight to segmented
    assert not any("career consultant" in s for s in provider.systems)  # skipped the doomed call
