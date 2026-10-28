# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-099 cl. 11 (UAT-1, U-9, U-11) — the four small model calls behind the
profile hub's decisions. Each sees ONLY what it judges (the two items, the
candidate's reply, two entries' identity fields) — never the whole profile.

* ``MERGE_SYSTEM``      — "Zusammenführen": one sentence from two bullets.
* ``REPLY_SYSTEM``      — a typed answer to a dispute → outcome | corrected
                          value | ONE follow-up question.
* ``IDENTITY_SYSTEM``   — two entries with identical dates: the same position?
* ``DUPLICATES_SYSTEM`` — merging two entries (U-11): which bullet pairs state
                          the same fact (any language), each with a proposal.

The judgements are the model's (ADR-062 cl. 1); what code checks afterwards is
only fact presence (``services/profile/merge_verifier.py``) and membership of
the offered answer set. Every output is a JSON object, and every system
prompt names "JSON" (an OpenAI-family JSON mode refuses a request whose
messages never say it — HTTP 400, found on the first real run, 2026-10-28).
"""
from __future__ import annotations

import json
from typing import Any

MERGE_SYSTEM = """You combine two CV bullet points about the SAME entry into ONE bullet point.

Rules:
1. Use only the facts the two bullets state. Every number, name, tool, place,
   organisation and product in your sentence must be written in one of them.
   Never add a figure, a scope word ("all", "global", "every") or an outcome
   that neither bullet states.
2. Keep each fact's stance: a goal or target stays a goal ("aims to", "targets"),
   a result stays a result. Keep the tense of the bullets.
3. Write one CV line: no "I", "my" or "we"; the language of the first bullet.
4. If the two bullets cannot be one line without dropping a fact, answer null.

Output ONLY this JSON object: {"merged": "<the one bullet>"} or {"merged": null}"""


def build_merge_prompt(existing: str, incoming: str, entry_label: str | None) -> str:
    return (
        f"Entry: {entry_label or '(unnamed)'}\n"
        f"Bullet 1: {existing}\n"
        f"Bullet 2: {incoming}\n"
        'Answer with {"merged": ...} now.'
    )


REPLY_SYSTEM = """You read a candidate's typed reply to a question about two values in their profile.

The question showed the CURRENT value and a NEW value for one field of one entry
and offered a fixed set of answers (listed below with their meaning). Decide what
the reply says. Exactly ONE of the three fields is non-null:

- "outcome": the offered answer the reply clearly chooses, as its key. "Both
  are right, they are two different things" is keep_both when it is offered.
- "corrected_value": the reply gives a THIRD value for the field instead of
  either one ("it was March 2020", "the team had 8 people"). Copy that value
  EXACTLY as the reply writes it — never reformat, translate or complete it.
- "follow_up": the reply chooses nothing you can name. Ask ONE short question,
  in the reply's language, that names the offered answers in plain words.

The values were shown in this order: CURRENT first, NEW second — "the first"
means CURRENT, "the second" means NEW. A reply that names a date, number or
word matching ONE value chooses that value.
Never guess: when the reply is ambiguous, ask the follow_up.
Output ONLY this JSON object: {"outcome": <key or null>, "corrected_value": <string or null>, "follow_up": <string or null>}"""

_OUTCOME_MEANING = {
    "keep_current": "keep the CURRENT value",
    "take_new": "replace it with the NEW value",
    "keep_both": "keep both — they are two different things",
    "merge": "combine both into one",
}


def build_reply_prompt(
    *,
    entry_label: str | None,
    field: str,
    existing: Any,
    incoming: Any,
    outcomes: list[str],
    reply: str,
) -> str:
    offered = "\n".join(f"- {k}: {_OUTCOME_MEANING.get(k, k)}" for k in outcomes)
    return (
        f"Entry: {entry_label or '(profile)'}\nField: {field}\n"
        f"CURRENT value: {json.dumps(existing, ensure_ascii=False)}\n"
        f"NEW value: {json.dumps(incoming, ensure_ascii=False)}\n"
        f"Offered answers:\n{offered}\n\n"
        f"Candidate's reply: {reply}\n"
        "Answer with the JSON object now."
    )


IDENTITY_SYSTEM = """You compare pairs of entries from ONE person's CV profile. Each pair has the
same start and end month. Decide per pair whether both entries describe the SAME
position — the same job at the same employer, written in two languages or under
two names (a translated title, a short or older employer name, an abbreviation).

- "same": the same position.
- "different": two positions (different employers, or clearly different jobs held
  at the same time).
- "unsure": you cannot tell from what is given.

Output ONLY this JSON object: {"verdicts": [{"pair_id": "<id>", "verdict": "same"|"different"|"unsure"}]}"""


def build_identity_prompt(pairs: list[dict[str, Any]]) -> str:
    return (
        "Pairs:\n"
        + json.dumps(pairs, ensure_ascii=False, indent=2)
        + '\nAnswer with {"verdicts": [...]} now.'
    )


DUPLICATES_SYSTEM = """Two entries of one person's CV profile are being merged into one. You get the
bullets of entry T and of entry S, numbered. Find every pair (one T bullet, one S
bullet) that states the SAME fact — the same piece of work, possibly in another
language or other words. Two different pieces of work that share words are NOT
a pair. For each pair, also propose ONE bullet that keeps EVERY fact of both
(only facts written in the two bullets; same stance and tense; no "I"; one
language — the language of the T bullet, never both side by side), or null
when they cannot be one line without dropping a fact.

Output ONLY this JSON object: {"pairs": [{"t": "T1", "s": "S2", "merged": "<bullet>"|null}]}
(an empty list when no bullet appears in both)"""


def build_duplicates_prompt(target_bullets: list[str], source_bullets: list[str]) -> str:
    t = "\n".join(f"T{i + 1}: {b}" for i, b in enumerate(target_bullets))
    s = "\n".join(f"S{i + 1}: {b}" for i, b in enumerate(source_bullets))
    return f"Entry T bullets:\n{t or '(none)'}\n\nEntry S bullets:\n{s or '(none)'}\nAnswer with the JSON object now."
