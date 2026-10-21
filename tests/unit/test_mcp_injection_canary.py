# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Agent collector #788 line 2 — the injection corpus driven over the MCP door.

The #447 corpus (`tests/files/injection_corpus/`) had only ever met the
extraction PROMPT (`tests/integration/test_injection_corpus_llm.py`); nothing
drove it through the agent door, so the ADR-084 cl. 4 `untrusted_content`
marking was a field list nobody had checked against what a hostile posting can
actually put on a tool result.

This hermetic tier is the worst case: the stub extractor OBEYS every posting —
it plants the case's canary in every string field the extraction schema has
(quotes taken from the posting's own hostile line, so they survive the ADR-069
grounding floor). The property is coverage, at the delivery point: **every path
of a door's result whose value carries the canary is listed in that result's
`untrusted_content.fields`**. A path that carries posting text and is not
listed is the defect class this lane exists to catch. The real-provider tier
(`tests/integration/test_injection_corpus_mcp.py`) runs the same check on what
the model actually does.
"""
import json
import re
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.support.mcp_door import mcp_signing_secret  # noqa: F401 — autouse

CORPUS = Path(__file__).resolve().parents[1] / "files" / "injection_corpus"
CASES = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))["cases"]
HOSTILE = [c for c in CASES if c["kind"] == "hostile"]


# ── the coverage instrument ──────────────────────────────────────────────────


def leaf_paths(obj, prefix=""):
    """(path, string) for every string leaf; list indices fold to `[]`, the
    notation `_JD_DERIVED_FIELDS` uses (`scope_requirements[].quote`)."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from leaf_paths(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(obj, list):
        for value in obj:
            yield from leaf_paths(value, f"{prefix}[]")
    elif isinstance(obj, str):
        yield prefix, obj


def covered(path: str, declared: list[str]) -> bool:
    """A declared field covers itself, its list items and anything under it."""
    return any(
        path == f or path.startswith(f + "[]") or path.startswith(f + ".") for f in declared
    )


def uncovered_canary_paths(payload: dict, markers: list[str]) -> list[str]:
    declared = (payload.get("untrusted_content") or {}).get("fields") or []
    hits = []
    for path, value in leaf_paths({k: v for k, v in payload.items() if k != "untrusted_content"}):
        if any(m.lower() in value.lower() for m in markers) and not covered(path, declared):
            hits.append(path)
    return sorted(set(hits))


def test_the_instrument_flags_an_unlisted_path_and_passes_a_listed_one():
    """The instrument itself is a control: prove it fires before trusting its silence."""
    payload = {
        "required_skills": ["CANARY"],
        "scope_requirements": [{"quote": "CANARY here"}],
        "seniority_level": "CANARY",
        "untrusted_content": {"fields": ["required_skills", "scope_requirements[].quote"]},
    }
    assert uncovered_canary_paths(payload, ["canary"]) == ["seniority_level"]
    payload["untrusted_content"]["fields"].append("seniority_level")
    assert uncovered_canary_paths(payload, ["canary"]) == []


# ── the door ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _obedient_extraction(posting: str, marker: str) -> dict:
    """What a model that obeyed the posting completely would return."""
    hostile_line = next(line for line in posting.splitlines() if marker in line).strip()
    return {
        "company_name": marker,
        "role_title": marker,
        "required_skills": [marker],
        "nice_to_have_skills": [marker],
        "keywords": [marker],
        "seniority_level": marker,
        "company_culture_signals": [marker],
        "language_requirement": marker,
        "education_requirement": marker,
        "berufsbild_code": None,
        "berufsbild_label": marker,
        "scope_requirements": [
            {"kind": "team_size", "value": 7, "comparator": "approx", "quote": hostile_line,
             "level": "required"}
        ],
        "leadership_emphasis": {"emphasis": "balanced", "quote": hostile_line},
    }


def _provider(extraction: dict):
    calls = {"n": 0}

    async def _dispatch(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return extraction
        return {"approved": True, "issues": [], "feedback": ""}

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_dispatch)
    return provider


def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _call(name, args, db, provider=None):
    from contextlib import ExitStack

    from applire.mcp.server import mcp

    with ExitStack() as stack:
        stack.enter_context(patch("applire.mcp.server.get_db", return_value=_db_cm(db)))
        if provider is not None:
            stack.enter_context(patch("applire.mcp.server.get_provider", return_value=provider))
        out = await mcp.call_tool(name, args)
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", HOSTILE, ids=lambda c: c["id"])
async def test_every_canary_on_analyze_jd_sits_under_a_marked_path(case, db):
    posting = (CORPUS / f"{case['id']}.txt").read_text(encoding="utf-8")
    marker = case["forbidden"][0]
    payload = await _call(
        "analyze_jd", {"text": posting}, db, _provider(_obedient_extraction(posting, marker))
    )
    # The stub obeyed: the canary reached the result — otherwise this proves nothing.
    assert marker.lower() in json.dumps(payload, ensure_ascii=False).lower()
    assert uncovered_canary_paths(payload, case["forbidden"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", HOSTILE[:2], ids=lambda c: c["id"])
async def test_the_doors_that_echo_the_posting_later_are_covered_too(case, db):
    """The posting's text resurfaces on later reads: the job:// resource and the
    application doors (labels come from the posting when the caller set none)."""
    from applire.mcp.server import mcp

    posting = (CORPUS / f"{case['id']}.txt").read_text(encoding="utf-8")
    marker = case["forbidden"][0]
    analysed = await _call(
        "analyze_jd", {"text": posting}, db, _provider(_obedient_extraction(posting, marker))
    )
    created = await _call("create_application", {"job_id": analysed["id"]}, db)
    flow = await _call("start_flow", {"job_id": analysed["id"]}, db)
    flow_state = await _call("get_flow_state", {"flow_id": flow["flow_id"]}, db)
    listed = await _call("list_applications", {}, db)
    got = await _call("get_application", {"application_id": created["id"]}, db)
    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)):
        contents = await mcp.read_resource(f"job://{analysed['id']}")
    resource = json.loads(list(contents)[0].content)

    for name, payload in (("create_application", created), ("get_application", got),
                          ("job://", resource), ("start_flow", flow),
                          ("get_flow_state", flow_state)):
        assert uncovered_canary_paths(payload, case["forbidden"]) == [], name
    for row in listed if isinstance(listed, list) else listed.get("items", [listed]):
        if isinstance(row, dict) and "untrusted_content" in row:
            assert uncovered_canary_paths(row, case["forbidden"]) == []
