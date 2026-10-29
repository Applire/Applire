# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-099 cl. 11 (UAT-1, U-9, U-11) — the profile hub's decisions: which
outcomes a dispute offers, and the four small model calls behind "Zusammen-
führen", a typed answer, the same-position question and the entry merge.

Deterministic here: the outcome SET per dispute (a fact about the field), the
fact-presence check on every model-written sentence (``merge_verifier``), the
membership check on every model answer, and the presence of a corrected value
in the candidate's own words. Judgements are the model's (ADR-062 cl. 1).
"""
from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from applire.prompts.profile_decisions import (
    ORG_FIGURE_SYSTEM,
    ASPIRATION_SYSTEM,
    PRESENT_SYSTEM,
    DUPLICATES_SYSTEM,
    IDENTITY_SYSTEM,
    MERGE_SYSTEM,
    REPLY_SYSTEM,
    build_aspiration_prompt,
    build_org_figure_prompt,
    build_present_prompt,
    build_duplicates_prompt,
    build_identity_prompt,
    build_merge_prompt,
    build_reply_prompt,
)
from applire.services.profile.merge_verifier import verify_merge

logger = logging.getLogger(__name__)

Outcome = Literal["keep_current", "take_new", "keep_both", "merge"]

#: The bullet lists a ``flag_conflict`` may name (prompt rule 13 / the
#: ``flag_conflict`` vocabulary). A dispute on one of them is a LIST conflict.
LIST_FIELDS: frozenset[str] = frozenset({"responsibilities", "achievements", "technologies"})
#: RULING A1-i1 (main, 2026-10-28; precedent F4/#73): a scalar dispute on one of
#: these identity fields of a work / volunteer ENTRY may be two different
#: entries — "keep both" creates the second one.
ENTRY_SECTIONS: frozenset[str] = frozenset({"work_experience", "volunteer_activities"})
ENTRY_IDENTITY_FIELDS: frozenset[str] = frozenset(
    {"role", "company", "organization", "start_date", "end_date"}
)

#: Outcome key → the ``ResolveField.resolution`` it writes.
RESOLUTION_FOR_OUTCOME: dict[str, str] = {
    "keep_current": "existing",
    "take_new": "incoming",
    "keep_both": "both",
    "merge": "merge",
}


def conflict_shape(section: str | None, field: str | None, entity_id: str | None) -> tuple[str, list[str]]:
    """``("list" | "scalar", allowed outcomes)`` for one dispute (ADR-099 cl. 11)."""
    if (field or "") in LIST_FIELDS:
        return "list", ["keep_current", "take_new", "keep_both", "merge"]
    if (section or "") in ENTRY_SECTIONS and entity_id and (field or "") in ENTRY_IDENTITY_FIELDS:
        return "scalar", ["keep_current", "take_new", "keep_both"]
    return "scalar", ["keep_current", "take_new"]


def _norm(text: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).split()).casefold()


def _sentence_or_none(raw: Any) -> str | None:
    """A model's sentence, or ``None`` — including the literal string "null"
    that gpt-6-luna returns for a JSON null (2/45 proposals, 2026-10-28)."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text.casefold() in {"null", "none", "n/a"}:
        return None
    return text


def _stage(label: str):
    from applire.providers.llm.debug_log import llm_log_stage

    return llm_log_stage(label)


# ── "Zusammenführen" ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MergeProposal:
    proposal: str | None
    verifier: Literal["passed", "failed"]
    sources: tuple[str, str]


async def propose_merge(provider, existing: str, incoming: str, entry_label: str | None = None) -> MergeProposal:
    """ONE model call; the verifier decides whether its sentence may be offered.
    Never retried (adv-decision C3: fail closed → the other three outcomes)."""
    sources = (str(existing or ""), str(incoming or ""))
    proposal: str | None = None
    try:
        with _stage("profile_merge_proposal"):
            data = await provider.aparse_json(
                build_merge_prompt(sources[0], sources[1], entry_label),
                system=MERGE_SYSTEM,
                temperature=0.1,
                max_tokens=600,
            )
        raw = data.get("merged") if isinstance(data, dict) else None
        proposal = _sentence_or_none(raw)
    except Exception:  # noqa: BLE001 — a failed call is a failed proposal, never a 500
        logger.warning("propose_merge: model call failed; offering no merge", exc_info=True)
        proposal = None
    if proposal is not None and verify_merge(proposal, sources):
        return MergeProposal(proposal, "passed", sources)
    if proposal is not None:
        logger.info("propose_merge: verifier rejected the proposal (fact not in either source)")
    return MergeProposal(None, "failed", sources)


# ── typed answer on a dispute ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ReplyReading:
    outcome: str | None = None
    corrected_value: str | None = None
    follow_up: str | None = None


