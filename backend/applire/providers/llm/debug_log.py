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
"""

from __future__ import annotations

import json
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

# Serialises the (rare, dev-only) file appends so large prompt lines never interleave.
_write_lock = threading.Lock()

# Guard against pathologically large single fields blowing up the log file.
_MAX_FIELD_CHARS = 200_000


def set_stage(label: str) -> None:
    """Set the current pipeline-stage label for subsequent LLM calls in this task."""
    _stage.set(label)


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
