# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The import restatement check (ADR-046 + ADR-063 amended 2026-10-22, D-B3V6;
founder ruling V-3 = A).

After a second source is merged onto a profile, one call asks which NEW bullets
for an existing entry state no fact its HELD bullets do not already state. A
judgement — the model's (ADR-062 clause 1); code only acts on the indexes the
model names (``services/profile/reconcile/restatement.py``).

Measured 2026-10-22 (luna, Requesty, thinking off; labels by a blind labeler):
replay set (the reconcile arms' bullets for records 182 + 71, 118 instances, 18
unique: 17 restated / 1 new) — 108/108 restatements held back, 10/10 new kept.
Held-out synthetic set (22 bullets on the same entries, 9 restated / 13 adding
one fact, n = 3): first text 27/27 held, 36/39 new kept (one bullet — a mentoring
line adding a promotion — held back 3/3); the "repeats AND adds" sentence below
was added for it: 27/27 and 39/39, replay set unchanged. That bullet is now a
development case, not a held-out one.

Second round, same day — a REAL two-source import on this branch (Daniel,
LinkedIn PDF then CV) showed the opposite direction failing: the held bullets
were LinkedIn's terse fragments and the CV's richer bullets ("Own the AWS
deployment and the GitLab CI/CD pipelines for four services" over "CI/CD
ownership for four services"; the p95 bullet adding "through query optimisation
and targeted PostgreSQL indexing") were held back — 3 of 3 false "restated" on
the captured call, 5/5 replays, unchanged by a clarifying rule. The answer
shape changed instead: the model now names, for EVERY new bullet, the facts it
adds (`added`), and only an item with an empty `added` restates (code acts on
the model's own statement; ADR-062 clause 1). Results: captured call 0/5 false
"restated" (now a development case — its GitLab shape is the second example
below); replay set 106/108 restatements held, 10/10 new kept; synthetic set
27/27 and 39/39; held-out real import (Stefan, XING PDF then CV, n = 1): 6/6
new bullets kept, 0 held.
"""
from __future__ import annotations

RESTATEMENT_SYSTEM_PROMPT = """\
You check bullet points of one person's professional profile. Each ENTRY (a position,
project or volunteer role) lists HELD bullets already in the profile and NEW bullets that
another document or answer of the same person adds to that entry. HELD also holds the
entry's aims not reached yet, and the bullets of the position a project belongs to or of
a position's projects: one engagement on the CV.

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
- A NEW bullet that repeats a held bullet AND adds something to it (a clause, a figure, an
  outcome such as a promotion, an award or a result) is NEW: judge the added part, not the
  repeated part.
- Wording, tone, language, word order, length and abbreviations are never facts.
- A more general NEW bullet whose content a held bullet states more specifically RESTATES.
- That a held target or goal is not reached yet is not a fact: a HELD bullet that words
  it as a target already says so.
- When you are not sure, it is NEW.

Respond ONLY with JSON, one item for EVERY NEW bullet:
{"items": [{"new": "<NEW bullet id, e.g. e1.n2>", "added": "<the facts of the NEW bullet that no HELD bullet of the entry states, as short phrases; an empty string when every fact is held>", "held": "<the HELD bullet id that states it most completely, or null>"}]}
"added" lists facts only: a different verb, verb form, word order, language or phrasing for a
fact a held bullet states is not added (HELD "Einführung von KVP-Routinen" / NEW "KVP
eingeführt." — added ""; HELD "CI/CD ownership" / NEW "Own the GitLab CI/CD pipelines" —
added "GitLab").
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