_DEFAULT_FOLLOW_UP = {
    "de": "Ich habe deine Antwort nicht eindeutig zuordnen können. Bitte wähle eine der Optionen unten.",
    "en": "I could not tell which option you mean. Please pick one of the options below.",
}


def default_follow_up(lang: str) -> str:
    return _DEFAULT_FOLLOW_UP.get(lang, _DEFAULT_FOLLOW_UP["en"])


async def interpret_reply(
    provider,
    *,
    reply: str,
    entry_label: str | None,
    field: str,
    existing: Any,
    incoming: Any,
    outcomes: list[str],
    lang: str = "en",
) -> ReplyReading:
    """The model reads the reply; code keeps only an answer it can verify:
    an outcome from the OFFERED set, or a corrected value written in the
    candidate's own reply. Anything else becomes the one follow-up."""
    try:
        with _stage("profile_conflict_reply"):
            data = await provider.aparse_json(
                build_reply_prompt(
                    entry_label=entry_label, field=field, existing=existing,
                    incoming=incoming, outcomes=list(outcomes), reply=reply,
                ),
                system=REPLY_SYSTEM,
                temperature=0.1,
                max_tokens=400,
            )
    except Exception:  # noqa: BLE001
        logger.warning("interpret_reply: model call failed; asking the follow-up", exc_info=True)
        data = {}
    if not isinstance(data, dict):
        data = {}
    outcome = data.get("outcome")
    if isinstance(outcome, str) and outcome in outcomes:
        return ReplyReading(outcome=outcome)
    corrected = data.get("corrected_value")
    if isinstance(corrected, str) and corrected.strip():
        value = corrected.strip()
        # A FACT (ADR-062 cl. 1): the value is written in the candidate's own
        # reply. A value the model composed is never written (RULING A1-1 A).
        if _norm(value) and _norm(value) in _norm(reply):
            return ReplyReading(corrected_value=value)
        logger.info("interpret_reply: corrected value not in the reply's own words; follow-up")
    follow = data.get("follow_up")
    if isinstance(follow, str) and follow.strip():
        return ReplyReading(follow_up=follow.strip())
    return ReplyReading(follow_up=default_follow_up(lang))


# ── U-9: the same position under two names ───────────────────────────────────

Verdict = Literal["same", "different", "unsure"]


async def judge_identity(provider, pairs: list[dict[str, Any]]) -> dict[str, Verdict]:
    """One model call over every candidate pair; ``{}`` on any failure (the hub
    then asks the candidate — an unjudged pair is shown, never hidden)."""
    if not pairs:
        return {}
    try:
        with _stage("profile_entry_identity"):
            data = await provider.aparse_json(
                build_identity_prompt(pairs),
                system=IDENTITY_SYSTEM,
                temperature=0.0,
                max_tokens=200 + 40 * len(pairs),
            )
    except Exception:  # noqa: BLE001
        logger.warning("judge_identity: model call failed; pairs stay unjudged", exc_info=True)
        return {}
    wanted = {str(p.get("pair_id")) for p in pairs}
    out: dict[str, Verdict] = {}
    for row in (data.get("verdicts") if isinstance(data, dict) else None) or []:
        if not isinstance(row, dict):
            continue
        pid, verdict = str(row.get("pair_id")), row.get("verdict")
        if pid in wanted and verdict in ("same", "different", "unsure"):
            out[pid] = verdict  # type: ignore[assignment]
    return out


# ── U-11: duplicate bullets between two entries ──────────────────────────────


@dataclass(frozen=True)
class DuplicatePair:
    target_index: int
    source_index: int
    proposal: str | None


async def find_duplicate_pairs(
    provider, target_bullets: list[str], source_bullets: list[str]
) -> list[DuplicatePair]:
    """The model names the pairs; each proposal passes the verifier or becomes
    ``None``. Indices out of range and repeated bullets are dropped."""
    if not target_bullets or not source_bullets:
        return []
    try:
        with _stage("profile_entry_duplicates"):
            data = await provider.aparse_json(
                build_duplicates_prompt(target_bullets, source_bullets),
                system=DUPLICATES_SYSTEM,
                temperature=0.1,
                max_tokens=400 + 200 * min(len(target_bullets), len(source_bullets)),
            )
    except Exception:  # noqa: BLE001
        logger.warning("find_duplicate_pairs: model call failed; no pairs offered", exc_info=True)
        return []
    pairs: list[DuplicatePair] = []
    used_t: set[int] = set()
    used_s: set[int] = set()
    for row in (data.get("pairs") if isinstance(data, dict) else None) or []:
        if not isinstance(row, dict):
            continue
        try:
            ti = int(str(row.get("t", "")).lstrip("Tt")) - 1
            si = int(str(row.get("s", "")).lstrip("Ss")) - 1
        except ValueError:
            continue
        if not (0 <= ti < len(target_bullets) and 0 <= si < len(source_bullets)):
            continue
        if ti in used_t or si in used_s:
            continue
        used_t.add(ti)
        used_s.add(si)
        merged = _sentence_or_none(row.get("merged"))
        if merged is not None and not verify_merge(merged, (target_bullets[ti], source_bullets[si])):
            merged = None
        pairs.append(DuplicatePair(ti, si, merged))
    return pairs


