# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The letter's wrong-shape seam (US368 gate re-run 2026-10-18, ruling after W3-7 interim).

Captured on gpt-5.6-luna, v2 + PROMPT_CONTEXT=c, nursing_priya_relocator.cross_en2de:
the pre-verdict condense answered with a recipient-only object (the body was lost),
the terminal corrector then returned ``body`` as a bare list, and ``body_word_count``
crashed — the run failed. Each part below is pinned on that captured shape:

(a) the condense keeps the preamble but gets no analysis field (its contract is
    "keep the same JSON structure");
(b) a wrong-shape condense or corrector reply goes to the site's existing handling
    (the condense keeps the current letter, the loop keeps its last good draft) on
    v1 and v2 alike, and the counters never crash on a list body.

No document text is rewritten (ADR-094 cl. 1).
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.prompts import preamble  # noqa: E402
from applire.services.cover_letter_positioning import (  # noqa: E402
    body_word_count,
    has_closing_paragraph,
    letter_shape_ok,
)
from applire.services.reviewer import review_and_refine  # noqa: E402

#: The captured condense reply (gate re-run 2026-10-18, verbatim).
CONDENSE_RECIPIENT_ONLY = {"recipient": "analysis", "title": None, "company": "Rhein Klinikum Bonn", "address": None}
#: The captured corrector shape: body as a bare list of paragraphs.
CORRECTOR_LIST_BODY = {
    "recipient": {"name": None, "company": "Rhein Klinikum Bonn"},
    "date": "18.10.2026",
    "body": ["Sehr geehrte Damen und Herren,", "ich bewerbe mich als Pflegefachkraft Intensivpflege."],
}
GOOD = {
    "recipient": {"name": None, "company": "Rhein Klinikum Bonn"},
    "body": {"paragraphs": ["ich bewerbe mich als Pflegefachkraft.", "Mit vier Jahren Intensivpflege bringe ich mit."]},
}


# ── (b) the predicate and the counters ────────────────────────────────────────

@pytest.mark.parametrize("shape", [CONDENSE_RECIPIENT_ONLY, CORRECTOR_LIST_BODY, None, [], {"body": {"paragraphs": []}},
                                   {"body": {"paragraphs": ["ok", 3]}}])
def test_wrong_shapes_are_not_letter_shaped(shape):
    assert letter_shape_ok(shape) is False


def test_the_stored_shape_is_letter_shaped():
    assert letter_shape_ok(GOOD) is True


@pytest.mark.parametrize("shape", [CORRECTOR_LIST_BODY, CONDENSE_RECIPIENT_ONLY, {"body": "text"}, None])
def test_the_counters_never_crash_on_a_wrong_shape(shape):
    """The crash site (`_persist_and_measure` -> `body_word_count`) and v1's retain_if
    predicate (`has_closing_paragraph`, the same `.get` on the body)."""
    assert isinstance(body_word_count(shape), int)
    assert isinstance(has_closing_paragraph(shape), bool)


def test_a_list_body_is_counted_not_dropped():
    assert body_word_count(CORRECTOR_LIST_BODY) == 11
    assert body_word_count(GOOD) == body_word_count({"body": {"paragraphs": GOOD["body"]["paragraphs"]}})


# ── (b) the condense seam ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [CONDENSE_RECIPIENT_ONLY, CORRECTOR_LIST_BODY])
async def test_a_wrong_shape_condense_raises_into_the_keep_the_letter_handler(monkeypatch, reply):
    """Every `_condense_call` caller wraps it in `except Exception` -> keep the current
    letter. Before the fix the captured reply was returned and stored as the letter."""
    from applire.services.cover_letter import _condense_call

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=dict(reply))
    with pytest.raises(ValueError, match="wrong shape"):
        await _condense_call(provider, "P", stage="letter_condense_pre_verdict", system="S")


@pytest.mark.asyncio
async def test_a_letter_shaped_condense_passes_through_unchanged():
    from applire.services.cover_letter import _condense_call

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=dict(GOOD))
    assert await _condense_call(provider, "P", stage="letter_condense_pre_verdict", system="S") == GOOD


# ── (b) the review-loop seam ──────────────────────────────────────────────────

def _loop_provider(corrector_reply):
    rejection = {"approved": False, "issues": [{"text": "The closing is missing.", "severity": "blocking"}],
                 "feedback": "Add a closing."}
    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=[rejection, dict(corrector_reply)])
    return provider


@pytest.mark.asyncio
async def test_a_wrong_shape_corrector_draft_keeps_the_reviewed_draft():
    result = await review_and_refine(
        source="src", draft=dict(GOOD),
        generator_prompt_fn=lambda d, f, s: "retry", generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev", reviewer_system="rev",
        provider=_loop_provider(CORRECTOR_LIST_BODY), max_retries=2,
        chain_id="letter_terminal_review", draft_shape_ok=letter_shape_ok,
    )
    assert result["body"] == GOOD["body"]


@pytest.mark.asyncio
async def test_without_a_shape_predicate_the_loop_is_unchanged():
    """Chains that pass no predicate (CV, JD, extraction) keep today's behaviour."""
    provider = _loop_provider(CORRECTOR_LIST_BODY)
    provider.aparse_json.side_effect = [
        {"approved": False, "issues": [{"text": "x", "severity": "blocking"}], "feedback": "x"},
        dict(CORRECTOR_LIST_BODY),
        {"approved": True, "issues": [], "feedback": ""},
    ]
    result = await review_and_refine(
        source="src", draft=dict(GOOD),
        generator_prompt_fn=lambda d, f, s: "retry", generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev", reviewer_system="rev",
        provider=provider, max_retries=2, chain_id="cv_tailoring",
    )
    assert result["body"] == CORRECTOR_LIST_BODY["body"]


def test_every_letter_loop_passes_the_shape_predicate():
    """v1 and v2 alike: the three letter review_and_refine call sites name it."""
    src = (_backend / "applire" / "services" / "cover_letter.py").read_text()
    calls = src.split("await review_and_refine(")[1:]
    assert len(calls) == 3
    for call in calls:
        head = call.split("\n        )\n", 1)[0] if "\n        )\n" in call else call[:3000]
        assert "draft_shape_ok=letter_shape_ok" in call[:4000], head[:200]


# ── (a) the condense gets the preamble, not the analysis field ──────────────────

class _Recorder:
    def __init__(self):
        self.calls = []

    async def aparse_json(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return dict(GOOD)


@pytest.mark.asyncio
async def test_condense_under_c_gets_the_preamble_and_no_analysis_field(monkeypatch):
    monkeypatch.setenv("LETTER_PIPELINE", "v2")
    monkeypatch.delenv("PROMPT_CONTEXT", raising=False)
    p = _Recorder()
    await preamble.aparse_json(p, "letter_condense:writer", "CONDENSE PROMPT", system="SYS", max_tokens=100)
    call = p.calls[0]
    assert call["system"].startswith("CONTEXT") and call["system"].endswith("SYS")
    assert call["prompt"] == "CONDENSE PROMPT" and call["max_tokens"] == 100


@pytest.mark.asyncio
async def test_the_letter_writer_under_c_still_gets_the_analysis_field(monkeypatch):
    monkeypatch.setenv("LETTER_PIPELINE", "v2")
    monkeypatch.delenv("PROMPT_CONTEXT", raising=False)
    p = _Recorder()
    await preamble.aparse_json(p, "cover_letter:writer", "WRITER PROMPT", system="SYS", max_tokens=100)
    assert '"analysis"' in p.calls[0]["prompt"]
