# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Agent collector #788 line 2 — the #447 injection corpus through the MCP door, REAL-LLM tier.

The hermetic twin (`tests/unit/test_mcp_injection_canary.py`) assumes the worst
case — an extractor that obeys every posting — and gates that every
canary-carrying path of the agent door's result is listed in
`untrusted_content.fields`. This tier runs the same corpus through the same door
(`mcp.call_tool("analyze_jd", …)`, the real extraction + review loop) on the
operator's provider and records what the model actually lets through:

* **coverage** (asserted): every path whose value carries a canary is marked —
  the channel never launders posting text as trusted output, whatever the
  model did;
* **survival** (measured, printed): how many hostile cases planted their canary
  in the result at all. That is the model's behaviour, not a property Applire
  can promise; the marking is what lets the agent's own hardening act on it.

A measurement, not a CI gate (same reasoning as `test_injection_corpus_llm.py`):
fixture-gated on ``INTEGRATION_LLM``; run it deliberately per release. Cost on
`openai/gpt-5.6-luna` (Requesty, thinking off), measured 2026-10-20: see the
WP-A report of Hazelnut build 2.

What it does NOT measure: whether a tool-bearing agent OBEYS a marked canary.
That needs an agent in the loop (a blind probe), and the agent's hardening is
its vendor's (threat model §4.2b).
"""
import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

CORPUS_DIR = Path(__file__).parent.parent / "files" / "injection_corpus"
CASES = json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))["cases"]
HOSTILE = [c for c in CASES if c["kind"] == "hostile"]


def _instrument():
    """The coverage instrument lives with the hermetic tier — one definition."""
    spec = importlib.util.spec_from_file_location(
        "mcp_injection_canary",
        Path(__file__).parent.parent / "unit" / "test_mcp_injection_canary.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def fresh_db():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import importlib

    from applire.db.session import Base

    for name in ("application", "color_profile", "company", "cover_letter", "cv", "flow",
                 "gap", "job", "profile", "session", "uploads", "user", "user_settings"):
        importlib.import_module(f"applire.models.{name}")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def analyse_over_the_door(text: str, session) -> dict:
    """`analyze_jd` exactly as an agent calls it, on the configured provider."""
    from applire.mcp.server import mcp

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("applire.mcp.server.get_db", return_value=cm):
        out = await mcp.call_tool("analyze_jd", {"text": text})
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


async def run_case(case) -> dict:
    """One case → {id, survived, uncovered, review}."""
    instrument = _instrument()
    engine, factory = await fresh_db()
    try:
        async with factory() as session:
            text = (CORPUS_DIR / f"{case['id']}.txt").read_text(encoding="utf-8")
            payload = await analyse_over_the_door(text, session)
    finally:
        await engine.dispose()
    blob = json.dumps(payload, ensure_ascii=False).lower()
    return {
        "id": case["id"],
        "class": case["class"],
        "survived": [m for m in case["forbidden"] if m.lower() in blob],
        "uncovered": instrument.uncovered_canary_paths(payload, case["forbidden"] or ["\x00"]),
        "review": payload.get("review"),
    }


pytestmark = pytest.mark.skipif(
    not os.getenv("INTEGRATION_LLM"),
    reason="Real-LLM test — set INTEGRATION_LLM=1 (and a provider key) to run",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", HOSTILE, ids=lambda c: c["id"])
async def test_a_canary_that_survives_the_model_is_always_marked(case):
    result = await run_case(case)
    print(json.dumps(result, ensure_ascii=False))
    assert result["uncovered"] == [], f"{case['id']}: canary on unmarked paths {result['uncovered']}"
