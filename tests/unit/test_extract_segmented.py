"""
Unit tests for segmented CV/profile extraction (US195, ADR-047).

A capped model truncates a single full-profile extraction mid-JSON, which used to
surface as "couldn't parse" → the CV was silently dropped. Segmented extraction reads
the source section by section (outline → per-role detail → core) so no single call
needs a large output, mirroring the segmented CV *generation* (US189).

No Docker, no DB, no real LLM.

Run: pytest tests/unit/test_extract_segmented.py -v
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.profile.extract_segmented import (
    extract_profile_segmented,
    extract_with_fallback,
)
from applire.constants import SEGMENT_MAX_TOKENS, CV_EXTRACTION_MAX_TOKENS
from applire.exceptions import LLMTruncatedError, LLMTimeoutError


# ---------------------------------------------------------------------------
# extract_profile_segmented — outline → per-role detail → core, then assemble
# ---------------------------------------------------------------------------


def _make_provider():
    """Provider whose aparse_json returns by system-prompt fingerprint."""
    provider = AsyncMock()

    async def _route(prompt, *, system, temperature=0.1, max_tokens=4096, disable_thinking=None):
        s = system.lower()
        if "outliner" in s:
            return {"work_experience": [
                {"company": "Siemens AG", "role": "Senior Engineer",
                 "start_date": "2018", "end_date": "2023"},
                {"company": "Bosch GmbH", "role": "Engineer",
                 "start_date": "2015", "end_date": "2018"},
            ]}
        if "detail extractor" in s:
            # one fixed detail per role (mock ignores which role)
            return {"responsibilities": ["Led a team"], "achievements": ["Cut costs 20%"],
                    "technologies": ["Python", "C++"]}
        if "core profile extractor" in s:
            return {
                "personal_info": {"name": "Markus Brandt", "email": "m@b.de"},
                "professional_summary": {"de": None, "en": "QA lead"},
                "education": [{"institution": "TU München", "degree": "MSc"}],
                "certifications": [],
                "skills": [{"name": "Python", "category": "technical", "proficiency": "expert"}],
                "languages": [{"language": "German", "level": "Native"}],
                "publications": [],
                "volunteer_activities": [],
                "projects": [],
            }
        raise AssertionError(f"unexpected system prompt: {system!r}")

    provider.aparse_json.side_effect = _route
    return provider


@pytest.mark.asyncio
async def test_segmented_extraction_assembles_full_profile():
    provider = _make_provider()
    data = await extract_profile_segmented("RAW CV TEXT", provider)

    # core fields survive
    assert data["personal_info"]["name"] == "Markus Brandt"
    assert data["skills"][0]["name"] == "Python"
    # both outlined positions appear, with per-role detail merged in
    assert [w["company"] for w in data["work_experience"]] == ["Siemens AG", "Bosch GmbH"]
    assert data["work_experience"][0]["responsibilities"] == ["Led a team"]
    assert data["work_experience"][0]["technologies"] == ["Python", "C++"]
    # the assembled dict validates as a master profile
    from applire.schemas.profile import MasterProfileData
    MasterProfileData.model_validate(data)


@pytest.mark.asyncio
async def test_segmented_extraction_calls_detail_once_per_role():
    provider = _make_provider()
    await extract_profile_segmented("RAW", provider)
    details = [c for c in provider.aparse_json.call_args_list
              if "detail extractor" in c.kwargs["system"].lower()]
    assert len(details) == 2  # one per outlined position


@pytest.mark.asyncio
async def test_every_segment_call_is_output_bounded():
    """No single segment call may request a large output — that is the whole point."""
    provider = _make_provider()
    await extract_profile_segmented("RAW", provider)
    for c in provider.aparse_json.call_args_list:
        assert c.kwargs["max_tokens"] == SEGMENT_MAX_TOKENS
        assert SEGMENT_MAX_TOKENS < CV_EXTRACTION_MAX_TOKENS


@pytest.mark.asyncio
async def test_no_work_positions_still_returns_core():
    provider = AsyncMock()

    async def _route(prompt, *, system, temperature=0.1, max_tokens=4096, disable_thinking=None):
        s = system.lower()
        if "outliner" in s:
            return {"work_experience": []}
        if "core profile extractor" in s:
            return {"personal_info": {"name": "Solo"}, "skills": [], "work_experience": []}
        raise AssertionError(system)

    provider.aparse_json.side_effect = _route
    data = await extract_profile_segmented("RAW", provider)
    assert data["personal_info"]["name"] == "Solo"
    assert data["work_experience"] == []
    # detail extractor never called when there are no positions
    assert not any("detail extractor" in c.kwargs["system"].lower()
                   for c in provider.aparse_json.call_args_list)


# ---------------------------------------------------------------------------
# extract_with_fallback — upfront segment on small cap, reactive on truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_single_call_when_cap_unknown():
    """No known cap → the fast single call is used; segmentation is not invoked."""
    provider = AsyncMock()
    provider.aparse_json.return_value = {"personal_info": {"name": "X"}, "work_experience": []}

    with patch("applire.services.profile.extract_segmented.resolve_effective_output_cap",
               new=AsyncMock(return_value=0)):
        data = await extract_with_fallback(
            "RAW", provider, system="SYS", user_prompt="USER",
        )

    assert data["personal_info"]["name"] == "X"
    assert provider.aparse_json.call_count == 1
    _, kwargs = provider.aparse_json.call_args
    assert kwargs["max_tokens"] == CV_EXTRACTION_MAX_TOKENS


@pytest.mark.asyncio
async def test_fallback_segments_upfront_when_cap_small():
    """A known cap below the extraction ceiling → segment without the doomed single call."""
    provider = _make_provider()

    with patch("applire.services.profile.extract_segmented.resolve_effective_output_cap",
               new=AsyncMock(return_value=8192)):
        data = await extract_with_fallback(
            "RAW", provider, system="SYS", user_prompt="USER",
        )

    # no single full-profile call at CV_EXTRACTION_MAX_TOKENS was made
    assert not any(c.kwargs.get("max_tokens") == CV_EXTRACTION_MAX_TOKENS
                   for c in provider.aparse_json.call_args_list)
    assert [w["company"] for w in data["work_experience"]] == ["Siemens AG", "Bosch GmbH"]


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [LLMTruncatedError("cap"), LLMTimeoutError("slow")])
async def test_fallback_reactive_segments_on_truncation(exc):
    """Unknown cap, single call truncates/times out → switch to segmented, no crash."""
    provider = _make_provider()
    base_route = provider.aparse_json.side_effect

    async def _route(prompt, *, system, **kw):
        if system == "SYS":  # the monolithic single call
            raise exc
        return await base_route(prompt, system=system, **kw)

    provider.aparse_json.side_effect = _route

    with patch("applire.services.profile.extract_segmented.resolve_effective_output_cap",
               new=AsyncMock(return_value=0)):
        data = await extract_with_fallback(
            "RAW", provider, system="SYS", user_prompt="USER",
        )

    assert [w["company"] for w in data["work_experience"]] == ["Siemens AG", "Bosch GmbH"]
