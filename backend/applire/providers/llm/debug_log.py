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

"""Developer-only full-fidelity logging of every LLM interaction.

Gated by ``settings.llm_debug_log`` (default ``False`` → OFF in production). When
enabled, the provider factory wraps the chosen provider in :class:`_LoggingProvider`
so that every ``acomplete`` / ``aparse_json`` call appends ONE JSON line to
``<llm_debug_log_dir>/<YYYY-MM-DD>.jsonl`` capturing the system prompt, user prompt,
call parameters, the raw response, latency, and any error.

This is a debugging aid for *us* — it records full prompt/response content, which
includes CV PII, so it must stay OFF in production. We don't need this depth there;
locally it lets us read exactly what each chain (extraction, reviewer, reconcile…)
sent and received.

Stage labelling: callers may wrap a section with :func:`llm_log_stage` (or call
:func:`set_stage`) so each record is attributable to a pipeline stage. Unlabelled
calls still log their full content with ``stage = null``.

Review-loop observability (#264, ADR-021 amended): ``stage`` alone cannot tell a
reviewer-verdict call apart from a corrector-retry call within the SAME
``review_and_refine`` chain, nor which attempt it was — reconstructing that
previously required matching on the system prompt's first line. :func:`set_review_call_meta`
labels the next call(s) with their ``role`` ("reviewer"/"generator") and 1-based
``attempt`` number as extra structured fields on the debug-log record. Separately,
:func:`log_review_verdict` / :func:`log_review_exhausted` / :func:`log_review_call_failed`
emit stable, PII-free, always-on structured lines via the standard ``applire.llm.review``
logger — unlike the full-fidelity JSONL (dev-only, gated behind ``settings.llm_debug_log``
because it carries prompt/response content), these carry only counts/booleans/chain-ids,
so they stay on in production and are the durable signal an exhausted review can be
counted from after the fact.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator

from applire.config import settings
from applire.providers.llm.base import LLMProvider

# Per-task pipeline-stage label (e.g. "cv_extraction", "reconcile"). ContextVars are
# copied per asyncio task, so concurrent requests never clobber each other's stage.
_stage: ContextVar[str] = ContextVar("llm_log_stage", default="")

# Per-task review-loop position (#264): which call ROLE within a review_and_refine
# chain ("reviewer" | "generator") and which 1-based ATTEMPT. None/None outside a loop.
_review_role: ContextVar[str | None] = ContextVar("llm_review_role", default=None)
_review_attempt: ContextVar[int | None] = ContextVar("llm_review_attempt", default=None)

# Standard, always-on logger for review-loop observability (#264) — distinct from the
# dev-only PII-bearing JSONL: these records carry only counts/booleans/chain-ids.
_review_logger = logging.getLogger("applire.llm.review")

# Serialises the (rare, dev-only) file appends so large prompt lines never interleave.
_write_lock = threading.Lock()

# Guard against pathologically large single fields blowing up the log file.
_MAX_FIELD_CHARS = 200_000


def set_stage(label: str) -> None:
    """Set the current pipeline-stage label for subsequent LLM calls in this task."""
    _stage.set(label)


def set_review_call_meta(role: str | None, attempt: int | None) -> None:
    """Label the NEXT LLM call(s) in this task with their review-loop ``role``
    ("reviewer" or "generator") and 1-based ``attempt`` number (#264).

    Recorded as the ``review_role`` / ``review_attempt`` fields on the debug-log
    record (a no-op when the debug log is disabled) alongside the existing
    ``stage`` label — ``stage`` says WHICH chain a call belongs to; this says WHERE
    in the loop. Call with ``(None, None)`` once the loop finishes so a later,
    unrelated call in the same task doesn't inherit a stale label.
    """
    _review_role.set(role)
    _review_attempt.set(attempt)


def log_review_verdict(
    chain_id: str, attempt: int, max_retries: int, *, approved: bool, issues_count: int
) -> None:
    """Structured, PII-free line for one reviewer verdict within a review loop (#264).

    Emitted via the standard (always-on) logger, unlike the full-fidelity debug-log
    JSONL — no prompt/response/issue text, so it is safe to leave on in production
    and is the durable signal review rounds can be counted/measured from."""
    _review_logger.info(
        "REVIEW_VERDICT chain=%s attempt=%d/%d approved=%s issues=%d",
        chain_id, attempt, max_retries, approved, issues_count,
    )


def log_review_exhausted(chain_id: str, max_retries: int, issues_count: int) -> None:
    """A review loop ran out of retries and is shipping its last, unapproved draft.

    Stable ``REVIEW_EXHAUSTED`` prefix + chain id: grep this to count exhaustion
    after the fact (#264) — this is the "silently ships" case made visible."""
    _review_logger.warning(
        "REVIEW_EXHAUSTED chain=%s max_retries=%d issues=%d — shipping last draft unreviewed",
        chain_id, max_retries, issues_count,
    )


def log_review_cycle_detected(chain_id: str, attempt: int, max_retries: int) -> None:
    """A review loop's generator retry reproduced a draft already seen earlier in the
    SAME loop (#272 wave-6 oscillation fix) — a cycle by definition, since a repeated
    draft cannot converge on further rounds. Stopped early rather than burning the
    remaining retries.

    Deliberately a DIFFERENT stable prefix from ``REVIEW_EXHAUSTED``: exhaustion means
    the loop used up every retry without approval; a cycle-stop means the loop noticed
    it was going in circles and quit early. Conflating the two would hide that a
    document shipped via early-stop, not via the ordinary retry budget — this keeps
    both countable, distinctly, after the fact (mirrors #264)."""
    _review_logger.warning(
        "REVIEW_CYCLE_DETECTED chain=%s attempt=%d/%d — generator retry reproduced an "
        "earlier draft in this loop; stopping early instead of burning remaining "
        "retries",
        chain_id, attempt, max_retries,
    )


def log_review_call_failed(chain_id: str, role: str, attempt: int, error_type: str) -> None:
    """A reviewer or refiner call itself failed (truncated/timed out) mid-loop —
    distinct from exhaustion: the loop could not even complete this attempt (#264)."""
    _review_logger.warning(
        "REVIEW_CALL_FAILED chain=%s role=%s attempt=%d error=%s — shipping current draft",
        chain_id, role, attempt, error_type,
    )


def log_review_precision(chain_id: str, attempt: int, *, raised: int, survived: int) -> None:
    """#306 (a): how many of the issues a reviewer round RAISED survive the
    deterministic sanity check (``services/review_issue_filter.py``) before
    any of them is spent as a retry.

    Charter run #7, case 2 pinned the failure this makes visible: the
    cover-letter reviewer's LAST round raised 11 issues, most self-refuting
    or self-annotated non-blocking — the retry budget was gone before the two
    genuine ones got a fix attempt. Stable ``REVIEW_PRECISION`` prefix, always
    on, PII-free (counts only) — so a chain's reviewer precision degrading
    over time is visible without re-reading the (dev-only) debug log."""
    _review_logger.info(
        "REVIEW_PRECISION chain=%s attempt=%d raised=%d survived=%d discarded=%d",
        chain_id, attempt, raised, survived, raised - survived,
    )


def log_review_issue_batch_all_discarded(chain_id: str, attempt: int, raised: int) -> None:
    """#306 (a): every issue a reviewer round raised failed the deterministic
    sanity check — the round is treated as approved (the loop does NOT spend
    a retry regenerating a draft to satisfy noise). Distinct, stable prefix
    (``REVIEW_ISSUES_ALL_DISCARDED``) so this degrades visibly rather than
    silently looking identical to a normal approval."""
    _review_logger.warning(
        "REVIEW_ISSUES_ALL_DISCARDED chain=%s attempt=%d raised=%d — every issue this "
        "round failed the deterministic sanity check (self-refuting, wrong count, or "
        "self-annotated non-blocking); treating as approved rather than spending a "
        "retry on noise",
        chain_id, attempt, raised,
    )


def log_review_substitution_diff(
    chain_id: str, *, retained: list[str], lost: list[str], gained: list[str]
) -> None:
    """#306 (b): a retain_if/prefer_if substitution just happened — log WHAT
    load-bearing evidence (see ``services/load_bearing.py``) moved, not just
    that a substitution occurred.

    Charter run #7, case 2's substitution log line said only that a swap
    happened; the causal chain to the 3 lost figures was only reconstructible
    from the drafted-vs-delivered log DIFF, done by hand, after the fact. This
    line makes that diff a first-class, always-on, PII-free (figures are
    canonical kind:value tokens, never surrounding prose) signal. Stable
    ``REVIEW_SUBSTITUTION_DIFF`` prefix."""
    _review_logger.warning(
        "REVIEW_SUBSTITUTION_DIFF chain=%s retained=%r lost=%r gained=%r",
        chain_id, sorted(retained), sorted(lost), sorted(gained),
    )


def log_review_substitution_refused(chain_id: str, tier: str, would_lose: list[str]) -> None:
    """#306 (b): a candidate draft satisfied the structural predicate(s) but
    was STRICTLY evidence-poorer than the settled draft — the substitution
    was refused rather than trading load-bearing figures away for a cleaner
    structural shape (the settled draft's cosmetic complaint ships instead).
    Stable ``REVIEW_SUBSTITUTION_REFUSED`` prefix."""
    _review_logger.warning(
        "REVIEW_SUBSTITUTION_REFUSED chain=%s tier=%s would_lose=%r — candidate draft "
        "satisfied the structural predicate(s) but is evidence-poorer than the settled "
        "draft; refusing this substitution (#306 — evidence beats a clean predicate match)",
        chain_id, tier, sorted(would_lose),
    )


def log_letter_over_budget(chain_id: str, word_count: int, word_budget: int) -> None:
    """#272 wave-6 follow-up (charter run #6, Task 3): the letter that ships is still
    over the region's letter_body_word_budget norm after the bounded condense pass —
    e.g. because the closing-paragraph retention floor (retain_if=has_closing_paragraph)
    correctly refused to ship a shorter draft that had lost its closing again. That is
    the honest tradeoff (a proper closing beats an on-budget stub), but it must stay
    COUNTABLE after the fact exactly like REVIEW_EXHAUSTED / REVIEW_CYCLE_DETECTED,
    rather than silently shipping a norms violation.

    Stable ``LETTER_OVER_BUDGET`` prefix + chain id, PII-free (no letter content) —
    safe to leave on in production and grep for after the fact."""
    _review_logger.warning(
        "LETTER_OVER_BUDGET chain=%s word_count=%d word_budget=%d — shipping over the "
        "region's letter norm rather than dropping required positioning content",
        chain_id, word_count, word_budget,
    )


@contextmanager
def llm_log_stage(label: str) -> Iterator[None]:
    """Label every LLM call made within the block (restores the prior label on exit)."""
    token = _stage.set(label)
    try:
        yield
    finally:
        _stage.reset(token)


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_FIELD_CHARS:
        return value[:_MAX_FIELD_CHARS] + f"…<truncated {len(value) - _MAX_FIELD_CHARS} chars>"
    return value


def _record(**fields: Any) -> None:
    directory = settings.llm_debug_log_dir or "logs/llm"
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(
            directory, datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".jsonl"
        )
        line = json.dumps(fields, ensure_ascii=False, default=str)
        with _write_lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:  # noqa: BLE001 — debug logging must never break a request
        pass


class _LoggingProvider(LLMProvider):
    """Transparent provider wrapper that records each call's full input and output."""

    def __init__(self, inner: LLMProvider) -> None:
        super().__init__(timeout=getattr(inner, "_timeout", 30))
        self._inner = inner

    async def acomplete(self, prompt: str, **kwargs: Any) -> str:
        return await self._logged("acomplete", prompt, kwargs, self._inner.acomplete)

    async def aparse_json(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        return await self._logged("aparse_json", prompt, kwargs, self._inner.aparse_json)

    async def _logged(self, method: str, prompt: str, kwargs: dict, fn: Any) -> Any:
        start = time.monotonic()
        result: Any = None
        error: str | None = None
        try:
            result = await fn(prompt, **kwargs)
            return result
        except Exception as exc:  # re-raised in finally's caller; we only observe it
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            _record(
                ts=datetime.now(timezone.utc).isoformat(),
                stage=_stage.get() or None,
                review_role=_review_role.get(),
                review_attempt=_review_attempt.get(),
                provider=type(self._inner).__name__,
                model=getattr(self._inner, "_model", None),
                method=method,
                system=_truncate(kwargs.get("system")),
                prompt=_truncate(prompt),
                params={
                    k: kwargs.get(k)
                    for k in ("max_tokens", "temperature", "disable_thinking")
                    if k in kwargs
                },
                latency_ms=round((time.monotonic() - start) * 1000),
                ok=error is None,
                error=error,
                response=_truncate(result),
            )


def wrap_provider(provider: LLMProvider) -> LLMProvider:
    """Wrap *provider* with debug logging when enabled; otherwise return it unchanged."""
    if not settings.llm_debug_log:
        return provider
    return _LoggingProvider(provider)
