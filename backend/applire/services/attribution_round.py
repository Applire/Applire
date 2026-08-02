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

"""ADR-071 clause 3 — the generation-side consumer of the Oracle's
``misattributed`` verdict.

The verdict was never the problem. ``oracle/extract.py`` stamps every CV bullet
claim with ``source_id = entry["id"]`` — the position it is actually rendered
under (US187) — and ``oracle/audit.py::_attribution_red_flag`` compares that
against ``EvidenceUnit.owner_ids`` via the shared matcher. Deterministic,
id-anchored, unit-tested, and running on every CV generation since Oracle v2.
The red chip has been on the review screen since 2026-07-19.

What was missing is a consumer. A control that computes the right answer and
only writes a report is write-only, and the CV chain had exactly one such gap
where the letter chain has had ``guard_letter_figures`` all along. #413 / #349
and the generator half of #378 are one bullet — a current-employer SAP fact
rendered under an employer left in 2017 — that survived five ``cv_tailoring``
rounds and one ``cv_language`` round because no instruction and no check ever
named ownership (ADR-071 clauses 1 and 2 fix that half).

**What this module is, precisely:**

* a **targeted review round**, not a strip. ``letter_figure_guard``'s deletion
  semantics are deliberately not mirrored: deleting the bullet destroys the
  candidate's true evidence, and this repository has measured that harm twice
  (#347 — offered denial choices were blanket denials, wrong 3/3, always
  toward destroying real evidence; #377 — the bullet cap deleted the CV's most
  quantified achievement). Relocation is the honest remedy, and only the writer
  can phrase it. Code never rewrites prose here.
* **not a gate.** ADR-052 §5 and ADR-060's PO decision 2 ("deliver the best
  document we have") stand unchanged. Every failure path returns a usable
  draft; nothing in this module raises.
* **one round, hard-capped.** Exhaustion is logged like ADR-021/#264
  exhaustion, so a document that shipped still-misattributed is visible rather
  than silently accepted.

The round runs on the PROSE draft (ADR-067's shape: ``summary`` / ``work`` /
``skills``), before assembly's deterministic join and before the post-review
tail — so a relocated bullet is still subject to every pass that follows,
exactly like one the writer placed correctly the first time.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from applire.constants import CV_GENERATION_MAX_TOKENS
from applire.prompts.cv_tailoring import CV_TAILORING_REFINEMENT_PROMPT, build_retry_prompt

logger = logging.getLogger(__name__)

_WORK_REF = re.compile(r"^work_experience\[(\d+)\]")


@dataclass(frozen=True)
class MisattributionFinding:
    """One ``misattributed`` claim, reduced to what the writer needs told."""

    text: str
    location: str
    rendered_under_id: str | None
    #: The ``EvidenceRef.ref`` of the vault evidence backing the claim, e.g.
    #: ``work_experience[0].responsibilities[0]``. ``None`` when the verdict
    #: carried no evidence ref — the finding still stands, the feedback is just
    #: vaguer.
    owner_ref: str | None


def misattributed_findings(report: dict[str, Any] | None) -> list[MisattributionFinding]:
    """Every ``misattributed`` claim in a truthfulness report.

    Tolerant of an absent, empty or malformed report in every direction: this
    consumer may never be the reason a generation fails.
    """
    claims = (report or {}).get("claims")
    if not isinstance(claims, list):
        return []

    found: list[MisattributionFinding] = []
    for result in claims:
        if not isinstance(result, dict):
            continue
        verdict = result.get("verdict")
        claim = result.get("claim")
        if not isinstance(verdict, dict) or not isinstance(claim, dict):
            continue
        if verdict.get("verdict") != "misattributed":
            continue
        owner_ref = None
        for ref in verdict.get("evidence") or []:
            if isinstance(ref, dict) and ref.get("kind") == "profile_path" and ref.get("ref"):
                owner_ref = str(ref["ref"])
                break
        found.append(MisattributionFinding(
            text=str(claim.get("text") or ""),
            location=str(claim.get("location") or ""),
            rendered_under_id=(
                str(claim["source_experience_id"])
                if claim.get("source_experience_id") else None
            ),
            owner_ref=owner_ref,
        ))
    return found


def _entries(profile_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    entries = (profile_json or {}).get("work_experience")
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _label(entry: dict[str, Any] | None, fallback: str) -> str:
    """A human-readable position label — how the writer sees the entry in its
    own prompt, not an internal id, wherever the profile allows it."""
    if not entry:
        return fallback
    company = str(entry.get("company") or "").strip()
    role = str(entry.get("role") or entry.get("position") or "").strip()
    span = " ".join(
        s for s in (str(entry.get("start_date") or "").strip(),
                    "–", str(entry.get("end_date") or "heute").strip()) if s
    ).strip(" –")
    parts = [p for p in (company, role) if p]
    label = ", ".join(parts) if parts else fallback
    return f"{label} ({span})" if span and parts else label


def _by_id(profile_json: dict[str, Any] | None, entry_id: str | None) -> dict[str, Any] | None:
    if not entry_id:
        return None
    for entry in _entries(profile_json):
        if str(entry.get("id") or "") == entry_id:
            return entry
    return None


def _by_ref(profile_json: dict[str, Any] | None, ref: str | None) -> dict[str, Any] | None:
    """Resolve a ``work_experience[N]...`` profile path to its work entry."""
    match = _WORK_REF.match(ref or "")
    if not match:
        return None
    entries = _entries(profile_json)
    index = int(match.group(1))
    return entries[index] if 0 <= index < len(entries) else None


def build_attribution_feedback(
    findings: list[MisattributionFinding], profile_json: dict[str, Any] | None
) -> str:
    """The referential critique the corrector receives (ADR-021 amended
    2026-06-29): it names WHERE the defect is and lets the corrector re-read the
    profile itself.

    The claim's own text IS quoted — that is the document's words, and it is
    the only unambiguous way to point at one bullet among several. Vault
    evidence is never pasted.

    The instruction asks for RELOCATION. Deletion is not offered as an option,
    because the fact is true and the candidate is entitled to it; the only
    defect is which employer it sits under.
    """
    if not findings:
        return ""

    lines = [
        "ROLE OWNERSHIP (ADR-071): the following bullets are rendered under a work "
        "entry that does not own their evidence. Each one is TRUE — the defect is "
        "placement, not the claim. Move each bullet under the work entry the "
        "candidate profile says owns it (keep the wording and the figures), or, if "
        "that entry is not in this CV, drop the misplaced detail from the bullet "
        "rather than restating it under the wrong employer. Do not change anything "
        "else.",
    ]
    for finding in findings:
        rendered = _label(
            _by_id(profile_json, finding.rendered_under_id),
            finding.rendered_under_id or finding.location or "unknown entry",
        )
        owner_entry = _by_ref(profile_json, finding.owner_ref)
        owner = _label(owner_entry, "the entry the profile assigns this evidence to")
        lines.append(
            f'- "{finding.text}" is written under {rendered}, '
            f"but the profile assigns this evidence to {owner}."
        )
    return "\n".join(lines)


def _work_ids(draft: Any) -> set[str]:
    work = draft.get("work") if isinstance(draft, dict) else None
    if not isinstance(work, list):
        return set()
    return {str(w.get("id")) for w in work if isinstance(w, dict) and w.get("id")}


async def run_attribution_round(
    prose_draft: dict[str, Any],
    *,
    report: dict[str, Any] | None,
    profile_json: dict[str, Any] | None,
    source_material: str,
    provider: Any,
) -> dict[str, Any]:
    """Run AT MOST ONE targeted ``cv_tailoring`` correction round.

    Returns the corrected prose draft, or ``prose_draft`` itself (the same
    object) when there is nothing to fix or the round could not produce
    something strictly safe to use. Never raises.

    The acceptance check on the round's output is structural, not qualitative:
    the corrector is a full re-emission of the prose object (the #303/GxP
    custody class), so a response that drops a work-entry id is a worse defect
    than the misattribution it was asked to fix. Whether the misattribution was
    actually resolved is not judged here — the pre-persistence self-audit runs
    again downstream and the review screen shows the human what shipped.
    """
    findings = misattributed_findings(report)
    if not findings:
        return prose_draft

    feedback = build_attribution_feedback(findings, profile_json)
    logger.info(
        "ATTRIBUTION_ROUND (ADR-071 clause 3): %d misattributed claim(s) — %s",
        len(findings),
        [(f.rendered_under_id, f.location) for f in findings],
    )

    try:
        corrected = await provider.aparse_json(
            build_retry_prompt(prose_draft, feedback, source_material),
            system=CV_TAILORING_REFINEMENT_PROMPT,
            temperature=0.2,
            max_tokens=CV_GENERATION_MAX_TOKENS,
        )
    except Exception:
        logger.warning(
            "ATTRIBUTION_ROUND EXHAUSTED (ADR-071 clause 3): the correction call failed; "
            "shipping the draft as-is — the misattribution stays visible on the review "
            "screen (ADR-052 §5: never a delivery gate)",
            exc_info=True,
        )
        return prose_draft

    original_ids = _work_ids(prose_draft)
    if not isinstance(corrected, dict) or _work_ids(corrected) != original_ids:
        logger.warning(
            "ATTRIBUTION_ROUND EXHAUSTED (ADR-071 clause 3): the correction dropped or "
            "renamed a work entry (%s -> %s) — rejected, shipping the original draft",
            sorted(original_ids), sorted(_work_ids(corrected)),
        )
        return prose_draft

    if corrected == prose_draft:
        logger.warning(
            "ATTRIBUTION_ROUND EXHAUSTED (ADR-071 clause 3): the round returned an "
            "unchanged draft for %d flagged claim(s); the document ships "
            "misattributed and the red flag is what the human sees",
            len(findings),
        )
        return prose_draft

    logger.info(
        "ATTRIBUTION_ROUND applied (ADR-071 clause 3): %d claim(s) sent back to the "
        "writer for relocation", len(findings),
    )
    return corrected
