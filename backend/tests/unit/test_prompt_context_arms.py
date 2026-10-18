# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""E068 US370 — the prompt-context arms (preamble / primer / analysis field).

The default arm must leave every call byte-identical; each arm must reach the model
in its own shape; arm C's field must never reach a caller.
"""

from __future__ import annotations

import json

import pytest

from applire.prompts import preamble
from applire.prompts.primers import primer_for


@pytest.fixture(autouse=True)
def _v2_pipelines(monkeypatch):
    """The context applies on the v2 pipelines only (ruling R-10); the arm tests run there.
    The scope tests below override this."""
    monkeypatch.setenv("CV_PIPELINE", "v2")
    monkeypatch.setenv("LETTER_PIPELINE", "v2")


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


@pytest.mark.parametrize("env", [None, "", "nonsense", " C "])
def test_default_arm_is_c(monkeypatch, env):
    """Ruling W3-6 = B (founder 2026-10-18): arm c ships as the default; only "off" opts out."""
    if env is None:
        monkeypatch.delenv("PROMPT_CONTEXT", raising=False)
    else:
        monkeypatch.setenv("PROMPT_CONTEXT", env)
    assert preamble.prompt_context() == "c"


def test_prompt_context_is_a_declared_setting_defaulting_to_c():
    from applire.settings_registry import get

    entry = get("PROMPT_CONTEXT")
    assert entry.default == "c" and entry.in_env_example is True


@pytest.mark.asyncio
async def test_off_is_byte_identical(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTEXT", "off")
    p = _Recorder()
    await preamble.aparse_json(p, "cv:writer", "PROMPT", system="SYS", max_tokens=100, json_schema=_SCHEMA)
    assert p.calls == [{"prompt": "PROMPT", "primer": [], "system": "SYS", "max_tokens": 100, "json_schema": _SCHEMA}]
    await preamble.acomplete(p, "review_rewrite:editor", "PROMPT", system="SYS")
    assert p.calls[1] == {"prompt": "PROMPT", "primer": [], "system": "SYS"}


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


# ── the JSON-break mode (10-17 screen: ministral-8b letter, "Expecting ',' delimiter") ──

#: The failure shape: a double quote inside the analysis text ends the string early.
_BROKEN = (
    '{"analysis": "The posting stresses "Kubernetes" and on-call; lead with the EKS migration.",\n'
    ' "body": ["Dear Ms Weber, I am applying for the Platform Engineer role."], "closing": "Kind regards"}'
)


def _decode_error(doc: str) -> json.JSONDecodeError:
    try:
        json.loads(doc)
    except json.JSONDecodeError as exc:
        return exc
    raise AssertionError("fixture parses")


class _Breaking(_Recorder):
    """Raises like OpenRouterProvider.aparse_json (json.loads on the reply) for the first N calls."""

    def __init__(self, docs, reply=None):
        super().__init__(reply=reply)
        self.docs = list(docs)

    async def aparse_json(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if self.docs:
            raise _decode_error(self.docs.pop(0))
        return dict(self.reply)


def test_the_fixture_is_the_screen_failure_shape():
    exc = _decode_error(_BROKEN)
    assert exc.msg == "Expecting ',' delimiter"


@pytest.mark.asyncio
async def test_a_break_inside_the_analysis_value_is_repaired_without_a_second_call(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTEXT", "c")
    p = _Breaking([_BROKEN])
    out = await preamble.aparse_json(p, "cover_letter:writer", "P", system="SYS")
    assert out == {"body": ["Dear Ms Weber, I am applying for the Platform Engineer role."], "closing": "Kind regards"}
    assert len(p.calls) == 1


@pytest.mark.asyncio
async def test_an_unrepairable_break_retries_once_without_the_field(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTEXT", "c")
    broken_body = '{"analysis": "fine", "body": ["unterminated], "closing": "x"}'
    p = _Breaking([broken_body], reply={"body": ["ok"]})
    out = await preamble.aparse_json(p, "cover_letter:writer", "P", system="SYS", max_tokens=100)
    assert out == {"body": ["ok"]}
    first, retry = p.calls
    assert '"analysis"' in first["prompt"] and first["max_tokens"] == 100 + preamble.ANALYSIS_EXTRA_TOKENS
    assert retry["prompt"] == "P" and retry["max_tokens"] == 100  # the call without arm c's field
    assert retry["system"].startswith("CONTEXT")  # the preamble stays


@pytest.mark.asyncio
async def test_a_retry_that_breaks_too_raises_into_the_sites_own_handling(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTEXT", "c")
    p = _Breaking(['{"body": [', '{"body": ['])
    with pytest.raises(json.JSONDecodeError):
        await preamble.aparse_json(p, "cover_letter:writer", "P", system="SYS")
    assert len(p.calls) == 2


@pytest.mark.parametrize("doc", [
    '{"body": "a "b" c", "x": 1}',  # no analysis key first: not ours to repair
    '{"analysis": "a "b" c" "no boundary"}',
    '{"analysis": "a "b", "body": [unterminated}',  # break after the boundary too
])
def test_repair_refuses_anything_but_the_analysis_break(doc):
    assert preamble.repair_broken_analysis(doc) is None


def test_repair_reads_a_fenced_reply():
    assert preamble.repair_broken_analysis("```json\n" + _BROKEN + "\n```")["closing"] == "Kind regards"


@pytest.mark.asyncio
async def test_letter_writer_site_survives_the_screen_shape(monkeypatch):
    """The call that ended the 10-17 run: cover_letter.py's writer call has no
    JSONDecodeError handling of its own, so the hook must not let this shape escape."""
    monkeypatch.setenv("PROMPT_CONTEXT", "c")
    p = _Breaking([_BROKEN])
    out = await preamble.aparse_json(p, "cover_letter:writer", "P", system="SYS")
    assert "analysis" not in out and out["closing"] == "Kind regards"


def test_own_system_strips_only_the_preamble():
    framed = preamble.frame_system("You are a CV language reviewer.", "cv_language:reviewer", "c")
    assert preamble.own_system(framed) == "You are a CV language reviewer."
    assert preamble.own_system("You are a CV language reviewer.") == "You are a CV language reviewer."
    assert preamble.own_system(None) == ""


@pytest.mark.asyncio
async def test_the_mock_provider_routes_a_framed_call_like_the_plain_one(monkeypatch):
    """The mock routes on substrings of the system prompt; the preamble says "claim",
    which the Oracle segmenter branch matches on — the mock must route on the site's prompt."""
    from applire.providers.llm.mock import MockLLMProvider

    site_prompt = "You write one segment of a tailored CV."  # "segment", and no "claim" of its own
    mock = MockLLMProvider()
    plain = await mock.aparse_json("P", system=site_prompt)
    framed = await mock.aparse_json("P", system=preamble.frame_system(site_prompt, "cv:writer", "c"))
    assert "claims" not in framed and framed == plain


