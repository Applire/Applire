# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-Q5 (Sorbet quality-1) — a role's leadership KIND is a stored fact.

``WorkEntry.leadership_kind`` ("disciplinary" | "functional" | None) is written
ONLY from the person's answer to the dedicated leadership-kind question, through
the one op ``RecordLeadershipKind`` and the committer (vault write matrix,
arc42 §5.3.19a). No prose reader: the bullet text never sets the kind
(the adversarial re-check N1–N3 showed every reading of the prose either
promoted a mention to a fact or demoted a true form).

The fixed options (RULINGS MD-Q5):

* "Disziplinarisch (Personalverantwortung)" / "Line management" → ``disciplinary``
* "Beides" / "Both" → ``disciplinary`` (the line names the stronger, stated kind)
* "Fachliche Anleitung" / "Functional guidance" → ``functional``
* own words that are not one of the options, or a skipped question → no kind
  (the neutral line), but the role counts as asked and is not asked again.

Callers (the interview's kind ask, Q2):

* :func:`option_for_answer` maps the answer text to an option (exact option
  label only — an own-words answer is never interpreted);
* :func:`leadership_kind_op` builds the op for a ``commit_ops`` batch the caller
  already commits (one turn, one ``EnrichmentRecord``);
* :func:`record_leadership_kind` commits it on its own (the candidate's act:
  source ``interview`` / ``agent_interview``, intake ``leadership_kind_answer``).
"""

from __future__ import annotations

import unicodedata
import uuid
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from applire.schemas.profile import FieldChange, LeadershipKindRecord, MasterProfileData

Option = Literal["disciplinary", "functional", "both", "own_words", "skipped"]

#: The intake name on the ``EnrichmentRecord`` and the receipt's rationale key.
ANSWER_INTAKE = "leadership_kind_answer"
RATIONALE_RECORDED = "leadership_kind_recorded"
RATIONALE_UNKNOWN = "leadership_kind_left_open"
#: Q3-2 / Q8-2 — the size part of the same answer, when it changed the size.
RATIONALE_SIZE_RECORDED = "team_size_recorded"

#: option → the stored kind. ``both`` stores disciplinary (RULINGS MD-Q5).
KIND_OF_OPTION: dict[str, str | None] = {
    "disciplinary": "disciplinary",
    "both": "disciplinary",
    "functional": "functional",
    "own_words": None,
    "skipped": None,
}

#: The fixed option labels the question offers, per language, in order.
OPTION_LABELS: dict[str, dict[str, str]] = {
    "de": {
        "disciplinary": "Disziplinarisch (Personalverantwortung)",
        "functional": "Fachliche Anleitung",
        "both": "Beides",
    },
    "en": {
        "disciplinary": "Disciplinary (line management)",
        "functional": "Functional guidance",
        "both": "Both",
    },
}

_SOURCES = ("interview", "agent_interview", "testimony")


class LeadershipKindEntryNotFound(LookupError):
    """No single work entry carries the given id (or company + role)."""


def _norm(text: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text or "")).split()).casefold()


#: Q3-2 / Q8-2 (Sorbet wave 2b) — the size part each chip carries after its
#: option label: the stored size to confirm ("– 14 Personen"), or an open slot
#: the person completes ("– Anzahl Personen:"). Code-built, never model text.
_SIZE_SLOT = {"de": "Anzahl Personen:", "en": "number of people:"}
_PEOPLE_NOUN = {"de": ("Person", "Personen"), "en": ("person", "people")}
#: The closed set of nouns a number in the answer may carry (both languages).
_PEOPLE_NOUNS = ("personen", "person", "mitarbeitende", "mitarbeitenden", "mitarbeiter",
                 "mitarbeiterinnen", "people", "persons", "employees", "employee")
_MAX_SIZE = 9999


def size_suffix(lang: str, team_size: Any) -> str:
    """The chip's size part: " – 14 Personen" for a stored size, " – Anzahl
    Personen:" when none is stored (the person types the number)."""
    lang = "de" if str(lang or "").lower().startswith("de") else "en"
    if isinstance(team_size, int) and not isinstance(team_size, bool) and team_size > 0:
        one, many = _PEOPLE_NOUN[lang]
        return f" – {team_size} {one if team_size == 1 else many}"
    return f" – {_SIZE_SLOT[lang]}"


def _size_rest(rest: str) -> tuple[bool, int | None]:
    """What may follow an exact option label (already normalised): nothing; a
    dash/comma/colon; the size slot ("anzahl personen:"); ONE whole number with
    or without a people noun from the closed list; a closing full stop. Anything
    else → not this form (``False``)."""
    import re

    if rest[:1].isdigit():
        return False, None  # S2 (adv-2b): "Beides14" is no label plus a number
    rest = rest.strip()
    rest = re.sub(r"^[-–—,;:]\s*", "", rest)
    for slot in _SIZE_SLOT.values():
        if rest.startswith(_norm(slot)):
            rest = rest[len(_norm(slot)):].strip()
            break
        if rest.startswith(_norm(slot).rstrip(":")):
            rest = rest[len(_norm(slot).rstrip(":")):].lstrip(" :").strip()
            break
    rest = rest.rstrip(".").strip()
    if not rest:
        return True, None
    # S2 (adv-2b): ASCII digits only — "٣" or a fullwidth "１４" is no whole number here.
    m = re.fullmatch(r"([0-9]{1,4})(?:\s+(" + "|".join(_PEOPLE_NOUNS) + r"))?", rest)
    if not m:
        return False, None
    n = int(m.group(1))
    if not 1 <= n <= _MAX_SIZE:
        return False, None
    return True, n


def parse_kind_answer(text: str | None) -> tuple[Option, int | None]:
    """The option an answer names and the number it gives (Q3-2 / Q8-2).

    Structural, no word list over prose: the answer must START with an EXACT
    option label (either language, case and spacing ignored) or the option key
    itself, and what follows may only be the chip's own size part — at most one
    whole number, optionally with a people noun from a closed list. Empty →
    ``skipped``; anything else → ``own_words`` (never interpreted: no kind, no
    size; the answer is reconciled like any other)."""
    t = _norm(text)
    if not t:
        return "skipped", None
    candidates = sorted(
        ((option, _norm(label)) for labels in OPTION_LABELS.values() for option, label in labels.items()),
        key=lambda pair: -len(pair[1]),
    ) + [(key, key) for key in ("disciplinary", "functional", "both")]
    for option, label in candidates:
        if t == label:
            return option, None  # type: ignore[return-value]
        if t.startswith(label):
            ok, size = _size_rest(t[len(label):])
            if ok:
                return option, size  # type: ignore[return-value]
    if t in KIND_OF_OPTION:
        return t, None  # type: ignore[return-value]
    return "own_words", None


def option_for_answer(text: str | None) -> Option:
    """The option an answer names: an EXACT option label (either language,
    case and spacing ignored) or the option key itself, optionally followed by
    the chip's size part (:func:`parse_kind_answer`); empty → ``skipped``;
    anything else → ``own_words`` (never interpreted — the neutral line)."""
    return parse_kind_answer(text)[0]


def leadership_kind_op(
    entry_id: str = "",
    option: Option = "skipped",
    *,
    answer: str = "",
    company: str = "",
    role: str = "",
    source: str = "interview",
    team_size: int | None = None,
):
    """The ``RecordLeadershipKind`` op for one role — for a caller that commits
    it in its own ``commit_ops`` batch. ``team_size`` (Q3-2 / Q8-2): the number
    the same answer confirmed or gave (:func:`parse_kind_answer`)."""
    from applire.services.profile.reconcile.ops import RecordLeadershipKind

    return RecordLeadershipKind(
        entry_id=str(entry_id or ""), company=company or "", role=role or "",
        option=option, answer=answer or "", source=source, team_size=team_size,
    )


def find_entry(profile: MasterProfileData, entry_id: str = "", company: str = "", role: str = ""):
    """The one work entry the op addresses: by id; else, for an entry stored
    without one, the single entry with that company and role."""
    if entry_id:
        hit = next((e for e in profile.work_experience if e.id == entry_id), None)
        if hit is not None:
            return hit
    if company or role:
        hits = [
            e for e in profile.work_experience
            if _norm(e.company) == _norm(company) and _norm(e.role) == _norm(role)
        ]
        if len(hits) == 1:
            return hits[0]
    raise LeadershipKindEntryNotFound(entry_id or f"{company}|{role}")


def apply_leadership_kind_op(op, profile: MasterProfileData, changes: list):
    """The applier branch (called from ``reconcile.apply.apply_ops``): set the
    kind and the record on the one entry, receipt it, and return the entry (the
    committer's class guard lets exactly these entries keep a changed kind)."""
    entry = find_entry(profile, op.entry_id, op.company, op.role)
    kind = KIND_OF_OPTION[op.option]
    before = entry.leadership_kind
    entry.leadership_kind = kind  # type: ignore[assignment]
    size = getattr(op, "team_size", None)
    entry.leadership_kind_record = LeadershipKindRecord(
        option=op.option, answer=" ".join((op.answer or "").split()), source=op.source,
        team_size=size,
    )
    label = entry.company or entry.role or entry.id
    if size is not None and size != entry.team_size:
        # Q3-2 / Q8-2 — the number the person gave in the same answer is the
        # role's team size; the CV prints it. A confirmed number changes
        # nothing and is kept only on the record (``team_size``).
        size_before = entry.team_size
        entry.team_size = size
        changes.append(FieldChange(
            section="work_experience", field="team_size",
            action="updated" if size_before else "added",
            old_value=size_before, new_value=size,
            rationale=f"Recorded on {label!r}: the candidate gave the team size as {size}.",
            rationale_key=RATIONALE_SIZE_RECORDED,
            entity_id=entry.id,
        ))
    changes.append(FieldChange(
        section="work_experience", field="leadership_kind",
        action="updated" if before else "added",
        old_value=before, new_value=kind,
        rationale=(
            f"Recorded on {label!r}: the candidate said the team size is {kind} leadership."
            if kind else
            f"Left open on {label!r}: the candidate's answer named no kind of leadership; "
            "the CV states the team size only."
        ),
        rationale_key=RATIONALE_RECORDED if kind else RATIONALE_UNKNOWN,
        entity_id=entry.id,
    ))
    return entry


async def record_leadership_kind(
    db: AsyncSession,
    *,
    entry_id: str = "",
    option: Option,
    answer: str = "",
    company: str = "",
    role: str = "",
    source: str = "interview",
    user_id: uuid.UUID | None = None,
):
    """Commit one role's answer on its own (one ``EnrichmentRecord``, intake
    ``leadership_kind_answer``, actor ``candidate``; no grounding — the option
    is the candidate's pick, never model output). Returns the ``CommitResult``.
    Raises :class:`LeadershipKindEntryNotFound` / ``LookupError``."""
    from applire.services.profile import _get_latest
    from applire.services.profile.commit import CommitProvenance, commit_ops

    if source not in _SOURCES:
        raise ValueError(f"record_leadership_kind: unknown source {source!r}")
    record = await _get_latest(db, user_id)
    if not record:
        raise LookupError("No profile found")
    find_entry(MasterProfileData.model_validate(record.profile_json), entry_id, company, role)
    result = await commit_ops(
        db,
        [leadership_kind_op(entry_id, option, answer=answer, company=company, role=role, source=source)],
        CommitProvenance(source=source, intake=ANSWER_INTAKE, actor="candidate"),
        record=record,
        grounding=None,
        snapshot=None,
        llm_provider=None,
    )
    await db.commit()
    await db.refresh(result.record)
    return result
