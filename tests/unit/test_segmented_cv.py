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
calls, each under SEGMENT_MAX_TOKENS) so no single call needs a large output. The
pieces are then assembled **deterministically in code** — this module pins that pure
assembly step: given an outline directive and the section pieces, produce a valid
TailoredCVData with work history ordered per the outline, and nothing fabricated.

Hermetic: pure function, no LLM, no DB.
"""


import pytest


def _work(id_, role):
    return {"id": id_, "company": f"{role} Co", "role": role, "start_date": "2020-01",
            "end_date": "2022-01", "bullets": [f"did {role} things"], "projects": []}


class _SegmentSpyProvider:
    """Routes aparse_json by the section's system-prompt role phrase and records every
    max_tokens budget. Absorbs the full ABC signature via **kwargs (AGENTS.md)."""

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
        if "education writer" in s:
            return {"education": [{"institution": "TU", "degree": "BSc", "field": "CS",
                                   "start_date": "2014", "end_date": "2017"}],
                    "languages": [{"language": "German", "level": "native"}]}
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
}

_JOB = {"role_title": "Lead Engineer", "required_skills": ["Python"], "keywords": ["Python"]}


@pytest.mark.asyncio
async def test_segmented_orchestrator_keeps_every_call_under_the_segment_budget():
    """No single segmented call may request more than SEGMENT_MAX_TOKENS — that is the
    whole point of segmentation (ADR-047 §1)."""
    from applire.constants import SEGMENT_MAX_TOKENS
    from applire.services.cv import generate_cv_segmented

    spy = _SegmentSpyProvider()
    await generate_cv_segmented(_JOB, _PROFILE, [], [], output_language="en", provider=spy)

    assert spy.budgets, "expected segmented calls"
    assert all(b <= SEGMENT_MAX_TOKENS for b in spy.budgets)


@pytest.mark.asyncio
async def test_segmented_orchestrator_calls_one_work_section_per_entry():
    """Per-work-entry segmentation: one section call for each work-experience entry."""
    from applire.services.cv import generate_cv_segmented

    spy = _SegmentSpyProvider()
    await generate_cv_segmented(_JOB, _PROFILE, [], [], output_language="en", provider=spy)

    assert spy.work_calls == len(_PROFILE["work_experience"]) == 2


@pytest.mark.asyncio
async def test_segmented_orchestrator_assembles_valid_cv_with_profile_contact():
    """The orchestrated result validates as TailoredCVData, with contact sourced
    deterministically from the profile (not LLM-generated — ADR-040)."""
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import generate_cv_segmented

    spy = _SegmentSpyProvider()
    result = await generate_cv_segmented(_JOB, _PROFILE, [], [], output_language="en", provider=spy)
    cv = TailoredCVData.model_validate(result)

    assert cv.contact.name == "Marcus Berg"
    assert len(cv.work_history) == 2
    assert cv.summary == "A tailored summary."
    assert cv.skills == ["Python", "Leadership"]


@pytest.mark.asyncio
async def test_segmented_orchestrator_keeps_reverse_chronological_work_order():
    """Work order stays deterministic reverse-chronological (single-call rule 2 parity),
    NOT whatever the outline's role_order suggests — the orchestrator owns the policy."""
    from applire.services.cv import generate_cv_segmented

    spy = _SegmentSpyProvider()
    result = await generate_cv_segmented(_JOB, _PROFILE, [], [], output_language="en", provider=spy)

    # Globex (2020–2023) is more recent than Acme (2018–2020) → must come first,
    # regardless of the stub outline returning role_order ["w1","w0"] coincidentally.
    assert [w["company"] for w in result["work_history"]] == ["Globex", "Acme"]


