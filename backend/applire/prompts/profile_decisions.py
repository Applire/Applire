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


# D-3 regression (Hazelnut build 5, delivery run 6): the judge asked "Ist das
# erreicht?" of a present-progressive line about the candidate's own working
# product, and of a team-building duty. Measured on the logged run-6 calls
# (gpt-6-luna, thinking off, n=5): product line 5/5 -> 0/5, team duty 3/5 ->
# 0/5; true positives (the governance mandates, the 60 %+/70 % targets) kept.
# The removed mandate example "Build up a data platform" primed the false
# positive. Residual: an extraction that rewrites a stated property as an
# imperative ("Build privacy into the platform …") is still flagged.
ASPIRATION_SYSTEM = """You read the bullet points of a person's CURRENT jobs in their CV profile. List
ONLY the bullets that state something NOT YET reached, a result or a thing to
create that the bullet itself leaves open:

- a goal, target or plan: "aiming for over 60% efficiency improvement",
  "the project targets a 20% cut", "plan to roll out", "Ziel: …", "angestrebt";
- a mandate to create or establish something, worded as an imperative or an
  "-ing" intention, that never says it exists: "Establish AI governance
  frameworks", "Aufbau einer Governance", "… while establishing a review
  board".

NEVER listed:
- an ongoing duty or role, even about a project or an area: "Lead a team of 8
  engineers", "Manage a cloud migration project using Azure", "Act as lead
  architect for the payments area", "Set strategic direction for the platform",
  "Build and grow the analytics team through hiring and coaching",
  "Verantwortung für das Budget" — building, growing or developing a TEAM or
  people (hiring, coaching, talent development) is always such a duty;
- building, developing or improving the person's own product, platform, app
  or system, in any form ("-ing", imperative, noun), when this bullet or
  another one says what that thing does, or that it is in use, released or in
  beta: "Building a booking app that matches riders and drivers in 12 cities",
  "Develop the app's backend", "Make the app work offline" — that is the job's
  ongoing work, a duty;
- anything the bullet states as reached, done, built or measured, in any tense:
  "Established …", "The pilot reached a 40% saving", "cut costs by 18%",
  "Set up a CI pipeline";
- plain descriptions of the role or the product.

When unsure, do not list the bullet.

Output ONLY this JSON object: {"items": [<bullet number>, ...]} (an empty list when none)"""


def build_aspiration_prompt(bullets: list[str]) -> str:
    lines = "\n".join(f"{i + 1}: {b}" for i, b in enumerate(bullets))
    return f"Bullets of the current jobs:\n{lines}\nAnswer with the JSON object now."


PRESENT_SYSTEM = """An import of one person's CV did NOT apply some entries. For each NEW entry,
decide whether it is the SAME as one of the EXISTING entries of its section:
the same degree at the same school, the same job, project, certificate, skill
or language, written in another language or under another name (a translated or
older institution name, a renamed or shortened company, a degree or title
written differently). A NEW skill may also be one of the EXISTING
certificates, and a NEW certificate one of the EXISTING skills.

- Name the existing entry's id only when it is clearly the same thing.
- Different degrees at one school, or different jobs at one employer, are NOT
  the same.
- When unsure, answer null.

Output ONLY this JSON object: {"matches": [{"item": "<item id>", "existing": "<existing id>" | null}]}"""


# D-5 — the same judgement for certificates an import has just ADDED: one
# translated or renamed credential must not stand twice in the profile.
CERT_SAME_SYSTEM = """You compare the certificates of one person's profile. An import has just
added the NEW certificates; the EXISTING ones were there before. For each NEW
certificate, decide whether it is the SAME credential as one EXISTING
certificate, written in another language or under another name (a translated
title, a shorter or longer form of the same title).

- Name the existing certificate's id only when it is clearly the same credential.
- Two levels, versions or tracks of one programme are NOT the same (a foundation
  and an advanced level, version 3 and version 4, one role's certificate and
  another role's certificate of the same body).
- When unsure, answer null.

Output ONLY this JSON object: {"matches": [{"item": "<item id>", "existing": "<existing id>" | null}]}"""


def build_present_prompt(items: list[dict[str, Any]], existing: dict[str, list[dict[str, Any]]]) -> str:
    return (
        "EXISTING entries by section:\n"
        + json.dumps(existing, ensure_ascii=False, indent=1)
        + "\n\nNEW entries the import did not apply:\n"
        + json.dumps(items, ensure_ascii=False, indent=1)
        + "\nAnswer with the JSON object now."
    )
