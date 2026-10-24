# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Read a provider's JSON reply: the first JSON value, never half an answer.

Hazelnut build 4, R2-13 (P-1; ADR-009 amended 2026-10-24). Every provider's
``aparse_json`` hands its reply text to :func:`parse_json_reply` — one parse,
one set of rules, so a provider cannot drift back to a strict tail.

The rules (founder 2026-10-24):

* Whitespace and ONE opening code fence (```` ``` ```` / ```` ```json ````) are
  stripped; then the FIRST JSON value is decoded with
  ``json.JSONDecoder().raw_decode``.
* A remainder that is not JSON — prose, a closing fence, whitespace — is
  discarded and the reply accepted. An always-on INFO line names the discard by
  length and kind only (the PII boundary: always-on logs carry no candidate
  content); the tail TEXT reaches only the dev-only debug log, handed over via
  :func:`note_discarded_tail` / :func:`take_discarded_tail` (the
  ``reasoning.note_trace`` precedent).
* A remainder that itself begins a JSON value that decodes (a second object,
  also behind a closing fence), or a remainder that is entirely a JSON scalar,
  raises :class:`~applire.exceptions.LLMResponseParseError`: half an answer is
  never kept unseen.
* A reply with no JSON value at its start (empty, prose first, broken JSON)
  raises :class:`~applire.exceptions.LLMResponseParseError` too. Leading prose
  is not skipped — the ruling covered trailing text only.

The error is also a ``json.JSONDecodeError`` (``doc`` = the reply text), so the
existing ``except json.JSONDecodeError`` handlers around ``aparse_json`` keep
catching it, and the debug log can record the raw reply from ``exc.doc``.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

from applire.exceptions import LLMResponseParseError

logger = logging.getLogger(__name__)

_DECODER = json.JSONDecoder()
_FENCE = "```"
_WS = " \t\r\n"

_last_tail: ContextVar[str | None] = ContextVar("llm_last_discarded_tail", default=None)


def note_discarded_tail(tail: str | None) -> None:
    """Hand the discarded tail of the current call to the debug log."""
    _last_tail.set(tail)


def take_discarded_tail() -> str | None:
    """The tail the current call discarded, once (the debug log reads it)."""
    tail = _last_tail.get()
    _last_tail.set(None)
    return tail


def _skip_opening_fence(text: str, i: int) -> int:
    """Index after an opening fence line (```` ```json\\n ````) at ``i``, else ``i``."""
    if not text.startswith(_FENCE, i):
        return i
    j = i + len(_FENCE)
    # an optional info string (``json``, ``JSON``) up to the end of the line
    while j < len(text) and text[j] not in "{[\r\n" and text[j] not in _WS:
        j += 1
    while j < len(text) and text[j] in _WS:
        j += 1
    return j


def _strip_fences_and_ws(text: str) -> str:
    """``text`` without leading whitespace and closing/opening fence markers."""
    out = text.lstrip(_WS)
    while out.startswith(_FENCE):
        out = out[_skip_opening_fence(out, 0):]
    return out


def _tail_is_json(tail: str) -> bool:
    """Does the remainder after the first value itself parse as JSON?"""
    rest = _strip_fences_and_ws(tail)
    if not rest:
        return False
    if rest[0] in "{[":
        try:
            _DECODER.raw_decode(rest)
        except json.JSONDecodeError:
            return False
        return True
    try:
        json.loads(rest.rstrip(_WS + "`"))
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def _tail_kind(tail: str) -> str:
    stripped = tail.strip(_WS)
    if not stripped:
        return "whitespace"
    has_fence = stripped.startswith(_FENCE)
    rest = _strip_fences_and_ws(stripped)
    if not rest:
        return "fence"
    return "fence+prose" if has_fence else "prose"


def parse_json_reply(
    content: str | None, *, provider: str, model: str | None = None
) -> Any:
    """The first JSON value of ``content``; see the module docstring for the rules.

    Raises:
        LLMResponseParseError: no JSON value at the start, broken JSON, or a
            second JSON value after the first. ``exc.doc`` is the reply text.
    """
    text = content if isinstance(content, str) else ("" if content is None else str(content))
    i = 0
    while i < len(text) and text[i] in _WS:
        i += 1
    i = _skip_opening_fence(text, i)
    if i >= len(text):
        raise LLMResponseParseError(
            f"{provider} reply holds no JSON value", text, min(i, len(text))
        )
    try:
        value, end = _DECODER.raw_decode(text, i)
    except json.JSONDecodeError as exc:
        raise LLMResponseParseError(
            f"{provider} reply is not valid JSON ({exc.msg})", text, exc.pos
        ) from None
    tail = text[end:]
    if not tail.strip(_WS):
        return value
    if _tail_is_json(tail):
        raise LLMResponseParseError(
            f"{provider} reply holds a second JSON value after the first", text, end
        )
    kind = _tail_kind(tail)
    if kind not in ("whitespace", "fence"):
        # PII boundary: length and kind only — the text goes to the debug log.
        logger.info(
            "LLM_JSON_TAIL_DISCARDED provider=%s model=%s tail_chars=%d tail_kind=%s",
            provider, model, len(tail.strip(_WS)), kind,
        )
        note_discarded_tail(tail)
    return value
