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
* A reply with no JSON value at all (empty, prose only, broken JSON) raises
  :class:`~applire.exceptions.LLMResponseParseError` too.
* **Leading prose is skipped** (founder ruling B8-15, 2026-11-13; ADR-009
  amended 2026-11-15): a reply that opens with prose ("We need …", "Item 0: …")
  is read for the ONE JSON object (or array of objects) that follows, without a
  second call. A candidate is an object ``{…}`` or an array opening ``[{``; a
  bracket inside the prose that is not one (``[0]``, ``{placeholder}``) is
  prose. Two or more candidates raise (``second_value``: no guessing which is
  the answer), and so does a candidate that opens like JSON (``{"``) but does
  not decode (a broken object is never read for an object nested inside it).
  MD-Q7 F6 (adversarial adv-1b, 2026-11-15): the one candidate must also be
  the LAST thing the reply says (only whitespace or a closing fence after it)
  and have the caller's expected shape (``json_schema``'s type and required
  keys; without a schema a non-empty object or a non-empty list of objects).
  A quoted or example object followed by more prose, ``{}`` or ``[]`` after
  prose, or a schema mismatch raise into the caller's existing retry. A reply
  that opens with a list of non-objects followed by prose ("[1] Item one …")
  is prose, and the object after it is the candidate.
  The skipped prose is named by length only on an always-on INFO line
  (``LLM_JSON_PROSE_SKIPPED``); its text reaches only the debug log
  (:func:`take_discarded_prose`), like a discarded tail.

The error is also a ``json.JSONDecodeError`` (``doc`` = the reply text), so the
existing ``except json.JSONDecodeError`` handlers around ``aparse_json`` keep
catching it, and the debug log can record the raw reply from ``exc.doc``.
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from typing import Any

from applire.exceptions import LLMResponseParseError

logger = logging.getLogger(__name__)

_DECODER = json.JSONDecoder()
_FENCE = "```"
_WS = " \t\r\n"

_last_tail: ContextVar[str | None] = ContextVar("llm_last_discarded_tail", default=None)
_last_prose: ContextVar[str | None] = ContextVar("llm_last_discarded_prose", default=None)

# B8-15: where a JSON answer can open inside a prose reply — an object, or an
# array whose first element is an object. ``_JSONISH`` is the stricter shape of
# an object that has begun (`{"key`): one of those that does not decode is a
# broken answer, never prose to step over.
_CANDIDATE = re.compile(r"\{|\[\s*\{")
_JSONISH = re.compile(r"(?:\{|\[\s*\{)\s*\"")


def note_discarded_tail(tail: str | None) -> None:
    """Hand the discarded tail of the current call to the debug log."""
    _last_tail.set(tail)


def take_discarded_tail() -> str | None:
    """The tail the current call discarded, once (the debug log reads it)."""
    tail = _last_tail.get()
    _last_tail.set(None)
    return tail


def note_discarded_prose(prose: str | None) -> None:
    """Hand the skipped leading prose of the current call to the debug log."""
    _last_prose.set(prose)


def take_discarded_prose() -> str | None:
    """The leading prose the current call skipped, once (the debug log reads it)."""
    prose = _last_prose.get()
    _last_prose.set(None)
    return prose


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


def _value_after_prose(text: str, i: int, *, provider: str) -> tuple[Any, int, int]:
    """B8-15: the ONE JSON object/array of objects after leading prose.

    Returns ``(value, start, end)``. Raises when there is none, when there are
    two or more (``second_value``), or when a JSON-shaped candidate is broken.
    """
    found: list[tuple[Any, int, int]] = []
    pos = i
    while True:
        m = _CANDIDATE.search(text, pos)
        if m is None:
            break
        p = m.start()
        try:
            value, end = _DECODER.raw_decode(text, p)
        except json.JSONDecodeError as exc:
            if _JSONISH.match(text, p):
                # An answer that began and broke: no object inside it is read.
                raise LLMResponseParseError(
                    f"{provider} reply is not valid JSON ({exc.msg})", text, exc.pos
                ) from None
            pos = p + 1  # `{placeholder}`, `{2}` — prose
            continue
        if isinstance(value, (dict, list)):
            found.append((value, p, end))
            if len(found) > 1:
                raise LLMResponseParseError(
                    f"{provider} reply holds a second JSON value after the first",
                    text, p, second_value=True,
                )
            pos = end
        else:  # pragma: no cover — the pattern only opens objects/arrays
            pos = p + 1
    if not found:
        raise LLMResponseParseError(f"{provider} reply holds no JSON value", text, i)
    value, start, end = found[0]
    # MD-Q7 F6 (adversarial adv-1b): a value inside prose is only the answer
    # when it is the LAST thing the reply says. A candidate followed by more
    # prose is a quoted or example object (a refusal quoting a posting's
    # `{"verdict": "supported"}`), never the reply.
    if _tail_kind(text[end:]) not in ("whitespace", "fence"):
        raise LLMResponseParseError(
            f"{provider} reply holds prose after the JSON value it opened with prose",
            text, end,
        )
    return value, start, end


def _matches_expected_shape(value: Any, json_schema: dict | None) -> bool:
    """MD-Q7 F6 — the shape a prose-first candidate must have to be read.

    With the caller's ``json_schema`` (the OpenAI-style block, ``schema`` inside):
    its top-level ``type`` and every ``required`` key. Without one: a non-empty
    object, or a non-empty list of objects — ``{}`` or ``[]`` after prose is
    never an answer. A FACT about the value, no reading of its content.
    """
    schema = json_schema.get("schema") if isinstance(json_schema, dict) else None
    if isinstance(schema, dict):
        kind = schema.get("type")
        if kind == "object" and not isinstance(value, dict):
            return False
        if kind == "array" and not isinstance(value, list):
            return False
        required = schema.get("required")
        if isinstance(value, dict) and isinstance(required, list):
            if not all(isinstance(k, str) and k in value for k in required):
                return False
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return bool(value) and all(isinstance(v, dict) and v for v in value)
    return False


def parse_json_reply(
    content: str | None,
    *,
    provider: str,
    model: str | None = None,
    json_schema: dict | None = None,
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
    prose_first = text[i] not in "{["
    if not prose_first:
        try:
            value, end = _DECODER.raw_decode(text, i)
        except json.JSONDecodeError as exc:
            raise LLMResponseParseError(
                f"{provider} reply is not valid JSON ({exc.msg})", text, exc.pos
            ) from None
        # MD-Q7 F6 (list case, predates B8-15): "[1] Item one … {...}" opens
        # with a numbered-list marker that decodes as the array [1]. A list of
        # non-objects followed by prose is prose, not the answer.
        if (
            isinstance(value, list)
            and not all(isinstance(v, dict) for v in value)
            and _tail_kind(text[end:]) not in ("whitespace", "fence")
        ):
            prose_first = True
    if prose_first:
        value, start, end = _value_after_prose(text, i, provider=provider)
        if not _matches_expected_shape(value, json_schema):
            raise LLMResponseParseError(
                f"{provider} reply's JSON after prose does not have the expected shape",
                text, start,
            )
        logger.info(
            "LLM_JSON_PROSE_SKIPPED provider=%s model=%s prose_chars=%d",
            provider, model, len(text[i:start].strip(_WS)),
        )
        note_discarded_prose(text[i:start])
    tail = text[end:]
    if not tail.strip(_WS):
        return value
    if _tail_is_json(tail):
        raise LLMResponseParseError(
            f"{provider} reply holds a second JSON value after the first", text, end,
            second_value=True,
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
