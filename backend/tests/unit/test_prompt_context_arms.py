# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""E068 US370 — the prompt-context arms (preamble / primer / analysis field).

The default arm must leave every call byte-identical; each arm must reach the model
in its own shape; arm C's field must never reach a caller.
"""

from __future__ import annotations

import pytest

from applire.prompts import preamble
from applire.prompts.primers import primer_for


class _Recorder:
    def __init__(self, reply=None):
        self.calls: list[dict] = []
        self.reply = reply

    async def aparse_json(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, "primer": preamble.primer_turns(), **kwargs})
        return dict(self.reply) if self.reply is not None else {"ok": True}

    async def acomplete(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, "primer": preamble.primer_turns(), **kwargs})
        return "text"


_SCHEMA = {"name": "x", "strict": True,
           "schema": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}


@pytest.mark.asyncio
@pytest.mark.parametrize("env", [None, "", "off", "nonsense"])
async def test_off_is_byte_identical(monkeypatch, env):
    if env is None:
        monkeypatch.delenv("PROMPT_CONTEXT", raising=False)
    else:
        monkeypatch.setenv("PROMPT_CONTEXT", env)
    p = _Recorder()
    await preamble.aparse_json(p, "cv:writer", "PROMPT", system="SYS", max_tokens=100, json_schema=_SCHEMA)
    assert p.calls == [{"prompt": "PROMPT", "primer": [], "system": "SYS", "max_tokens": 100, "json_schema": _SCHEMA}]


@pytest.mark.asyncio
async def test_unknown_site_passes_through(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTEXT", "c")
    p = _Recorder(reply={"analysis": "kept", "a": 1})
    out = await preamble.aparse_json(p, preamble.loop_site("profile_extraction", "reviewer"), "P", system="S")
    assert p.calls[0]["system"] == "S" and p.calls[0]["prompt"] == "P"
    assert out == {"analysis": "kept", "a": 1}


@pytest.mark.asyncio
async def test_arm_a_frames_the_system_prompt_with_you_are_here(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTEXT", "a")
    p = _Recorder()
    await preamble.aparse_json(p, preamble.loop_site("cv_terminal_review", "reviewer"), "P", system="SYS")
    sys = p.calls[0]["system"]
    assert sys.startswith("CONTEXT") and sys.endswith("--- YOUR TASK ---\nSYS")
    assert "fails in that interview" in sys
    here = [line for line in sys.splitlines() if "you are here" in line]
    assert here == ["4. CV review — reviewers check grounding, language and duplicates; a corrector revises."
                    "   <- you are here"]
    assert p.calls[0]["prompt"] == "P" and p.calls[0]["primer"] == []


@pytest.mark.asyncio
async def test_arm_b_adds_the_primer_turn_only_inside_the_call(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTEXT", "b")
    p = _Recorder()
    await preamble.aparse_json(p, "cover_letter:writer", "P", system="SYS")
    q, a = primer_for("letter", "writer")
    assert p.calls[0]["primer"] == [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    assert p.calls[0]["system"].startswith("CONTEXT")
    assert preamble.primer_turns() == []  # reset after the call


@pytest.mark.asyncio
async def test_arm_c_puts_analysis_first_and_strips_it(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTEXT", "c")
    p = _Recorder(reply={"analysis": "reasoning", "summary": "s"})
    out = await preamble.aparse_json(p, "cv:writer", "P", system="SYS", max_tokens=100, json_schema=_SCHEMA)
    call = p.calls[0]
    assert out == {"summary": "s"}
    assert call["prompt"].startswith("P\n\n") and '"analysis"' in call["prompt"]
    assert call["max_tokens"] == 100 + preamble.ANALYSIS_EXTRA_TOKENS
    sch = call["json_schema"]["schema"]
    assert list(sch["properties"]) == ["analysis", "summary"] and sch["required"] == ["analysis", "summary"]
    assert list(_SCHEMA["schema"]["properties"]) == ["summary"]  # the caller's schema is not mutated


@pytest.mark.asyncio
async def test_acomplete_frames_but_adds_no_analysis(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTEXT", "c")
    p = _Recorder()
    out = await preamble.acomplete(p, "review_rewrite:editor", "P", system="SYS")
    assert out == "text" and p.calls[0]["prompt"] == "P" and p.calls[0]["system"].startswith("CONTEXT")


def test_openrouter_messages_carry_the_primer_between_system_and_task(monkeypatch):
    from applire.providers.llm.openrouter import _build_messages

    assert _build_messages("P", "S") == [{"role": "system", "content": "S"}, {"role": "user", "content": "P"}]
    token = preamble._primer_turns.set(({"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}))
    try:
        assert [m["role"] for m in _build_messages("P", "S")] == ["system", "user", "assistant", "user"]
    finally:
        preamble._primer_turns.reset(token)


def test_every_bench_stage_has_a_site():
    """The stages a v2 bench run logs (10-17) all resolve to a site, so no arm skips one."""
    for chain in ("cv_tailoring", "cv_language", "cv_language_recheck", "cv_terminal_review",
                  "cover_letter", "letter_terminal_review", "job_analysis"):
        for role in ("reviewer", "generator"):
            assert preamble.loop_site(chain, role) in preamble.SITES
    for site in ("cv:writer", "cover_letter:writer", "letter_condense:writer", "review_rewrite:editor",
                 "job_analysis:writer", "gap_analysis:writer"):
        assert site in preamble.SITES
