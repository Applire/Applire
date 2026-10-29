# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FIG-2 (founder ruling, Hazelnut build 5): "Gehört „€19 Mrd. Umsatz 2021“ in
Deine Unterlagen?" Some bullets carry a figure that measures the ORGANISATION
(the employer's revenue, headcount, market size, funding), not the person's own
work. The founder ruled that "this is something the user can decide", asked
once, in the profile hub.

Three owners, kept apart (ADR-062):

* **Candidate selection is code.** A bullet (responsibility, achievement or a
  project description sentence) of any work entry or project that carries a
  figure (``load_bearing.bullet_carries_figure``, a FACT) and that no verdict
  has judged yet.
* **The judgement is the model's.** ONE ``judge_org_figures`` call at the end
  of a CV import, persisted as ``RecordOrgFigureVerdicts`` in the import's
  commit. Negatives are persisted too, so a bullet is judged once. Code keeps
  only a figure the model copied from its own bullet that still carries a
  figure.
* **The answer is the candidate's.** The hub asks the ``org_figure`` thread
  (class A, resolver ``ask_org_figure``), ONCE per figure. The figure's
  canonical key (``figure_key``) joins its tellings across bullets and
  entries: "€19 billion revenue impact" on the role and "€19 billion revenue in
  2021" on its project are one question. The answer is written as an
  ``OrgFigureDecision`` on EVERY entry carrying the figure, with ``relation``
  verbatim. The decision is entry data, like ``not_yet_reached``: it moves with
  a merged entry and stays when the profile is re-imported.

A failed judge call writes no verdicts. Then nothing is asked (the question is
a courtesy, never a gate), and the next import judges again.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from applire.schemas.profile import MasterProfileData, MasterProfileResponse, ProjectEntry
from applire.services.profile.aspiration import Entry, _bullets, aspiration_key

logger = logging.getLogger(__name__)

ANSWER_INTAKE = "org_figure_answer"
ISSUE_PREFIX = "org_figure:"
RATIONALE_INCLUDE = "org_figure_include"
RATIONALE_OMIT = "org_figure_omit"


class OrgFigureNotFound(LookupError):
    """No open ``org_figure`` issue with that id (unknown, or the bullet is gone)."""


class OrgFigureAnswered(Exception):
    """The figure was already decided."""


class OrgFigureStale(Exception):
    """The id is well-formed but names no open question any more (the bullet
    was edited away). The hub treats it like an answered one (409)."""


class OrgFigureRelationRequired(ValueError):
    """``include`` without the candidate's relation to the figure."""


def figure_key(figure: str) -> str:
    """The figure's canonical key: its first non-year figure as
    ``<kind>-<value>`` (the shared extractor, a FACT), so "€19 billion" and
    "19 Mrd. €" are one question. Falls back to the normalised words."""
    from applire.services.oracle.matchers.figures import extract_figures

    for f in extract_figures(figure or ""):
        if f.kind != "year":
            return f"{f.kind}-{f.value}"
    return "text-" + aspiration_key(figure or "")


def issue_id(entry_id: str, key: str) -> str:
    return f"{ISSUE_PREFIX}{entry_id}:{key}"


def parse_issue_id(value: str) -> tuple[str, str]:
    if not value.startswith(ISSUE_PREFIX):
        raise OrgFigureNotFound(value)
    entry_id, _, key = value[len(ISSUE_PREFIX):].rpartition(":")
    if not entry_id or not key:
        raise OrgFigureNotFound(value)
    return entry_id, key


def _entries(profile: MasterProfileData) -> list[tuple[str, Entry]]:
    return [("work_experience", w) for w in profile.work_experience or []] + [
        ("projects", p) for p in profile.projects or []
    ]


def entry_label(entry: Entry) -> str:
    """"Role @ Company" for a position, the project's name for a project."""
    if isinstance(entry, ProjectEntry):
        return entry.name
    return " @ ".join(x for x in ((entry.role or "").strip(), (entry.company or "").strip()) if x)


def unjudged_bullets(profile: MasterProfileData) -> list[tuple[str, str, str, str]]:
    """``(section, entry_id, key, text)`` of every figure-carrying bullet no
    verdict judged."""
    from applire.services.load_bearing import bullet_carries_figure

    meta = profile.metadata
    judged = {v.key for v in (meta.org_figure_verdicts if meta else [])}
    out: list[tuple[str, str, str, str]] = []
    for section, entry in _entries(profile):
        for text in _bullets(entry):
            k = aspiration_key(text)
            if k not in judged and bullet_carries_figure(text):
                judged.add(k)
                out.append((section, entry.id, k, text))
    return out


def _answered(profile: MasterProfileData) -> set[str]:
    return {d.key for _s, e in _entries(profile) for d in e.org_figure_decisions}


def holders(profile: MasterProfileData, key: str) -> list[tuple[str, Entry, str, str]]:
    """``(section, entry, bullet, figure)`` of every entry whose standing bullet
    the model flagged with the figure ``key``, in profile order."""
    meta = profile.metadata
    flagged = {
        v.key: v.figure for v in (meta.org_figure_verdicts if meta else [])
        if v.figure and v.figure_key == key
    }
    out: list[tuple[str, Entry, str, str]] = []
    for section, entry in _entries(profile):
        for text in _bullets(entry):
            figure = flagged.get(aspiration_key(text))
            if figure:
                out.append((section, entry, text, figure))
    return out


def open_org_figures(profile: MasterProfileData) -> list[tuple[str, Entry, str, str, str]]:
    """``(section, entry, figure_key, bullet, figure)`` — ONE per figure the
    model flagged that still stands on a bullet and is undecided; the entry is
    the first one carrying it."""
    meta = profile.metadata
    if meta is None or not meta.org_figure_verdicts:
        return []
    answered = _answered(profile)
    out: list[tuple[str, Entry, str, str, str]] = []
    for key in dict.fromkeys(v.figure_key for v in meta.org_figure_verdicts if v.figure and v.figure_key):
        if key in answered:
            continue
        found = holders(profile, key)
        if found:
            section, entry, text, figure = found[0]
            out.append((section, entry, key, text, figure))
    return out


async def org_figure_verdict_ops(profile: MasterProfileData, provider) -> list:
    """The import-end judgement (one model call) as a ``RecordOrgFigureVerdicts``
    op for the import's own commit; ``[]`` on no candidates or any failure."""
    try:
        from applire.services.profile.decisions import judge_org_figures
        from applire.services.profile.reconcile.ops import OrgFigureVerdictItem, RecordOrgFigureVerdicts

        candidates = unjudged_bullets(profile)
        if not candidates:
            return []
        found = await judge_org_figures(provider, [t for _, _, _, t in candidates])
        if found is None:
            return []
        items = [
            OrgFigureVerdictItem(
                section=sec, entry_id=eid, key=k, text=t,
                figure=found.get(i), figure_key=figure_key(found[i]) if i in found else None,
            )
            for i, (sec, eid, k, t) in enumerate(candidates)
        ]
        return [RecordOrgFigureVerdicts(verdicts=items)]
    except Exception:  # noqa: BLE001 — a judge failure must never fail the import
        logger.warning("org-figure verdicts skipped", exc_info=True)
        return []


def find_open_org_figure(profile: MasterProfileData, entry_id: str, key: str) -> None:
    """Raise unless ``(entry_id, key)`` names an open question (one predicate for
    the service pre-check and the applier, ADR-066)."""
    if key in _answered(profile):
        raise OrgFigureAnswered(key)
    for _section, entry, k, _text, _figure in open_org_figures(profile):
        if k == key and any(e.id == entry_id for _s, e, _t, _f in holders(profile, key)):
            return
    raise OrgFigureStale(key)


def apply_org_figure_op(op, profile: MasterProfileData, changes: list) -> None:
    from applire.schemas.profile import FieldChange, OrgFigureDecision, OrgFigureVerdict, ProfileMetadata

    if profile.metadata is None:
        profile.metadata = ProfileMetadata()
    meta = profile.metadata
    if op.op == "record_org_figure_verdicts":
        judged = {(v.entry_id, v.key) for v in meta.org_figure_verdicts}
        for item in op.verdicts:
            if (item.entry_id, item.key) in judged:
                continue
            judged.add((item.entry_id, item.key))
            meta.org_figure_verdicts.append(OrgFigureVerdict(**item.model_dump()))
        return

    find_open_org_figure(profile, op.entry_id, op.key)
    # FIG-2: the relation is the candidate's own words, kept verbatim (only the
    # outer whitespace goes); a paraphrase is not a source.
    relation = (op.relation or "").strip() or None
    if op.include and relation is None:
        raise OrgFigureRelationRequired(op.key)
    for section, entry, text, figure in holders(profile, op.key):
        if any(d.key == op.key and d.text == text for d in entry.org_figure_decisions):
            continue
        entry.org_figure_decisions.append(OrgFigureDecision(
            figure=figure, include=op.include, relation=relation if op.include else None,
            text=text, key=op.key,
        ))
        label = entry_label(entry)
        changes.append(FieldChange(
            section=section, field="org_figure_decisions", action="added",
            old_value=None, new_value=figure,
            rationale=(
                f"{figure!r} on {label!r}: the candidate keeps it, with their relation to it."
                if op.include else
                f"{figure!r} on {label!r}: the candidate leaves it out of the documents."
            ),
            rationale_key=RATIONALE_INCLUDE if op.include else RATIONALE_OMIT,
            entity_id=entry.id,
        ))


async def answer_org_figure(
    db: AsyncSession,
    *,
    issue_id: str,
    include: bool,
    relation: str | None,
    user_id: uuid.UUID | None = None,
) -> MasterProfileResponse:
    """The hub's answer — one ``AnswerOrgFigure`` op through the committer
    (``manual_edit``, no grounding, no snapshot)."""
    from applire.services.profile import _get_latest, _to_response
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import AnswerOrgFigure

    entry_id, key = parse_issue_id(issue_id)
    if include and not (relation or "").strip():
        raise OrgFigureRelationRequired(key)
    record = await _get_latest(db, user_id)
    if not record:
        raise LookupError("No profile found")
    find_open_org_figure(MasterProfileData.model_validate(record.profile_json), entry_id, key)
    result = await commit_ops(
        db,
        [AnswerOrgFigure(entry_id=entry_id, key=key, include=include, relation=relation)],
        CommitProvenance(source="manual_edit", intake=ANSWER_INTAKE, actor="candidate"),
        record=record,
        grounding=None,
        snapshot=None,
        llm_provider=None,
    )
    await db.commit()
    await db.refresh(result.record)
    return _to_response(result.record)
