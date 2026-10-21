# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US367 (E068 / ADR-094 cl. 6) — short entity ids in model-facing payloads.

The weak tier failed 9 of 42 E066 bench runs on one defect: the CV writer echoed a
36-character UUID with one character dropped, and ``assemble_tailored_cv`` failed
closed on "unknown work-entry id" (ADR-067 cl. 3 — correctly). The model is asked to
copy an opaque string it cannot read; a short id it can (``w1``, ``p2``, ``e1``).

Two parts, both deterministic (ADR-062 cl. 1 — a lookup, never a judgement):

* :class:`ShortIdMap` — one bidirectional map per generation. Seeded from the vault in
  section order, so the same vault always yields the same short ids (``w1`` is the
  first work entry); any other UUID met in a prompt gets an ``x<n>`` id on the fly.
* :class:`ShortIdProvider` — an ``LLMProvider`` wrapper installed for ONE chain: every
  UUID in the prompt and the system text is replaced by its short id before the call,
  and every JSON string value that is exactly a short id this map issued is replaced
  by its UUID in the response. So nothing downstream of the provider changes: the
  pipeline keeps working in vault UUIDs, and the model never sees one.

A short id the model invents (``w9`` when there are three roles) is NOT mapped — it
reaches ``assemble_tailored_cv`` as the unknown id it is and still fails closed.
Mapping is exact-string: ``"w1"`` as a whole JSON string value, never a substring of
prose, so a bullet that happens to contain "w1" is untouched.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from applire.providers.llm.base import LLMProvider

UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_SHORT_RE = re.compile(r"^[a-z]\d+$")

#: Vault section → short-id prefix. Order is the seeding order.
SECTION_PREFIXES: tuple[tuple[str, str], ...] = (
    ("work_experience", "w"),
    ("projects", "p"),
    ("education", "e"),
    ("languages", "l"),
    ("certifications", "c"),
    ("volunteer_activities", "v"),
    ("volunteering", "v"),
    ("skills", "s"),
    ("signature_stories", "t"),
)
_UNKNOWN_PREFIX = "x"


class ShortIdMap:
    """A per-generation bidirectional UUID ⇄ short-id map.

    ``namespace`` (ADR-078 amended 2026-10-20, 783:L79) prefixes every issued id
    (``id_w1``) for a caller whose prompt already gives the model its OWN short
    handles — the reconciler's local refs are ``w1``/``p1``/``v1`` (prompt rule
    3), so an un-namespaced map would make an existing entity and a new entity
    the same string. Default ``""`` keeps the writers' ids (``w1``) unchanged."""

    def __init__(self, namespace: str = "") -> None:
        self._to_short: dict[str, str] = {}
        self._to_long: dict[str, str] = {}
        self._counters: Counter[str] = Counter()
        self._namespace = namespace
        self._short_re = (
            re.compile(rf"^{re.escape(namespace)}[a-z]\d+$") if namespace else _SHORT_RE
        )

    @classmethod
    def from_profile(cls, profile_json: dict | None, *, namespace: str = "") -> "ShortIdMap":
        m = cls(namespace)
        for section, prefix in SECTION_PREFIXES:
            for entry in (profile_json or {}).get(section) or []:
                if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                    if UUID_RE.fullmatch(entry["id"].strip()):
                        m.short(entry["id"].strip(), prefix=prefix)
        return m

    def __len__(self) -> int:
        return len(self._to_short)

    def short(self, long_id: str, *, prefix: str = _UNKNOWN_PREFIX) -> str:
        key = long_id.lower()
        found = self._to_short.get(key)
        if found is not None:
            return found
        self._counters[prefix] += 1
        sid = f"{self._namespace}{prefix}{self._counters[prefix]}"
        self._to_short[key] = sid
        self._to_long[sid] = long_id
        return sid

    def long(self, short_id: str) -> str | None:
        return self._to_long.get(short_id)

    def shorten_text(self, text: str | None) -> str | None:
        """Every UUID in ``text`` replaced by its short id (new ones get ``x<n>``)."""
        if not text:
            return text
        return UUID_RE.sub(lambda mt: self.short(mt.group(0)), text)

    def restore(self, obj: Any) -> Any:
        """Map every JSON string value (and key) that is exactly an issued short id
        back to its UUID. Anything else — prose, numbers, unknown short ids — is
        returned unchanged."""
        if isinstance(obj, str):
            if self._short_re.match(obj):
                return self._to_long.get(obj, obj)
            return obj
        if isinstance(obj, list):
            return [self.restore(v) for v in obj]
        if isinstance(obj, dict):
            return {self.restore(k) if isinstance(k, str) else k: self.restore(v) for k, v in obj.items()}
        return obj

    def restore_json_text(self, text: str) -> str:
        """``restore`` for a raw-text response: parse it when it is JSON; otherwise
        replace only quoted whole-string short ids (``"w1"``)."""
        try:
            return json.dumps(self.restore(json.loads(text)), ensure_ascii=False)
        except (TypeError, ValueError):
            return re.sub(
                rf'"({re.escape(self._namespace)}[a-z]\d+)"',
                lambda mt: f'"{self._to_long[mt.group(1)]}"' if mt.group(1) in self._to_long else mt.group(0),
                text,
            )


class ShortIdProvider(LLMProvider):
    """Wrap a provider so the model sees short ids and the caller sees UUIDs."""

    def __init__(self, inner: LLMProvider, id_map: ShortIdMap) -> None:
        super().__init__(timeout=getattr(inner, "_timeout", 30))
        self._inner = inner
        self.id_map = id_map

    @property
    def _model(self) -> Any:  # type: ignore[override]
        return getattr(self._inner, "_model", None)

    def __getattr__(self, name: str) -> Any:
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    def _shorten_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if isinstance(kwargs.get("system"), str):
            kwargs = {**kwargs, "system": self.id_map.shorten_text(kwargs["system"])}
        return kwargs

    async def acomplete(self, prompt: str, **kwargs: Any) -> str:
        out = await self._inner.acomplete(
            self.id_map.shorten_text(prompt), **self._shorten_kwargs(kwargs)
        )
        return self.id_map.restore_json_text(out) if isinstance(out, str) else out

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        out = await self._inner.aparse_json(
            self.id_map.shorten_text(prompt), **self._shorten_kwargs(kwargs)
        )
        return self.id_map.restore(out)
