# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The severity contract shared by EVERY reviewer prompt (ADR-021 amended 2026-07-28).

ADR-021 deferred a per-issue severity field on 2026-04-10 ("if future analysis shows
that minor issues are causing unnecessary retries..."). Tiramisu wave 8 is that
analysis. Charter run #7 and a controlled model replay showed the reviewer had no way
to say *"I noticed this, but it is not worth regenerating the document"* — so every
observation it made, however small, arrived as a rejection and cost a rewrite. Two
independent models did it: Mistral Medium filed 11 issues (4 self-refuting) and Sonnet
5 filed 5 issues with 2 explicitly annotated as non-blocking in their own prose. The
gap is therefore in the SCHEMA, not in the model.

The reason this matters is not cost. The wave-6 amendment established that each rewrite
round is a memoryless regeneration that can erode content an earlier round had right —
so a rewrite triggered by a wording preference is a real chance of losing a grounded
fact. **An unnecessary rewrite is a truthfulness risk, not merely a latency one.** That
is why the contract below tells the reviewer to resolve doubt toward "minor".

Two values only. A third ("major", "info") invites triage debates in a field whose only
job is a binary loop decision: does the writer run again?

Every reviewer prompt in this package composes:
  * :data:`SEVERITY_CONTRACT` — the shared, domain-neutral meaning of the two values,
    identical everywhere so the vocabulary cannot drift prompt by prompt (the System
    FMEA's SF-WRITE finding: eight reviewer prompts had each grown their own dialect);
  * :func:`review_output_schema` — the JSON block, so the shape cannot drift either;
  * ONE domain line of its own naming what is blocking IN THAT PASS. That line is the
    per-prompt half of the contract and each prompt owns it.

The loop side lives in ``services/reviewer.py`` (the gate) and
``services/review_issues.py`` (parsing + the measurement checks).
"""
from __future__ import annotations

SEVERITY_BLOCKING = "blocking"
SEVERITY_MINOR = "minor"

SEVERITY_CONTRACT = """\
SEVERITY — every issue you raise carries one, and it alone decides whether this
document is regenerated:

- "blocking": as it stands, the draft would put something UNTRUE, UNSUPPORTED, or
  MISATTRIBUTED in front of a reader, or it omits something the source explicitly
  required. Only this severity is worth a rewrite.
- "minor": the draft is truthful and complete, but you would have written it
  differently — wording, repetition, ordering, tone, emphasis, length, polish. Record
  it so it stays visible. It is NOT worth regenerating the document for.

Rules, and they are not optional:
- Set "approved": false ONLY if at least one of your issues is "blocking". If
  everything you found is "minor", set "approved": true AND still list them.
- "feedback" addresses the blocking issues only. Never instruct the writer to change
  anything on the strength of a minor issue.
- Each rewrite is a fresh regeneration with no memory of the previous one, so it can
  silently drop a correct fact while fixing your complaint. That trade is worth making
  for something untrue. It is never worth making for something that merely reads
  awkwardly.
- WHEN IN DOUBT, "minor". If you cannot point at what is untrue, unsupported, or
  missing, it is not blocking — however strongly you would have phrased it otherwise."""


def review_output_schema(issue_hint: str, feedback_hint: str) -> str:
    """Render the reviewer's JSON output block with the severity field.

    Args:
        issue_hint: In-schema hint describing what one issue should say in THIS
                    domain (e.g. "naming the paragraph and the ungrounded claim").
        feedback_hint: In-schema hint for the ``feedback`` string.
    """
    return f"""Respond ONLY with a valid JSON object — no markdown, no explanations:
{{
  "approved": true or false,
  "issues": [
    {{"severity": "blocking" or "minor", "issue": "{issue_hint}"}}
  ],
  "feedback": "{feedback_hint}"
}}

{SEVERITY_CONTRACT}"""
