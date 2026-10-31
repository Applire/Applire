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
    # D-14 ripple: no end date reads "heute" only when the entry is not marked
    # ended — an explicit ``is_current: False`` shows the start alone.
    end = str(entry.get("end_date") or "").strip() or (
        "" if entry.get("is_current") is False else "heute")
    span = " ".join(
        s for s in (str(entry.get("start_date") or "").strip(), "–", end) if s
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


def _bullet_count(draft: Any) -> int:
    work = draft.get("work") if isinstance(draft, dict) else None
    if not isinstance(work, list):
        return 0
    total = 0
    for entry in work:
        if not isinstance(entry, dict):
            continue
        total += sum(1 for b in (entry.get("bullets") or []) if isinstance(b, str))
        for project in entry.get("projects") or []:
            if isinstance(project, dict):
                total += sum(
                    1 for b in (project.get("bullets") or []) if isinstance(b, str)
                )
    return total


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

    # The id set surviving is not evidence the CONTENT did. A relocation moves
    # a bullet between entries and keeps the total; the feedback also permits
    # dropping a misplaced detail when its owning entry is not in the CV, so
    # the floor is one bullet per finding, not zero. Below that floor the round
    # destroyed prose it was not asked to touch — the #303/GxP full-re-emission
    # class this module's own docstring names — and the original ships.
    before, after = _bullet_count(prose_draft), _bullet_count(corrected)
    if after < before - len(findings):
        logger.warning(
            "ATTRIBUTION_ROUND EXHAUSTED (ADR-071 clause 3): the correction returned "
            "%d bullet(s) where the draft had %d and at most %d relocation(s) were "
            "requested — rejected as content loss, shipping the original draft",
            after, before, len(findings),
        )
        return prose_draft

    original_summary = prose_draft.get("summary") if isinstance(prose_draft, dict) else None
    if original_summary and not corrected.get("summary"):
        logger.warning(
            "ATTRIBUTION_ROUND EXHAUSTED (ADR-071 clause 3): the correction emptied the "
            "summary, which this round never asks about — rejected, shipping the original",
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


# ─────────────────────────────────────────────────────────────────────────────
# ADR-099 cl. 4/6 (UAT-2 fix round, WP-B) — the TRUTH round.
#
# The attribution round above, widened in two directions the founder ruled
# (UAT-3a): it now also carries the ``unbacked`` family (unsupported term,
# credential, identifier, skill, figure, tenure — "corrector removes or
# narrows"), and it runs for the cover letter too (the letter had no
# attribution round at all). Same discipline: ONE bounded corrector call on the
# draft, a structural acceptance check, never a gate, never a code rewrite of
# prose. What the round could not fix is still in the DELIVERED report — the
# same instrument (ADR-099 cl. 4(b)) — and renders as class-A residue.
#
# Removals (cl. 5): a flagged claim present before the round and absent after it
# was removed (or narrowed out) BY the corrector, attributable to this round —
# measured draft-to-draft, never across the later terminal rounds, whose
# rewording would read as removal (the control-false-positive class).
# ─────────────────────────────────────────────────────────────────────────────

#: ADR-099 cl. 4(c): at most this many claims per round (the v2 per-round cap),
#: misattributed first — truthfulness priority within the round.
TRUTH_ROUND_MAX_FINDINGS = 8
#: the letter's per-family cap (``cover_letter._V2_FINDINGS_PER_FAMILY``)
TRUTH_ROUND_LETTER_PER_FAMILY = 2

_UNBACKED_REASON: dict[str, str] = {
    "figuresNoEvidence": "its figure is not stated anywhere in the candidate profile",
    "credentialUnbacked": "the candidate profile records no such credential",
    "identifierAbsent": "the candidate profile records no such identifier",
    "skillNoEvidence": "no entry of the candidate profile shows this skill",
    "tenureStartBeforeVault": "the stated duration starts before the profile's own dates",
    "tenureExceedsVault": "the stated duration is longer than the profile's own dates",
    "subjectTenureExceeds": "the stated duration is longer than the profile's own dates",
}


@dataclass(frozen=True)
class TruthFinding:
    """One group-1 claim the truth round hands the corrector."""

    family: str                 # "misattributed" | "unbacked"
    kind: str                   # finding-registry key, e.g. "oracle:figuresNoEvidence"
    text: str
    location: str
    detail_key: str | None
    rendered_under_id: str | None = None
    owner_ref: str | None = None


def truth_findings(
    report: dict[str, Any] | None, *, document: str = "cv"
) -> list[TruthFinding]:
    """The claims the truth round asks about, capped and ordered (misattributed
    before unbacked). ``inflated`` is NOT here: its stance comes from marker
    lists, a text heuristic, which may not instruct a writer (ADR-062, ADR-099
    cl. 6) — it stays the class-A question. Never raises."""
    claims = (report or {}).get("claims")
    if not isinstance(claims, list):
        return []
    mis: list[TruthFinding] = []
    unb: list[TruthFinding] = []
    seen: set[str] = set()
    for result in claims:
        if not isinstance(result, dict):
            continue
        verdict, claim = result.get("verdict"), result.get("claim")
        if not isinstance(verdict, dict) or not isinstance(claim, dict):
            continue
        v = verdict.get("verdict")
        if v not in ("misattributed", "unbacked"):
            continue
        text = str(claim.get("text") or "").strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        key = verdict.get("detail_key")
        owner_ref = None
        for ref in verdict.get("evidence") or []:
            if isinstance(ref, dict) and ref.get("kind") == "profile_path" and ref.get("ref"):
                owner_ref = str(ref["ref"])
                break
        f = TruthFinding(
            family=v,
            kind=f"oracle:{key}" if key else f"oracle@{v}",
            text=text,
            location=str(claim.get("location") or ""),
            detail_key=key,
            rendered_under_id=(
                str(claim["source_experience_id"]) if claim.get("source_experience_id") else None
            ),
            owner_ref=owner_ref,
        )
        (mis if v == "misattributed" else unb).append(f)
    if document == "cover_letter":
        mis, unb = mis[:TRUTH_ROUND_LETTER_PER_FAMILY], unb[:TRUTH_ROUND_LETTER_PER_FAMILY]
    return (mis + unb)[:TRUTH_ROUND_MAX_FINDINGS]


def build_truth_feedback(
    findings: list[TruthFinding], profile_json: dict[str, Any] | None, *, document: str = "cv"
) -> str:
    """The corrector's referential critique (ADR-021 amended 2026-06-29): it names
    the document's own sentence and the CATEGORY of the defect; vault evidence is
    never pasted, and no system-authored wording is offered as a source (memory
    "a paraphrase is not a source")."""
    mis = [f for f in findings if f.family == "misattributed"]
    unb = [f for f in findings if f.family == "unbacked"]
    blocks: list[str] = []
    if mis and document == "cv":
        blocks.append(build_attribution_feedback(
            [MisattributionFinding(f.text, f.location, f.rendered_under_id, f.owner_ref) for f in mis],
            profile_json,
        ))
    elif mis:
        lines = [
            "ROLE OWNERSHIP (ADR-071): the following sentences tie a fact to an employer "
            "or role that the candidate profile does not assign it to. Each fact is TRUE — "
            "the defect is the attribution. Rewrite only that sentence so it names the "
            "entry the profile assigns the fact to, or drop the misattributed detail. Do "
            "not change anything else.",
        ]
        for f in mis:
            owner = _label(_by_ref(profile_json, f.owner_ref), "the entry the profile assigns it to")
            lines.append(f'- "{f.text}" — the profile assigns this to {owner}.')
        blocks.append("\n".join(lines))
    if unb:
        unit = "bullet or skill" if document == "cv" else "sentence"
        lines = [
            "PROFILE BACKING (ADR-099): the candidate profile does not back the following "
            f"claims. For each {unit}: remove the unbacked claim, or narrow it to exactly "
            "what the profile states (keep the true part, drop the unbacked term, figure, "
            "credential or duration). Re-read the candidate profile for what it states. "
            "Never add a fact, never move the claim to another entry. Do not change "
            "anything else.",
        ]
        for f in unb:
            reason = _UNBACKED_REASON.get(f.detail_key or "", "no entry of the candidate profile states it")
            lines.append(f'- "{f.text}" — {reason}.')
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w%+.,/-]+", " ", s.casefold()).split())


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def claim_present(text: str, draft: Any) -> bool:
    """Normalised containment of a claim's text in any string of ``draft`` — the
    presence FACT the removal line is measured with (ADR-062 cl. 1)."""
    needle = _norm(text)
    if not needle:
        return False
    return any(needle in _norm(s) for s in _strings(draft))


def _letter_paragraphs(draft: Any) -> int:
    body = draft.get("body") if isinstance(draft, dict) else None
    paras = body.get("paragraphs") if isinstance(body, dict) else None
    return len([p for p in paras if isinstance(p, str) and p.strip()]) if isinstance(paras, list) else 0


@dataclass
class TruthRoundResult:
    draft: dict[str, Any]
    findings: list[TruthFinding]
    #: findings whose claim text was in the draft before the round and is gone after it
    removed: list[TruthFinding]
    applied: bool = False
    llm_calls: int = 0


async def run_truth_round(
    draft: dict[str, Any],
    *,
    report: dict[str, Any] | None,
    profile_json: dict[str, Any] | None,
    source_material: str,
    provider: Any,
    system: str,
    document: str = "cv",
    prompt_fn: Any = None,
    max_tokens: int = CV_GENERATION_MAX_TOKENS,
    json_schema: dict[str, Any] | None = None,
) -> TruthRoundResult:
    """AT MOST ONE corrector call over the group-1 claims of ``report`` (ADR-099
    cl. 6). Returns the corrected draft, or ``draft`` itself when there is nothing
    to fix or the response is not structurally safe. Never raises.

    ``prompt_fn(previous_draft, feedback, source) -> str`` is the chain's own
    corrector prompt (the letter's carries its measure + language blocks);
    ``None`` → the CV ``build_retry_prompt``. ``system`` is the chain's corrector
    system prompt (v1 or v2) — the round speaks the loop's own language.
    """
    findings = truth_findings(report, document=document)
    if not findings:
        return TruthRoundResult(draft, [], [])
    feedback = build_truth_feedback(findings, profile_json, document=document)
    logger.info(
        "TRUTH_ROUND (ADR-099 cl. 6) document=%s: %d claim(s) — %s",
        document, len(findings), [(f.family, f.kind, f.location) for f in findings],
    )
    build = prompt_fn or build_retry_prompt
    try:
        corrected = await provider.aparse_json(
            build(draft, feedback, source_material),
            system=system,
            temperature=0.2,
            max_tokens=max_tokens,
            # the loop corrector's own structured-output schema (#767), when it has one
            **({"json_schema": json_schema} if json_schema else {}),
        )
    except Exception:
        logger.warning(
            "TRUTH_ROUND EXHAUSTED: the correction call failed; the claims stay and "
            "render as class-A residue (ADR-099 cl. 4(e))", exc_info=True,
        )
        return TruthRoundResult(draft, findings, [], llm_calls=1)
    if not isinstance(corrected, dict):
        logger.warning("TRUTH_ROUND EXHAUSTED: non-object response — original kept")
        return TruthRoundResult(draft, findings, [], llm_calls=1)
    if document == "cv":
        if _work_ids(corrected) != _work_ids(draft):
            logger.warning(
                "TRUTH_ROUND EXHAUSTED: the correction dropped or renamed a work entry "
                "(%s -> %s) — rejected", sorted(_work_ids(draft)), sorted(_work_ids(corrected)),
            )
            return TruthRoundResult(draft, findings, [], llm_calls=1)
        if _bullet_count(corrected) < _bullet_count(draft) - len(findings):
            logger.warning("TRUTH_ROUND EXHAUSTED: bullets lost beyond the findings — rejected")
            return TruthRoundResult(draft, findings, [], llm_calls=1)
        if draft.get("summary") and not corrected.get("summary"):
            logger.warning("TRUTH_ROUND EXHAUSTED: the summary was emptied — rejected")
            return TruthRoundResult(draft, findings, [], llm_calls=1)
    else:
        before, after = _letter_paragraphs(draft), _letter_paragraphs(corrected)
        if after == 0 or after < before - 1:
            logger.warning(
                "TRUTH_ROUND EXHAUSTED: the letter body lost paragraphs (%d -> %d) — rejected",
                before, after,
            )
            return TruthRoundResult(draft, findings, [], llm_calls=1)
    if corrected == draft:
        logger.warning(
            "TRUTH_ROUND EXHAUSTED: unchanged draft for %d claim(s); they render as "
            "class-A residue", len(findings),
        )
        return TruthRoundResult(draft, findings, [], llm_calls=1)
    removed = [
        f for f in findings
        if f.family == "unbacked" and claim_present(f.text, draft) and not claim_present(f.text, corrected)
    ]
    logger.info(
        "TRUTH_ROUND applied document=%s: %d claim(s) sent, %d removed or narrowed",
        document, len(findings), len(removed),
    )
    return TruthRoundResult(corrected, findings, removed, applied=True, llm_calls=1)
