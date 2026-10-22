# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The import restatement check (ADR-046 + ADR-063 amended 2026-10-22, D-B3V6;
founder ruling V-3 = A).

After a second source is merged onto a profile, one call asks which NEW bullets
for an existing entry state no fact its HELD bullets do not already state. A
judgement — the model's (ADR-062 clause 1); code only acts on the indexes the
model names (``services/profile/reconcile/restatement.py``).
"""
from __future__ import annotations

RESTATEMENT_SYSTEM_PROMPT = """\
You check bullet points of one person's professional profile. Each ENTRY (a position,
project or volunteer role) lists HELD bullets already in the profile and NEW bullets that
another document of the same person adds to that entry.

For every NEW bullet decide one thing: does it state at least one fact that NONE of the
HELD bullets of the same entry states?

A fact is any of: a number or figure (team size, budget, percentage, count, money); a scope
(sites, machines, shifts, areas, products, customers); a named tool, system, method,
standard or certificate; an object or area of responsibility; an action the person took;
a result or outcome; a date or duration.

- If the NEW bullet states at least one fact that is not in the HELD bullets, it is NEW.
  Example: HELD "Leitung von 38 Mitarbeitenden" / NEW "Leitung von 38 Mitarbeitenden und
  Budget von 6 Mio. €" — NEW (the budget is a fact the held bullet does not state).
- Only if EVERY fact of the NEW bullet is already stated by the HELD bullets (in any
  wording or language, shortened, abbreviated or expanded, split across several held
  bullets) does it RESTATE. Example: HELD "Einführung von Shopfloor-Management und
  KVP-Routinen" / NEW "Shopfloor-Management und KVP eingeführt." — RESTATES.
- Wording, tone, language, word order, length and abbreviations are never facts.
- A more general NEW bullet whose content a held bullet states more specifically RESTATES.
- When you are not sure, it is NEW.

Respond ONLY with JSON:
{"restated": [{"new": "<NEW bullet id, e.g. e1.n2>", "held": "<HELD bullet id that states it most completely, e.g. e1.h1>"}]}
List only the NEW bullets that restate. An empty list when none does.
"""


def build_restatement_prompt(entries: list[dict]) -> str:
    """``entries``: ``[{"label": str, "held": [str], "new": [str]}]`` in order;
    ids are ``e<i>.h<j>`` / ``e<i>.n<k>`` (1-based)."""
    lines: list[str] = []
    for i, entry in enumerate(entries, start=1):
        lines.append(f"ENTRY e{i}: {entry['label']}")
        lines.append("HELD:")
        for j, text in enumerate(entry["held"], start=1):
            lines.append(f"  e{i}.h{j}: {text}")
        lines.append("NEW:")
        for k, text in enumerate(entry["new"], start=1):
            lines.append(f"  e{i}.n{k}: {text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
