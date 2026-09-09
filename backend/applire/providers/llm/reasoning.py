# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Reasoning traces: consume the answer, never the thinking (founder ruling M-4).

Applire's write paths ask a model for a typed JSON batch. A reasoning model
answers that question twice — once as a chain of thought, once as the answer —
and the two arrive on different channels depending on the vendor:

===================  ==========================================================
OpenRouter           ``message.reasoning`` / ``message.reasoning_details``, a
                     sibling of ``message.content``; the token count lands in
                     ``usage.completion_tokens_details.reasoning_tokens``.
Requesty             same OpenAI-compatible shape.
Anthropic            ``thinking`` content blocks beside the ``text`` blocks;
                     ``anthropic.py:_text()`` already keeps only ``text``.
Ollama               ``message.thinking`` when the model declares it, and an
                     inline ``<think>…</think>`` span in ``message.content``
                     when it does not.
Any gateway          an inline ``<think>`` / ``<thinking>`` / ``<reasoning>``
                     span leaking into ``content``, which is what breaks
                     ``json.loads`` — the failure this module exists for.
===================  ==========================================================

The sibling-channel shapes are already safe: nothing reads them. The **inline**
shape is not: a leaked ``<think>`` span in front of a JSON object makes
``json.loads`` raise, ``reconcile()`` swallows the exception into an empty
result (``engine.py:100-102``), and the candidate's answer is gone with no
error anywhere — indistinguishable from a model that chose to say nothing
(``o3/failure-taxonomy-2026-09-09.md`` §3.1/§3.6, the same silent-loss class).

So: strip the trace at the provider seam, before any parse, on every provider.

**The trace is dropped, not stored.** ``llm_usage`` counts reasoning tokens
(``usage.py``) and nothing else; the trace text itself reaches only the debug
log, which is off by default and already carries full prompts and completions
(``debug_log.py``) — it is handed over through ``note_trace`` /
``take_trace`` rather than returned to the caller, so no code path can consume
a trace as if it were the answer.

Nothing in this module may raise into a generation.
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

# The tag names models actually emit inline. Deliberately a closed list: a
# regex over "any XML-ish tag" would eat a legitimate ``<think>`` inside a
# cover-letter draft the candidate wrote, and this runs on document text too.
_TAG_NAMES = ("think", "thinking", "reasoning", "reflection", "scratchpad")
_TAGS = "|".join(_TAG_NAMES)