# ── scope: v2 pipelines only (ruling R-10, main 2026-10-18) ──────────────────────

_LETTER_SITES = sorted(k for k, v in preamble.SITES.items() if v.doc == "letter")
_CV_KEYED_SITES = sorted(k for k, v in preamble.SITES.items() if v.doc != "letter")


@pytest.mark.asyncio
@pytest.mark.parametrize("site", sorted(preamble.SITES))
async def test_v1_with_prompt_context_unset_is_byte_identical(monkeypatch, site):
    monkeypatch.delenv("PROMPT_CONTEXT", raising=False)
    monkeypatch.delenv("CV_PIPELINE", raising=False)
    monkeypatch.delenv("LETTER_PIPELINE", raising=False)
    p = _Recorder()
    await preamble.aparse_json(p, site, "PROMPT", system="SYS", max_tokens=100, json_schema=_SCHEMA)
    await preamble.acomplete(p, site, "PROMPT", system="SYS")
    assert p.calls == [
        {"prompt": "PROMPT", "primer": [], "system": "SYS", "max_tokens": 100, "json_schema": _SCHEMA},
        {"prompt": "PROMPT", "primer": [], "system": "SYS"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("site", sorted(preamble.SITES))
async def test_v2_with_prompt_context_unset_gives_c(monkeypatch, site):
    monkeypatch.delenv("PROMPT_CONTEXT", raising=False)
    p = _Recorder(reply={"analysis": "x", "summary": "s"})
    out = await preamble.aparse_json(p, site, "PROMPT", system="SYS", max_tokens=100, json_schema=_SCHEMA)
    call = p.calls[0]
    assert out == {"summary": "s"}
    assert call["system"].startswith("CONTEXT") and call["system"].endswith("SYS")
    assert '"analysis"' in call["prompt"] and call["max_tokens"] == 100 + preamble.ANALYSIS_EXTRA_TOKENS
    assert list(call["json_schema"]["schema"]["properties"])[0] == "analysis"


@pytest.mark.asyncio
async def test_each_site_follows_its_own_pipeline_switch(monkeypatch):
    """Letter sites follow LETTER_PIPELINE; CV, JD and gap sites follow CV_PIPELINE."""
    monkeypatch.delenv("PROMPT_CONTEXT", raising=False)
    for cv, letter in (("v2", "v1"), ("v1", "v2")):
        monkeypatch.setenv("CV_PIPELINE", cv)
        monkeypatch.setenv("LETTER_PIPELINE", letter)
        for site in _LETTER_SITES:
            assert preamble.site_arm(site) == ("c" if letter == "v2" else "off"), site
        for site in _CV_KEYED_SITES:
            assert preamble.site_arm(site) == ("c" if cv == "v2" else "off"), site
    assert {"job_analysis:writer", "gap_analysis:writer", "cv:writer"} <= set(_CV_KEYED_SITES)
    assert {"cover_letter:writer", "letter_condense:writer", "review_rewrite:letter_editor"} <= set(_LETTER_SITES)
