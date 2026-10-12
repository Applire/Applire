# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (scorers) — the judge client.

A deliberately small client for the four judge prompts: OpenRouter
chat-completions over ``httpx``, temperature 0, JSON out.

Configuration is **process environment only** and fails loudly:

* ``JUDGE_MODEL`` — the OpenRouter model id of the judge (no default: a silent
  default would let a bench number be quoted without its instrument). The spec
  names Claude Sonnet 5.5 (a different family than the systems under test);
  the exact OpenRouter id must be verified against ``GET /api/v1/models`` when
  the first paid run is approved.
* ``OPENROUTER_API_KEY`` — read from ``os.environ`` only, never from a file.
* ``QB_JUDGE_CACHE`` (optional) — cache directory, default
  ``./.quality-bench-judge-cache``.

**Disk cache.** Every successful call is stored under
``sha256(model + system + user)``; re-scoring an unchanged run (or re-running
the report) is free and byte-identical. A malformed reply is never cached.

**Retries.** One retry on malformed JSON (the retry sends the broken reply back
with a one-line instruction); transient HTTP errors (429/5xx/timeouts) get up to
three attempts with backoff. Anything else raises :class:`JudgeError`.

:class:`FakeJudge` is the hermetic double: canned JSON per class, no network.
This module never calls an endpoint at import time and is never exercised
against the real endpoint by the test-suite.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_CACHE_DIR = ".quality-bench-judge-cache"
DEFAULT_MAX_TOKENS = 8000


class JudgeError(RuntimeError):
    """The judge could not produce a parseable answer (or is misconfigured)."""


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 characters per token; German is a little denser)."""
    return max(1, round(len(text) / 3.8))


def cache_key(model: str, system: str, user: str) -> str:
    return hashlib.sha256(f"{model}\0{system}\0{user}".encode("utf-8")).hexdigest()


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a judge reply into a JSON object; tolerate a markdown fence or
    prose around the object. Raises ``ValueError`` when no object is found."""
    t = _FENCE.sub("", (text or "").strip())
    try:
        out = json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object in judge reply")
        out = json.loads(t[start : end + 1])
    if not isinstance(out, dict):
        raise ValueError("judge reply is not a JSON object")
    return out


class _Stats:
    def __init__(self) -> None:
        self.calls = 0
        self.cache_hits = 0
        self.retries = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.by_class: dict[str, dict[str, int]] = {}

    def add(self, cls: str, prompt: int, completion: int, *, cached: bool) -> None:
        bucket = self.by_class.setdefault(cls or "?", {"calls": 0, "cache_hits": 0, "prompt_tokens": 0, "completion_tokens": 0})
        bucket["calls"] += 1
        if cached:
            self.cache_hits += 1
            bucket["cache_hits"] += 1
        self.calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        bucket["prompt_tokens"] += prompt
        bucket["completion_tokens"] += completion

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "retries": self.retries,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "by_class": self.by_class,
        }


class JudgeClient:
    """OpenRouter judge. ``complete_json(system, user, cls=...)`` -> dict."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        *,
        cache_dir: str | Path | None = None,
        base_url: str = DEFAULT_BASE_URL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = 180.0,
        http: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model or os.environ.get("JUDGE_MODEL", "")
        if not self.model:
            raise JudgeError("JUDGE_MODEL is not set (OpenRouter model id of the judge; no default by design)")
        self.api_key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key and http is None:
            raise JudgeError("OPENROUTER_API_KEY is not set in the process environment")
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._http = http
        self._sleep = sleep
        self.cache_dir = Path(cache_dir or os.environ.get("QB_JUDGE_CACHE") or DEFAULT_CACHE_DIR)
        self.stats = _Stats()

    # -- cache ---------------------------------------------------------------
    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        path = self._cache_path(key)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _cache_put(self, key: str, payload: dict[str, Any]) -> None:
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    # -- transport -----------------------------------------------------------
    def _post(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        import httpx

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        client = self._http or httpx.Client(timeout=self.timeout)
        last: Exception | None = None
        for attempt in range(3):
            try:
                resp = client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
                if resp.status_code in (429, 500, 502, 503, 504):
                    last = JudgeError(f"HTTP {resp.status_code}")
                    self.stats.retries += 1
                    self._sleep(2.0 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    raise JudgeError(f"judge HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                text = data["choices"][0]["message"]["content"] or ""
                return text, data.get("usage") or {}
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                self.stats.retries += 1
                self._sleep(2.0 * (attempt + 1))
            except (KeyError, IndexError, ValueError) as exc:
                raise JudgeError(f"unexpected judge response shape: {exc}") from exc
        raise JudgeError(f"judge unreachable after 3 attempts: {last}")

    # -- public --------------------------------------------------------------
    def complete_json(self, system: str, user: str, *, cls: str = "") -> dict[str, Any]:
        key = cache_key(self.model, system, user)
        hit = self._cache_get(key)
        if hit is not None and isinstance(hit.get("parsed"), dict):
            usage = hit.get("usage") or {}
            self.stats.add(cls, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0), cached=True)
            return hit["parsed"]

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        text, usage = self._post(messages)
        try:
            parsed = parse_json_object(text)
        except (ValueError, json.JSONDecodeError):
            self.stats.retries += 1
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "That was not a valid JSON object. Reply again with the JSON object only."},
            ]
            text2, usage2 = self._post(messages)
            try:
                parsed = parse_json_object(text2)
            except (ValueError, json.JSONDecodeError) as exc:
                raise JudgeError(f"judge returned malformed JSON twice (class {cls or '?'})") from exc
            usage = {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0) + int(usage2.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0) + int(usage2.get("completion_tokens") or 0),
            }
            text = text2
        self._cache_put(key, {"model": self.model, "cls": cls, "response_text": text, "parsed": parsed, "usage": usage})
        self.stats.add(cls, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0), cached=False)
        return parsed


_EMPTY: dict[str, dict[str, Any]] = {
    "L": {"issues": [], "exempt": []},
    "U": {"removed": [], "omitted": [], "understated": []},
    "D": {"groups": []},
    "S": {"issues": []},
}


class FakeJudge:
    """Hermetic judge double: canned JSON per class, nothing leaves the process.

    ``canned[cls]`` is a dict, or a callable ``(system, user) -> dict`` for
    prompts whose answer depends on the input (e.g. echoing the removal ids).
    A class with no entry answers the valid empty shape. Every call is kept in
    ``calls`` as ``(cls, system, user)`` so tests can assert what the judge saw
    — and ``stats`` mirrors :class:`JudgeClient` so the instrument block of a
    score file looks the same.
    """

    model = "fake-judge"

    def __init__(self, canned: dict[str, Any] | None = None) -> None:
        self.canned = dict(canned or {})
        self.calls: list[tuple[str, str, str]] = []
        self.stats = _Stats()

    def complete_json(self, system: str, user: str, *, cls: str = "") -> dict[str, Any]:
        self.calls.append((cls, system, user))
        self.stats.add(cls, estimate_tokens(system + user), 0, cached=False)
        ans = self.canned.get(cls)
        if callable(ans):
            ans = ans(system, user)
        if ans is None:
            ans = _EMPTY.get(cls, {})
        return json.loads(json.dumps(ans))