# A complete span: <think> … </think>, any case, newlines included.
_PAIRED = re.compile(rf"<\s*({_TAGS})\s*>.*?<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL)
# An opening tag whose close never arrived (the budget ran out mid-trace):
# everything after it is trace, so the answer is whatever came BEFORE it.
_UNCLOSED = re.compile(rf"<\s*({_TAGS})\s*>.*\Z", re.IGNORECASE | re.DOTALL)
# A closing tag with no opening one — some models emit the trace bare and only
# mark where it ended. Everything up to and including it is trace.
_ORPHAN_CLOSE = re.compile(rf"\A.*?<\s*/\s*({_TAGS})\s*>", re.IGNORECASE | re.DOTALL)

_last_trace: ContextVar[str | None] = ContextVar("llm_last_reasoning_trace", default=None)


def split_reasoning(text: str) -> tuple[str, str]:
    """Return ``(answer, trace)`` for one completion.

    Order matters. A paired span is removed wherever it sits; only then is an
    unclosed opener treated as "the rest is trace", because a text that has both
    a complete span and a later truncated one must lose both. The orphan-close
    case is checked last and only when no opener was seen at all, so a normal
    ``</think>`` belonging to a span already removed cannot eat the answer.

    Never raises: on any surprise the input is returned unchanged as the answer.
    """
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else "", ""
    try:
        parts: list[str] = []

        def _keep(match: re.Match[str]) -> str:
            parts.append(match.group(0))
            return ""

        answer = _PAIRED.sub(_keep, text)

        unclosed = _UNCLOSED.search(answer)
        if unclosed:
            parts.append(unclosed.group(0))
            answer = answer[: unclosed.start()]
        elif not parts:
            orphan = _ORPHAN_CLOSE.match(answer)
            if orphan:
                parts.append(orphan.group(0))
                answer = answer[orphan.end() :]

        return answer.strip(), "".join(parts)
    except Exception:  # pragma: no cover - defensive; a strip may not break a call
        logger.debug("reasoning: split failed, passing the completion through unchanged")
        return text, ""


def clean_completion(text: str, *, model: str = "", method: str = "") -> str:
    """Strip an inline reasoning trace and hand it to the debug log.

    The single seam every provider calls on the text it is about to return. A
    stripped trace is a WARNING, not a debug line: it means the operator's model
    is spending output budget on thinking that Applire pays for and discards,
    which is exactly the number ``docs/llm-models.md`` asks an operator to act on.
    """
    if not isinstance(text, str):
        # A provider handed back None (an empty completion shape its own guard
        # let through). Pass it on untouched — turning it into "" here would
        # hide the shape from the caller that knows what to do with it.
        return text
    answer, trace = split_reasoning(text)
    if trace:
        note_trace(trace)
        logger.warning(
            "model=%s [%s] emitted a %d-char inline reasoning trace before its "
            "answer; stripped before parsing (the answer kept %d chars)",
            model or "?", method or "?", len(trace), len(answer),
        )
    return answer


def finalise_completion(
    response: Any, text: str, *, model: str = "", method: str = ""
) -> str:
    """Strip the trace, then refuse to return an answer that is not there.

    The second half is the control M-4 is missing without it. A reasoning model
    can spend its whole output budget thinking and return an EMPTY completion
    with a normal stop reason — no timeout, no ``finish_reason="length"``, so
    ``raise_if_truncated`` never fires. `deepseek/deepseek-v4-flash-0731` did
    exactly that on 4 of 30 reconcile turns at 32,768 completion tokens each
    (`o3/failure-taxonomy-2026-09-09.md` §3.7): the call succeeded, cost a full
    budget, and produced nothing. Downstream, ``json.loads("")`` raises,
    ``reconcile()`` swallows it into an empty result (`engine.py:100-102`), and
    the candidate's answer is gone with no signal anywhere.

    Tokens billed with no answer returned IS a budget exhaustion, whatever the
    provider calls it — so it is raised as :class:`LLMTruncatedError`, which
    puts it on the one path built for this: ``retry_on_truncation`` retries once
    with a doubled budget (M-4's "raise the budgets", applied only where a run
    actually needed it), and a second failure surfaces as data loss instead of
    silence.

    An empty completion that cost NO completion tokens is a different animal —
    an upstream hiccup, already handled by ``raise_if_no_completion`` — and is
    passed through unchanged.
    """
    answer = clean_completion(text, model=model, method=method)
    if isinstance(answer, str) and not answer.strip():
        from applire.providers.llm.usage import extract_usage

        try:
            usage = extract_usage(response)
        except Exception:  # pragma: no cover - defensive
            usage = None
        spent = usage.completion_tokens if usage else 0
        if spent > 0:
            from applire.exceptions import LLMTruncatedError

            logger.warning(
                "model=%s [%s] billed %d completion tokens and returned no answer "
                "(%d of them reported as reasoning); treating as a budget "
                "exhaustion rather than an empty result",
                model or "?", method or "?", spent,
                usage.reasoning_tokens if usage else 0,
            )
            raise LLMTruncatedError(
                f"Model {model or '?'} spent {spent} completion tokens and returned "
                "no answer (reasoning consumed the output budget). Raise max_tokens, "
                "bound reasoning, or choose a model that answers."
            )
    return answer


def note_trace(trace: str) -> None:
    """Stash the trace for the debug log; never returned to a caller."""
    try:
        _last_trace.set(trace or None)
    except Exception:  # pragma: no cover - defensive
        pass


def take_trace() -> str | None:
    """Read and clear the stashed trace (the debug log's one reader)."""
    try:
        trace = _last_trace.get()
        _last_trace.set(None)
        return trace
    except Exception:  # pragma: no cover - defensive
        return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def extract_reasoning_tokens(response: Any) -> int | None:
    """Reasoning tokens the PROVIDER reported, or None when it reported none.

    OpenAI-compatible gateways (OpenRouter, Requesty, OpenAI) nest the count at
    ``usage.completion_tokens_details.reasoning_tokens`` and already include it
    in ``completion_tokens``; this reads the split, it does not add to the total.
    Anthropic bills thinking inside ``output_tokens`` with no separate field and
    Ollama reports none — both correctly yield ``None`` (unknown), which the row
    stores as 0 rather than as a fabricated split.
    """
    if response is None:
        return None
    try:
        if isinstance(response, dict):
            usage: Any = response.get("usage")
        else:
            usage = getattr(response, "usage", None)
        if usage is None:
            return None
        if isinstance(usage, dict):
            details: Any = usage.get("completion_tokens_details") or usage.get(
                "output_tokens_details"
            )
        else:
            details = getattr(usage, "completion_tokens_details", None) or getattr(
                usage, "output_tokens_details", None
            )
        if details is None:
            return None
        if isinstance(details, dict):
            return _as_int(details.get("reasoning_tokens"))
        return _as_int(getattr(details, "reasoning_tokens", None))
    except Exception:  # pragma: no cover - defensive
        return None
