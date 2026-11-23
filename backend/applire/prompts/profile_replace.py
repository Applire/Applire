# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The bullet-replace loop's two prompts (Sorbet invite-gate WP-V; rulings Q9-1 …
Q9-7, GARH): the AUTHOR writes one bullet from two, the REVIEWER confirms it
with quotes or hands it back with a finding.

Measured basis: ``Documents/Runs/Sorbet/quality-1/q9/report.md`` (115 captured
restatement calls ×3 on Luna). The constrained author (q9 arm b2v2) is the one
that copied no answer text into a bullet (0 of 77 executions, against 21–26 for
an author that saw the answer as material); it is kept here almost word for
word. Two changes the q9 holes asked for:

* the author never sees the person's ANSWER: its only material is the two
  bullets (hole 1: "the author's rewrite is an unchecked second writer"); what
  it adds is then checked in code against the two bullets
  (``services/profile/replace_loop.check_proposal``);
* the reviewer has two exits, ``confirm`` or ``revise`` (founder ruling Q9-7:
  "I rejected this because of …"); a confirm must quote, for every part of
  BOTH bullets, the words of the proposal that state it — a translation or a
  synonym may be quoted (the q9 evidence rule that asked Katrin's obvious yes
  14 times: the quote is the PROPOSAL's, never the answer's).

Measured (WP-V, Luna, 3 replicates of the 38 q9 candidates + 18 adversarial pairs, read
by a blind Sonnet reader): before the verb sentence 2 of 75 distinct proposals carried an
upgraded verb ("Zuständigkeit für" for "covering", "Erstellung von" for "for"); the
sentence in both prompts is the fix, re-measured in ``v/report.md``.

The author may withdraw (``{"new": null}``) when the two bullets are about
different facts: Q9-7's "the author withdraws after the finding"; both bullets
then stay, as before this loop existed.
"""
from __future__ import annotations

import json

REPLACE_AUTHOR_SYSTEM = """\
You write ONE bullet of a person's professional profile from TWO bullets of the same
profile entry. HELD is in the profile already. NEW was just written from the person's
interview answer and states the same activity with more detail. Write the ONE bullet that
can stand in place of both:
- It states EVERY part of HELD with the same meaning: same figures, each on the same object
  or people, same subject, same negations, same kind of leadership. Keep HELD's own words
  where you can.
- It states every fact of NEW. It adds NOTHING that neither bullet states: no new figure,
  no new name, no sentence about what the person did not do, has not done yet, wants or
  plans to do, and no "I"/"ich".
- Keep each verb as strong as the bullet it comes from: "covering", "for", "betreut" never
  become "responsible for", "Zuständigkeit", "Erstellung" or "Führung".
- Write it in the language of HELD (translate NEW's facts if NEW is in another language).
- If HELD and NEW are about DIFFERENT facts (another task, another object, another time,
  other people), do not merge them: answer {"new": null, "reason": "<why>"}.
A FINDING, when present, says what was wrong with your previous bullet: fix exactly that.
Respond ONLY with JSON: {"new": "<the one bullet>"} or {"new": null, "reason": "<why>"}
"""

REPLACE_REVIEWER_SYSTEM = """\
You review ONE proposed edit of a person's professional profile. PROPOSAL is meant to replace
two bullets of the same entry: HELD (already in the profile) and NEW (just written from the
person's answer). The edit is right only when PROPOSAL states every part of HELD and every
fact of NEW with the same meaning (same figures, each on the same object or people, same
subject, same negations, same kind of leadership) and adds nothing else. HELD, NEW and
PROPOSAL may be in different languages; a faithful translation or a synonym keeps the meaning.
A stronger verb is something added: "covering" or "for" read as "responsible for",
"Zuständigkeit" or "Erstellung", "Anleitung" read as "Führung", is a revise.

SIGNALS come from a word-level comparison. They are hints, not verdicts: words can differ by
language or wording while the meaning is kept, and every word can be present while the
meaning changed (a number moved to another object, another subject).

Decide one of:
- "confirm": PROPOSAL keeps every part of HELD and every fact of NEW and adds nothing.
  Give "evidence": for EVERY part of HELD and of NEW one item {"part": "<exact substring of
  HELD or of NEW>", "quote": "<exact substring of PROPOSAL that states it>"}. Together the
  parts must cover every word of HELD and every word of NEW.
- "revise": PROPOSAL loses or changes a part of HELD or of NEW, or adds something neither
  states. Give "finding": what the writer must keep, restore or remove, in one sentence.

Respond ONLY with JSON: {"decision": "confirm|revise", "evidence": [...], "finding": "<one sentence>"}
"""


def build_author_prompt(
    held: str, new: str, *, previous: str | None = None, finding: str | None = None
) -> str:
    """HELD and NEW only — never the answer (q9 hole 1). A revision also sees its
    previous bullet and the finding it must fix."""
    lines = [f"HELD: {held}", f"NEW: {new}"]
    if previous is not None:
        lines.append(f"YOUR PREVIOUS BULLET: {previous}")
    if finding:
        lines.append(f"FINDING: {finding}")
    return "\n".join(lines) + "\n"


def build_reviewer_prompt(held: str, new: str, proposal: str, signals: dict) -> str:
    return (
        f"HELD: {held}\nNEW: {new}\nPROPOSAL: {proposal}\n"
        f"SIGNALS: {json.dumps(signals, ensure_ascii=False)}\n"
    )