@pytest.mark.asyncio
async def test_segmented_orchestrator_runs_against_the_real_mock_provider():
    """The new segmented chains in MockLLMProvider must all return schema-valid slices —
    drives the orchestrator through the actual mock the CI/integration tests use."""
    from applire.providers.llm.mock import MockLLMProvider
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import generate_cv_segmented

    result = await generate_cv_segmented(
        _JOB, _PROFILE, [], [], output_language="de", provider=MockLLMProvider()
    )
    cv = TailoredCVData.model_validate(result)

    assert cv.summary
    assert len(cv.work_history) == 2
    assert cv.skills
    assert cv.languages  # education writer also returns spoken languages


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
    yields a complete, valid CV — the PQ-run-4 'no CV' fix (ADR-047)."""
    from applire.exceptions import LLMTruncatedError
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import _tailor_cv_with_fallback

    provider = _SingleCallFails(LLMTruncatedError("hit the cap"))
    raw = await _tailor_cv_with_fallback(
        _JOB, _PROFILE, [], [], output_language="de", provider=provider
    )
    cv = TailoredCVData.model_validate(raw)

    assert provider.single_calls == 1  # tried the fast path once
    assert len(cv.work_history) == 2  # then completed via segmentation


@pytest.mark.asyncio
async def test_timed_out_single_call_falls_back_to_segmented():
    from applire.exceptions import LLMTimeoutError
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import _tailor_cv_with_fallback

    provider = _SingleCallFails(LLMTimeoutError("too slow"))
    raw = await _tailor_cv_with_fallback(
        _JOB, _PROFILE, [], [], output_language="de", provider=provider
    )
    assert TailoredCVData.model_validate(raw)


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
    await _tailor_cv_with_fallback(_JOB, _PROFILE, [], [], output_language="de", provider=provider)

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
    await _tailor_cv_with_fallback(_JOB, _PROFILE, [], [], output_language="de", provider=provider)

    assert any("outline planner" in s for s in provider.systems)  # went straight to segmented
    assert not any("career consultant" in s for s in provider.systems)  # skipped the doomed call



def test_assembly_orders_work_history_by_the_outline_role_order():
    """The outline decides role ordering; assembly must honour it deterministically."""
    from applire.services.cv import assemble_segmented_cv

    outline = {"role_order": ["w2", "w1"], "summary_angle": "", "skills_focus": []}
    sections = {
        "summary": "A tailored summary.",
        "work_entries": [_work("w1", "Engineer"), _work("w2", "Lead")],
        "skills": ["Python", "Leadership"],
        "education": [],
        "languages": [],
        "projects": [],
    }

    assembled = assemble_segmented_cv(outline, sections)

    assert [w["role"] for w in assembled["work_history"]] == ["Lead", "Engineer"]


def test_assembly_produces_a_valid_tailored_cv_data():
    """The assembled dict must validate as TailoredCVData — assembly is the contract
    boundary between the segmented LLM calls and the rest of the pipeline."""
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import assemble_segmented_cv

    outline = {"role_order": ["w1"], "summary_angle": "", "skills_focus": []}
    sections = {
        "summary": "Senior engineer with a delivery focus.",
        "work_entries": [_work("w1", "Engineer")],
        "skills": ["Python"],
        "education": [{"institution": "TU", "degree": "BSc", "field": "CS",
                       "start_date": "2014", "end_date": "2017"}],
        "languages": [{"language": "German", "level": "native"}],
        "projects": [{"name": "Side project", "bullets": ["built a thing"]}],
    }

    assembled = assemble_segmented_cv(outline, sections)
    cv = TailoredCVData.model_validate(assembled)

    assert cv.summary == "Senior engineer with a delivery focus."
    assert cv.skills == ["Python"]
    assert cv.education[0].institution == "TU"
    assert cv.languages[0].language == "German"
    assert cv.projects[0].name == "Side project"


def test_assembly_carries_contact_from_the_profile_sourced_section():
    """Contact is factual identity data sourced deterministically from the profile, not
    LLM-generated per segment (ADR-040) — assembly must carry it onto the CV."""
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import assemble_segmented_cv

    outline = {"role_order": [], "summary_angle": "", "skills_focus": []}
    sections = {
        "contact": {"name": "Marcus Berg", "email": "m@example.com", "location": "Berlin"},
        "summary": "", "work_entries": [], "skills": [],
        "education": [], "languages": [], "projects": [],
    }

    cv = TailoredCVData.model_validate(assemble_segmented_cv(outline, sections))

    assert cv.contact.name == "Marcus Berg"
    assert cv.contact.location == "Berlin"


def test_assembly_appends_work_entries_missing_from_role_order():
    """An entry the outline forgot to order must still appear (no silent data loss) —
    appended after the ordered ones, never dropped (ADR-040 truthfulness floor)."""
    from applire.services.cv import assemble_segmented_cv

    outline = {"role_order": ["w1"], "summary_angle": "", "skills_focus": []}
    sections = {
        "summary": "",
        "work_entries": [_work("w1", "Engineer"), _work("w2", "Intern")],
        "skills": [],
        "education": [],
        "languages": [],
        "projects": [],
    }

    assembled = assemble_segmented_cv(outline, sections)

    assert [w["role"] for w in assembled["work_history"]] == ["Engineer", "Intern"]


def test_assembly_ignores_role_order_ids_with_no_matching_entry():
    """A stale id in role_order (no matching work entry) must not invent an empty entry."""
    from applire.services.cv import assemble_segmented_cv

    outline = {"role_order": ["ghost", "w1"], "summary_angle": "", "skills_focus": []}
    sections = {
        "summary": "",
        "work_entries": [_work("w1", "Engineer")],
        "skills": [],
        "education": [],
        "languages": [],
        "projects": [],
    }

    assembled = assemble_segmented_cv(outline, sections)

    assert len(assembled["work_history"]) == 1
    assert assembled["work_history"][0]["role"] == "Engineer"
