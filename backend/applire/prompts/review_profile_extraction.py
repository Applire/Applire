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

# Prompt version: v2 (2026-09-17, #713 — the date rule and the approval bar ported from
#                  review_cv_extraction.py v5 (2026-06-30), which this door never received.
#                  Check 3 said dates "must match exactly what is stated in the source"; on a
#                  German LinkedIn export the reviewer blocked `2024-12` against "Dezember 2024",
#                  the corrector rewrote every date into German month names, the import witness
#                  could then match no position, and the loop exhausted 5/5 on issues that
#                  concluded "no issue". Check 3 now forbids invented date COMPONENTS only;
#                  format normalisation is named as expected; an issue that finds nothing is
#                  not listed; the closing question names all four checks (it dropped check 4 —
#                  Vault collector #674). Replay evidence: see the #713 PR body.)
#            v1
# Used by: services/profile/__init__.py → ingest_cv → reviewer.review_and_refine
#
# 2026-09-13 (ruling E-1, then ruling V-3) — A FIFTH CHECK WAS BUILT, MEASURED AND NOT SHIPPED.
#   The prompt text below is unchanged; this header is what E-1 produced, and it is here so the
#   next agent does not re-derive the idea and re-spend the measurement.
#
#   Why it was proposed: build 2 ported #407's PER-ENTRY GROUNDING rule onto the agent door's
#   EXTRACTION prompt (`profile_extraction.py` rule 14) and measured it as door parity, NOT as a
#   repair — `ministral-8b-2512` backfills a general KENNTNISSE item onto a role whose own text
#   never names it 5/5 before the rule and 4/5 after, on the rule's own worked example.
#   Ruling E-1 dispositioned the residue on the REVIEWER: the review loop is the door's second
#   chance and the only stage that sees the extraction and the source side by side.
#
#   What was built: check 5 MISATTRIBUTED TECHNOLOGIES, blocking, plus the same enumeration in
#   the "WHAT IS BLOCKING" sentence. Triage (applire-prompt-first): Category B — the narrower-
#   rule check was run first and check 4 is about BULLETS and about content "not present in the
#   source", while this defect is neither (the token IS in the source, in the general skills
#   section, and lands on a `technologies` list).
#
#   How it was measured: the production reviewer call replayed over two CAPTURED extractions —
#   one that backfills, one that does not (`tests/files/extraction_parity/
#   multi_employer_kenntnisse_hard.{violating,clean}.json`, taken from the build-2 records so
#   the positive case is guaranteed to contain the defect) — n=5 per case per model per arm,
#   real provider, 60 calls. Blocking issue naming a misattributed tool:
#
#     arm            model               violating      clean (false positives)
#     v1 (no rule)   gpt-5.6-luna        5/5            1/5
#     v1 (no rule)   ministral-8b-2512   2/5            0/5
#     check 5        gpt-5.6-luna        5/5            1/5
#     check 5        ministral-8b-2512   5/5            5/5
#     check 5 sharp. gpt-5.6-luna        5/5            0/5
#     check 5 sharp. ministral-8b-2512   5/5            5/5
#
#   ("sharp." = the wording re-aimed at the observed failure: judge the entry against the
#   EMPLOYER'S OWN SOURCE PASSAGE, never against the extracted JSON's arrays.)
#
#   Why it is not shipped (ruling V-3): on the qualified model the check buys no new CATCH —
#   luna already reached this defect by stretching check 4 — and on `ministral-8b-2512` it fires
#   on every CLEAN extraction, which the founder's standing criterion (M5.1.1: a change that
#   degrades a model is not shipped) reads as a precision degradation. The false positives are
#   not near-misses: all five accuse `SAP MM`, which appears on NO entry of the clean
#   extraction, and two of them attack the top-level `skills` array — the exact place the rule
#   says a general skill belongs.
#
#   What remains open: Vault collector #674 carries the false-positive shape and the one
#   unmeasured prompt-health finding this work surfaced — the closing question in
#   `build_review_prompt` enumerates three of the four checks ("no duplicates, no fabrications,
#   no invented dates"), so the last sentence the model reads silently drops check 4.
#   Records: `Documents/Runs/Nougat/build-3/v/runs/E1-{before,after,sharpened}-*.jsonl`.

import json

from applire.prompts.review_severity import review_output_schema

REVIEW_SYSTEM_PROMPT = """\
You are a strict CV data quality auditor. Your task is to verify that an extracted
profile JSON faithfully represents the source CV text — nothing more, nothing less.
Extraction is a NORMALISING transform into a fixed schema: judge meaning and provenance,
never surface form.

APPROVAL BAR: list an issue ONLY when it names a defect that needs a correction. If what you
would write about an entry is "faithful", "supported", "acceptable" or "no issue", it is not
an issue — leave it out. When nothing needs correcting, approve with an empty issues list.

Check for ALL of the following:
1. DUPLICATE ENTRIES: Each employer and role must appear exactly once in work_history.
   Flag any entry that is a duplicate or variant of another entry (same company/role,
   different or missing dates).
2. FABRICATED ENTRIES: Every work_history entry must have a clear corresponding passage
   in the source text. Flag any entry with no basis in the source.
3. INVENTED DATES: a start_date or end_date must not assert a year or month that the source
   does not state for that entry. If a date is absent from the source, the field must be null —
   never inferred or invented. This is about date COMPONENTS, not format: the schema stores dates
   as "YYYY-MM" or "YYYY", so a source month name in any language ("Dezember 2024", "Dec 2024")
   rendered as "2024-12" is CORRECT, and an ongoing role ("Present", "heute") is stored as
   end_date null with is_current true. Never ask for the source's own date wording.
4. INVENTED BULLETS: Bullets must reflect what is explicitly stated in the source text.
   Flag any bullet that adds responsibilities, achievements, or skills not present in the source.

WHAT IS BLOCKING IN THIS PASS: a failure of check 1, 2, 3 or 4 above — a duplicate, a
fabricated entry, an invented date, an invented bullet. Nothing else. Rewording, reordering,
capitalisation, and how a source sentence was split or joined are "minor" BY DEFINITION:
extraction is a normalising transform, and re-running it to satisfy a phrasing preference
risks losing a fact it had right.

""" + review_output_schema(
    issue_hint="specific issue with work_history index and description — empty array if nothing found",
    feedback_hint="concise instruction for the extractor to correct the BLOCKING issues — empty string if there are none",
) + """

Keep `feedback` concise and *referential*: name the offending location (work_experience index,
field, section) and state what is wrong. Do NOT quote or paste source passages — the corrector
re-reads the source text itself (ADR-021 amended 2026-06-29)."""


def build_review_prompt(raw_cv_text: str, extracted_json: dict) -> str:
    """Build the reviewer user prompt for profile extraction.

    Args:
        raw_cv_text: The original CV text the profile was extracted from.
        extracted_json: The profile JSON produced by the extraction agent.
    """
    return (
        "Review this extracted profile against the source CV text.\n\n"
        f"SOURCE CV TEXT:\n{raw_cv_text}\n\n"
        f"EXTRACTED PROFILE:\n{json.dumps(extracted_json, ensure_ascii=False, indent=2)}\n\n"
        "Does the extracted profile faithfully represent the source — no duplicate entries, "
        "no fabricated entries, no invented dates, no invented bullets? Return your review JSON."
    )