# ── RULING B-1 (U-7): a current role's goal/task-worded bullet ────────────────


async def judge_aspirations(provider, bullets: list[str]) -> set[int] | None:
    """ONE model call: the indices of ``bullets`` worded as a goal or a task
    still to be done. ``None`` on any failure (nothing is asked then — the
    question is a courtesy, never a gate). The judgement is the model's
    (ADR-062 cl. 1); code keeps only in-range indices."""
    if not bullets:
        return set()
    try:
        with _stage("profile_aspiration"):
            data = await provider.aparse_json(
                build_aspiration_prompt(bullets),
                system=ASPIRATION_SYSTEM,
                temperature=0.0,
                max_tokens=200 + 8 * len(bullets),
            )
    except Exception:  # noqa: BLE001
        logger.warning("judge_aspirations: model call failed; nothing asked", exc_info=True)
        return None
    out: set[int] = set()
    for n in (data.get("items") if isinstance(data, dict) else None) or []:
        try:
            i = int(n) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(bullets):
            out.add(i)
    return out


# ── D-2: a not-applied import item that is already in the vault ───────────────


async def judge_already_present(
    provider,
    items: list[dict[str, Any]],
    existing: dict[str, list[dict[str, Any]]],
    *,
    system: str = PRESENT_SYSTEM,
    stage: str = "profile_not_applied_present",
    also_in: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, str] | None:
    """ONE model call: ``{item id: existing entry id}`` for every not-applied
    item the model judges to be an entry the vault already holds under another
    name. Code keeps only ids it offered for that item's own section; ``None``
    on any failure (nothing is hidden then — the item stays asked).

    D-5 reuses it for certificates an import has just added (``system`` =
    ``CERT_SAME_SYSTEM``, its own stage). D-2b: ``also_in`` names the further
    sections whose ids an item of a section may match (a skill item that is a
    held certificate)."""
    if not items:
        return {}
    try:
        with _stage(stage):
            data = await provider.aparse_json(
                build_present_prompt(items, existing),
                system=system,
                temperature=0.0,
                max_tokens=200 + 40 * len(items),
            )
    except Exception:  # noqa: BLE001
        logger.warning("judge_already_present: model call failed; items stay asked", exc_info=True)
        return None
    section_of = {str(i["item"]): i["section"] for i in items}
    offered = {sec: {str(e["id"]) for e in rows} for sec, rows in existing.items()}
    out: dict[str, str] = {}
    for row in (data.get("matches") if isinstance(data, dict) else None) or []:
        if not isinstance(row, dict):
            continue
        item, match = str(row.get("item")), row.get("existing")
        if item not in section_of or not isinstance(match, str):
            continue
        sections = (section_of[item], *((also_in or {}).get(section_of[item], ())))
        if any(match in offered.get(sec, set()) for sec in sections):
            out[item] = match
    return out


# ── FIG-2: an organisation-scale figure on a bullet ──────────────────────────


async def judge_org_figures(provider, bullets: list[str]) -> dict[int, str] | None:
    """ONE model call: ``{bullet index: the organisation figure as the bullet
    writes it}``. ``None`` on any failure (nothing is asked then). The judgement
    is the model's (ADR-062 cl. 1); code keeps only in-range indices whose
    copied words stand in the bullet and carry a figure (a FACT)."""
    from applire.services.load_bearing import bullet_carries_figure

    if not bullets:
        return {}
    try:
        with _stage("profile_org_figure"):
            data = await provider.aparse_json(
                build_org_figure_prompt(bullets),
                system=ORG_FIGURE_SYSTEM,
                temperature=0.0,
                max_tokens=200 + 24 * len(bullets),
            )
    except Exception:  # noqa: BLE001
        logger.warning("judge_org_figures: model call failed; nothing asked", exc_info=True)
        return None
    out: dict[int, str] = {}
    for item in (data.get("items") if isinstance(data, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        try:
            i = int(item.get("n")) - 1
        except (TypeError, ValueError):
            continue
        figure = " ".join(str(item.get("figure") or "").split()).strip(" .,;")
        if not (0 <= i < len(bullets)) or i in out or not figure:
            continue
        if figure.casefold() not in " ".join(bullets[i].split()).casefold():
            continue
        if not bullet_carries_figure(figure):
            continue
        out[i] = figure
    return out

