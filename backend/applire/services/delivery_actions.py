# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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
"""ADR-099 cl. 6/9 — the corrector actions the review surface offers on ONE
finding of a delivered document (WP-B, for WP-C's resolvers).

Each action is a model edit of ONE sentence (never a code rewrite of prose,
ADR-082/094 cl. 1), saved through the section-editor write, followed by the
full document re-audit (the same function the surface reads, ADR-099 cl. 4(b)),
and returns the new report. User-triggered, shown, re-audited and reversible
(the ADR-090 cl. 3 carve-out ADR-099 cl. 9 extends to translation).

Contract for the caller (WP-C, ``services/review_actions.py``):

* the caller holds ``review_state.document_lock(kind, doc_id)`` and records the
  review decision (``with_decision``) with ``result.changes`` as the undo
  sections — these functions never touch ``review_state``;
* ``finding_key`` is the ``review_state.finding_key`` of a group-1 row
  (``oracle:<norm>`` / ``ats:<norm>``) as the live report lists it;
* every function raises :class:`ActionNotApplicable` (→ 409) when the finding
  is no longer listed, the station is not one of :func:`owning_stations`, or
  there is nothing to change; ``RewriteFailed`` (→ 502) when the model's edit
  did not pass its fact check (nothing is written then).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

Kind = Literal["cv", "cover_letter"]
NarrowMode = Literal["remove", "narrow", "as_goal"]


class ActionNotApplicable(LookupError):
    """The finding / station / span the action names is not (or no longer) there."""


class RewriteFailed(RuntimeError):
    """The model edit returned nothing usable; nothing was written."""


@dataclass(frozen=True)
class StationOption:
    """One answer of "Wohin gehört das?" — a station that OWNS the evidence the
    Oracle found for a misattributed / borrowed claim (from the verdict's
    evidence refs, never from text)."""

    #: the section id the sentence moves into (CV: ``position::<tailored
    #: position id>`` as ``cv_section_editor`` names it; letter: the work
    #: entry id — the letter has one ``body`` section, the move rewrites the
    #: sentence to name that station).
    station_id: str
    #: profile entry id (``WorkEntry.id`` / project id) — stable across versions.
    experience_id: str | None
    #: structured parts for the UI to compose, never a server sentence.
    title: str
    organization: str


@dataclass
class DeliveryActionResult:
    record: Any                      # refreshed GeneratedCV / GeneratedCoverLetter
    report: dict | None              # record.ats_report after the re-audit
    truthfulness: dict | None        # record.truthfulness_report after the re-audit
    changes: list[dict] = field(default_factory=list)   # [{"section_id","before","after"}] — undo
    still_listed: bool = False       # finding_key still listed in the live report
    llm_calls: int = 0


async def owning_stations(
    kind: Kind, doc_id: uuid.UUID, finding_key: str, db, *, user_id: uuid.UUID | None = None
) -> list[StationOption]:
    """The stations that own the evidence of a ``misattributed`` /
    ``restatementBorrowed`` finding — the answers of the "Wohin gehört das?"
    card (plus the surface's own "Entfernen"). ``[]`` when the verdict carries no
    owned evidence (then the only end state is removal). Raises
    :class:`ActionNotApplicable` when the finding is not listed."""
    from applire.services.review_actions import load_document

    record = await load_document(kind, doc_id, db, user_id=user_id)
    claim, verdict = _listed_claim(record, finding_key)
    if verdict.get("verdict") != "misattributed":
        raise ActionNotApplicable(f"finding {finding_key!r} is not an attribution finding")
    profile = await _profile_json(record, db)
    entries = _work_entries(profile)
    rendered_under = str(claim.get("source_experience_id") or "")
    owners: list[dict] = []
    for ref in verdict.get("evidence") or []:
        m = _WORK_REF.match(str((ref or {}).get("ref") or "")) if isinstance(ref, dict) else None
        if not m:
            continue
        # ADV-4: the index was taken at audit time; the profile may have shifted
        # since (U-11 merge, adopt, an import). Resolve by what the entry HOLDS.
        e = _evidence_owner(entries, int(m.group(1)), str(ref.get("excerpt") or ""))
        if str(e.get("id") or "") and str(e.get("id")) != rendered_under and e not in owners:
            owners.append(e)
    out: list[StationOption] = []
    for e in owners:
        eid = str(e.get("id") or "")
        title = str(e.get("title") or e.get("role") or e.get("position") or "")
        org = str(e.get("company") or "")
        if kind == "cv":
            pos = next((p for p in (record.content_snapshot or {}).get("positions") or []
                        if str(p.get("work_id") or "") == eid), None)
            if pos is None:
                continue  # the owning station is not in this CV: only removal
            out.append(StationOption(f"position::{pos['id']}", eid, pos.get("title") or title,
                                     pos.get("company") or org))
        else:
            out.append(StationOption(eid, eid, title, org))
    return out


def _entry_text(value) -> str:
    """Every string an entry holds (title, company, bullets, nested lists), folded."""
    if isinstance(value, str):
        return _fold(value)
    if isinstance(value, dict):
        return " \n ".join(_entry_text(v) for v in value.values())
    if isinstance(value, list):
        return " \n ".join(_entry_text(v) for v in value)
    return ""


def _evidence_owner(entries: list[dict], index: int, excerpt: str) -> dict:
    """The live profile entry a persisted ``work_experience[N]`` evidence ref
    names (ADV-4). The index is the audit's, not the profile's: it counts only
    while that entry still holds the evidence excerpt; otherwise the ONE entry
    that holds it is the owner. No excerpt, no holder, or several holders none
    of which sits at the index → :class:`ActionNotApplicable` (409): the card
    offers *Entfernen* or a recheck, never a guessed employer."""
    want = _fold(excerpt)
    if not want:
        raise ActionNotApplicable("the evidence carries no excerpt to resolve its entry by")
    if 0 <= index < len(entries) and want in _entry_text(entries[index]):
        return entries[index]
    holders = [e for e in entries if want in _entry_text(e)]
    if len(holders) == 1:
        return holders[0]
    raise ActionNotApplicable(
        "the profile changed since the audit — the evidence's entry is "
        + ("gone" if not holders else "ambiguous") + "; recheck the document"
    )


async def move_sentence(
    kind: Kind,
    doc_id: uuid.UUID,
    finding_key: str,
    station_id: str,
    db,
    provider,
    *,
    user_id: uuid.UUID | None = None,
) -> DeliveryActionResult:
    """Move the finding's sentence to ``station_id`` (one of
    :func:`owning_stations`): the corrector rewrites the sentence for that
    station; on a CV it leaves its current section and is written under the owning
    position, on a letter it is rewritten in place to name that station; then
    re-audits."""
    from applire.services.review_actions import load_document, patchable_sections

    stations = {s.station_id: s for s in await owning_stations(kind, doc_id, finding_key, db, user_id=user_id)}
    station = stations.get(station_id)
    if station is None:
        raise ActionNotApplicable(f"station {station_id!r} does not own this finding's evidence")
    record = await load_document(kind, doc_id, db, user_id=user_id)
    claim, _verdict = _listed_claim(record, finding_key)
    sections = await patchable_sections(kind, record, db)
    hit = _locate(sections, str(claim.get("text") or ""))
    if hit is None:
        raise ActionNotApplicable("the sentence is no longer in the document")
    sid, full, start, end = hit
    sentence = full[start:end]
    language = await _language(kind, record, db)
    label = ", ".join(p for p in (station.title, station.organization) if p)
    new = await _edit(provider, record, kind, "move", sentence, language=language, station=label)
    if not new:
        raise RewriteFailed("the move edit returned nothing usable")
    if kind == "cv":
        if station_id == sid:
            raise ActionNotApplicable("the sentence already stands under this station")
        target = dict(sections).get(station_id)
        if target is None:
            raise ActionNotApplicable(f"station section {station_id!r} is not in this CV")
        changes = [
            {"section_id": sid, "before": full, "after": _splice(full, start, end, "")},
            {"section_id": station_id, "before": target,
             "after": (target.rstrip("\n") + "\n" + new) if target.strip() else new},
        ]
    else:
        changes = [{"section_id": sid, "before": full, "after": _splice(full, start, end, new)}]
    return await _apply(kind, record, db, changes, finding_key, calls=1)


async def narrow_or_remove(
    kind: Kind,
    doc_id: uuid.UUID,
    finding_key: str,
    db,
    provider,
    *,
    mode: NarrowMode = "remove",
    user_id: uuid.UUID | None = None,
) -> DeliveryActionResult:
    """``remove`` — take the claim out (whole sentence, or only the named figures
    for a ``figuresNoEvidence`` verdict, through the review removal rewrite);
    ``narrow`` — rewrite it to what the profile backs; ``as_goal`` — rewrite an
    ``inflatedAspirational`` claim as the goal the profile states ("Ist das
    erreicht?" → Nein). Re-audits."""
    from applire.services.review_actions import load_document, patchable_sections

    if mode not in ("remove", "narrow", "as_goal"):
        raise ActionNotApplicable(f"unknown mode {mode!r}")
    record = await load_document(kind, doc_id, db, user_id=user_id)
    claim, verdict = _listed_claim(record, finding_key)
    text = str(claim.get("text") or "")
    sections = await patchable_sections(kind, record, db)
    language = await _language(kind, record, db)
    if mode == "remove":
        from applire.services.review_rewrite import rewrite_for_removal

        figures = [str(f) for f in verdict.get("figures") or [] if f]
        changes, calls = [], 0
        hit = _locate(sections, text)
        targets = [hit] if hit else []
        for sid, full, start, end in targets:
            span = full[start:end]
            res = await rewrite_for_removal(
                kind, record, sid, span, figures or [span], provider,
                language=language, figures_only=bool(figures),
            )
            calls += res.llm_calls
            if res.changed:
                changes.append({"section_id": sid, "before": full, "after": _splice(full, start, end, res.after)})
            elif not figures:
                # the whole claim IS the wording: the claim leaves with its line
                changes.append({"section_id": sid, "before": full, "after": _splice(full, start, end, "")})
        if not changes:
            raise ActionNotApplicable("nothing to remove: the claim is no longer in the document")
        return await _apply(kind, record, db, changes, finding_key, calls=calls)
    if mode == "as_goal" and verdict.get("verdict") != "inflated":
        raise ActionNotApplicable("as_goal answers an inflatedAspirational finding only")
    hit = _locate(sections, text)
    if hit is None:
        raise ActionNotApplicable("the sentence is no longer in the document")
    sid, full, start, end = hit
    excerpt = _profile_excerpt(await _profile_json(record, db), claim) if mode == "narrow" else None
    new = await _edit(provider, record, kind, mode, full[start:end], language=language,
                      profile_excerpt=excerpt)
    if new is None:
        raise RewriteFailed(f"the {mode} edit returned nothing usable")
    changes = [{"section_id": sid, "before": full, "after": _splice(full, start, end, new)}]
    return await _apply(kind, record, db, changes, finding_key, calls=1)


async def translate_spans(
    kind: Kind,
    doc_id: uuid.UUID,
    db,
    provider,
    *,
    spans: list[str] | None = None,
    user_id: uuid.UUID | None = None,
) -> DeliveryActionResult:
    """"Diese Stellen übersetzen": rewrite ONLY the sentences the failed
    ``document-language`` check names (``spans`` = a subset of them; None = all)
    into the document language, then re-audit. The items are recomputed with the
    check's own enumeration on the document as it stands (one instrument)."""
    from applire.services.review_actions import load_document, patchable_sections

    record = await load_document(kind, doc_id, db, user_id=user_id)
    language = await _language(kind, record, db)
    items = _foreign_items(kind, record, language)
    if spans is not None:
        wanted = {_fold(s) for s in spans}
        items = [t for t in items if _fold(t) in wanted or any(_fold(t).startswith(w) for w in wanted)]
    if not items:
        raise ActionNotApplicable("no sentence of this document is in another language")
    sections = await patchable_sections(kind, record, db)
    current = dict(sections)
    changes: dict[str, dict] = {}
    calls = 0
    for item in items[:12]:
        hit = _locate(list(current.items()), item)
        if hit is None:
            continue
        sid, full, start, end = hit
        new = await _edit(provider, record, kind, "translate", full[start:end], language=language)
        calls += 1
        if not new or not _same_numbers(full[start:end], new):
            continue
        after = _splice(full, start, end, new)
        changes.setdefault(sid, {"section_id": sid, "before": full, "after": after})["after"] = after
        current[sid] = after
    if not changes:
        raise RewriteFailed("no translation passed its fact check")
    result = await _apply(kind, record, db, list(changes.values()), None, calls=calls)
    result.still_listed = bool(_foreign_items(kind, result.record, language))
    return result


async def shorten_letter(
    kind: Kind,
    doc_id: uuid.UUID,
    db,
    provider,
    *,
    user_id: uuid.UUID | None = None,
) -> DeliveryActionResult:
    """The letter's page-length card, "kürzen" (ADR-099 cl. 7): one corrector call
    condenses the body toward the page norm, keeping every fact the letter needs;
    then re-audits. Residue still over the norm renders the card again."""
    from applire.norms import REGION_NORMS
    from applire.prompts.cover_letter import build_condense_prompt, build_system_prompt
    from applire.services.cover_letter import _apply_section_overrides, _condense_call
    from applire.services.review_actions import load_document

    if kind != "cover_letter":
        raise ActionNotApplicable("the CV's page card is the ladder proposal (POST page-overflow)")
    record = await load_document(kind, doc_id, db, user_id=user_id)
    check = _check(record, "page-length")
    if check is None or check.get("status") != "fail":
        raise ActionNotApplicable("the letter is within its page norm")
    params = check.get("details_params") or {}
    region = params.get("region") or "DACH"
    norm = REGION_NORMS.get(region) or next(iter(REGION_NORMS.values()))
    language = await _language(kind, record, db)
    data = _apply_section_overrides(record.letter_data or {}, record.section_overrides or {})
    before = "\n\n".join(p for p in ((data.get("body") or {}).get("paragraphs") or []) if isinstance(p, str))
    pins = await _letter_pin_quotes(record, db)
    condensed = await _condense_call(
        provider,
        build_condense_prompt(
            data, norm.letter_body_word_budget, int(params.get("pages") or 2),
            letter_pages=norm.letter_pages, language=language,
            # ADR-077 cl. 2/3: a pinned sentence survives the shortening — by
            # instruction here, refused below when the result drops one.
            pinned_quotes=pins or None,
        ),
        stage="letter_condense_review_action",
        system=build_system_prompt(language),
    )
    paras = [p for p in ((condensed.get("body") or {}).get("paragraphs") or []) if isinstance(p, str) and p.strip()]
    after = "\n\n".join(paras)
    if not paras or after == before or not _no_new_numbers(before, after):
        raise RewriteFailed("the shortening returned nothing usable")
    from applire.services.review_state import norm_quote

    lost = [q for q in pins if norm_quote(q) in norm_quote(before) and norm_quote(q) not in norm_quote(after)]
    if lost:
        logger.info("DELIVERY_SHORTEN_REFUSED reason=pinned_dropped n=%d", len(lost))
        raise RewriteFailed("the shortening dropped a pinned fact")
    result = await _apply(kind, record, db, [{"section_id": "body", "before": before, "after": after}], None, calls=1)
    c = _check(result.record, "page-length")
    result.still_listed = bool(c and c.get("status") == "fail")
    return result


# ── plumbing ─────────────────────────────────────────────────────────────────

async def _letter_pin_quotes(record, db) -> list[str]:
    """The letter's active pinned quotes (ADR-077), as the letter audit loads them;
    ``[]`` on any failure (fail-safe: the shortening then has nothing to protect,
    and the re-audit still measures pin presence)."""
    try:
        from applire.services.application import get_application_for_job
        from applire.services.fact_pins import load_pins
        from applire.services.pin_reach import active_pins

        app = await get_application_for_job(record.job_analysis_id, record.user_id, db)
        if app is None or not app.pinned_facts:
            return []
        return [pn.quote for pn in active_pins(load_pins(app), "letter") if pn.quote]
    except Exception:  # noqa: BLE001
        logger.exception("pin load failed for shorten_letter on %s", getattr(record, "id", None))
        return []


_WORK_REF = re.compile(r"^work_experience\[(\d+)\]")
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_EMPTY = "<EMPTY>"


def _fold(s: str) -> str:
    return " ".join(str(s or "").casefold().split())


def _listed_claim(record, finding_key: str) -> tuple[dict, dict]:
    """The flagged Oracle claim behind a group-1 key as the LIVE report lists it."""
    from applire.services.review_actions import findings_of
    from applire.services import review_state as rs

    finding = rs.find_listed(findings_of(record), finding_key)
    if finding is None:
        raise ActionNotApplicable(f"finding {finding_key!r} is not listed")
    want = rs.norm_quote(finding.claim_text or "") if finding.claim_text else rs.split_key(finding_key)[1]
    for c in (record.truthfulness_report or {}).get("claims") or []:
        if not isinstance(c, dict):
            continue
        claim, verdict = c.get("claim") or {}, c.get("verdict") or {}
        if verdict.get("verdict") in rs.FLAG_VERDICTS and rs.norm_quote(claim.get("text")) == want:
            return claim, verdict
    raise ActionNotApplicable(f"finding {finding_key!r} carries no Oracle claim")


async def _profile_json(record, db) -> dict:
    from applire.models.profile import MasterProfile

    row = await db.get(MasterProfile, record.profile_id)
    return (row.profile_json if row else None) or {}


def _work_entries(profile: dict) -> list[dict]:
    w = profile.get("work_experience")
    return [e for e in w if isinstance(e, dict)] if isinstance(w, list) else []


def _profile_excerpt(profile: dict, claim: dict) -> str:
    """What the profile states for the entry the claim stands under — the
    narrowing's source (never the system's own wording of a gap)."""
    sid = str(claim.get("source_experience_id") or "")
    lines: list[str] = []
    for e in _work_entries(profile):
        if sid and str(e.get("id") or "") != sid:
            continue
        head = ", ".join(str(e.get(k) or "") for k in ("title", "company") if e.get(k))
        lines.append(head)
        for k in ("responsibilities", "achievements"):
            lines += [f"- {x}" for x in e.get(k) or [] if isinstance(x, str)]
        if sid:
            break
    if not lines:
        summ = profile.get("professional_summary")
        if isinstance(summ, dict):
            lines += [v for v in summ.values() if isinstance(v, str) and v]
        elif isinstance(summ, str):
            lines.append(summ)
    return "\n".join(l for l in lines if l)[:4000]


async def _language(kind: Kind, record, db) -> str:
    from applire.services.review_actions import _document_language

    return await _document_language(kind, record, db)


def _locate(sections: list[tuple[str, str]], text: str):
    from applire.services.review_signals import locate_sentence

    return locate_sentence(sections, text)


def _splice(full: str, start: int, end: int, new: str) -> str:
    if new:
        return full[:start] + new + full[end:]
    # removal: the line (bullet) or sentence leaves with its separator
    before, after = full[:start], full[end:]
    if before.endswith("\n") or not before:
        after = after[1:] if after.startswith("\n") else after.lstrip(" ")
    else:
        before = before.rstrip(" ")
        if after.startswith("\n") and before.endswith("\n"):
            after = after[1:]
    return before + after


def _numbers(s: str) -> list[str]:
    return [n.replace(",", ".") for n in _NUM.findall(s or "")]


def _no_new_numbers(source: str, out: str) -> bool:
    have = set(_numbers(source))
    return all(n in have for n in _numbers(out))


def _same_numbers(a: str, b: str) -> bool:
    return sorted(_numbers(a)) == sorted(_numbers(b))


async def _edit(provider, record, kind: Kind, mode: str, sentence: str, *, language: str,
                station: str | None = None, profile_excerpt: str | None = None) -> str | None:
    """One model edit of one sentence. ``""`` = the model answered <EMPTY> (only
    for ``narrow``); None = unusable (truncated, adds a number, unchanged)."""
    from applire.exceptions import LLMTruncatedError
    from applire.prompts import preamble as _prompt_context
    from applire.prompts.review_rewrite import DELIVERY_EDIT_SYSTEM_PROMPT, build_delivery_edit_prompt
    from applire.providers.llm.debug_log import llm_log_stage
    from applire.providers.llm.usage import llm_usage_context

    prompt = build_delivery_edit_prompt(mode, sentence, language=language, station=station,
                                        profile_excerpt=profile_excerpt)
    try:
        with llm_usage_context(stage="review_delivery_action", document_kind=kind,
                               document_id=getattr(record, "id", None)), llm_log_stage("review_delivery_action"):
            out = await _prompt_context.acomplete(
                provider, f"review_delivery:{mode}", prompt,
                system=DELIVERY_EDIT_SYSTEM_PROMPT, temperature=0.2,
                max_tokens=min(2048, max(300, len(sentence) // 2 + 256)),
                disable_thinking=True,
            )
    except LLMTruncatedError:
        return None
    out = (out or "").strip().strip('"').strip()
    if out == _EMPTY:
        return "" if mode == "narrow" else None
    # a move may keep the sentence word for word (the CV relocates it); every
    # other mode must change it
    if not out or (mode != "move" and _fold(out) == _fold(sentence)):
        return None
    source = sentence + "\n" + (profile_excerpt or "") if mode == "narrow" else sentence
    if not _no_new_numbers(source, out):
        logger.info("DELIVERY_EDIT_REFUSED mode=%s reason=new_number", mode)
        return None
    return out


def _check(record, check_id: str) -> dict | None:
    for c in (record.ats_report or {}).get("checks") or []:
        if isinstance(c, dict) and c.get("id") == check_id:
            return c
    return None


def _foreign_items(kind: Kind, record, language: str) -> list[str]:
    from applire.services.ats_audit import foreign_language_items

    if kind == "cv":
        from applire.schemas.cv import TailoredCVData
        from applire.services.cv_section_editor import apply_overrides_to_tailored

        tailored = apply_overrides_to_tailored(
            TailoredCVData.model_validate(record.tailored_data or {}),
            record.content_snapshot, record.section_overrides,
        )
        return [t for w, t in foreign_language_items(tailored, language) if not w.endswith("> Branche")]
    from applire.services.cover_letter import _apply_section_overrides
    from applire.utils.language_detection import item_language_mismatch

    data = _apply_section_overrides(record.letter_data or {}, record.section_overrides or {})
    return [p for p in ((data.get("body") or {}).get("paragraphs") or [])
            if isinstance(p, str) and item_language_mismatch(p, language)]


async def _apply(kind: Kind, record, db, changes: list[dict], finding_key: str | None, *, calls: int) -> DeliveryActionResult:
    """Write every changed section (all or nothing), re-audit once, report."""
    from applire.services import review_state as rs
    from applire.services.review_actions import findings_of, reaudit, write_section

    changes = [c for c in changes if c["after"] != c["before"]]
    if not changes:
        raise ActionNotApplicable("nothing to change")
    for c in changes:
        await write_section(kind, record, c["section_id"], c["after"], db)
    await reaudit(kind, record, db)
    still = bool(finding_key) and rs.find_listed(findings_of(record), finding_key) is not None
    return DeliveryActionResult(
        record=record, report=record.ats_report, truthfulness=record.truthfulness_report,
        changes=changes, still_listed=still, llm_calls=calls,
    )
