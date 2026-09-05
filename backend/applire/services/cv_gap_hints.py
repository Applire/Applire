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

"""Gap hints = Keyword Ledger × live document coverage (ADR-019/ADR-048, #117).

Every hint is derived at read time from two orthogonal axes:

- **evidence** (ledger ``status``/``claimable`` — owned by gap analysis; only
  profile enrichment changes it), which decides the hint *kind* and therefore
  the CTA the UI offers;
- **coverage** (surface-form presence in the current document text — the same
  normalised-substring predicate the ATS audit uses), which decides whether a
  hint shows at all.

Covered entries never hint: a claimable keyword already in the document is
done, and an honest-gap keyword in the document is the ATS panel's
truthfulness warning, not a section hint. Coverage is NEVER persisted into
the gap analysis. Pre-ledger analyses (``keyword_ledger`` NULL/empty) fall
back to the category_b/c labels, read-only, with the same coverage filter.
"""
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from applire.prompts.review_severity import SEVERITY_BLOCKING
from applire.schemas.cv_sections import GapHintItem
from applire.services.ats_audit import _norm, surface_present
from applire.services.cv_gap_mapper import map_gaps_to_sections
from applire.services.keyword_ledger import is_scope_entry
from applire.services.review_issues import ReviewIssue


@dataclass(frozen=True)
class _Candidate:
    label: str
    kind: str                 # "claimable" | "honest"
    surface_forms: tuple[str, ...]


def _candidates(
    ledger: list[dict[str, Any]] | None,
    category_b: list[str],
    category_c: list[str],
) -> list[_Candidate]:
    """Hint candidates on the evidence axis (coverage not yet applied)."""
    out: list[_Candidate] = []
    seen: set[str] = set()

    if ledger:
        for entry in ledger:
            concept = (entry.get("concept") or "").strip()
            if not concept or _norm(concept) in seen:
                continue
            # ADR-069: a scope entry's concept is a synthesised bar label
            # ("Führungsspanne ~120 MA") carrying the JD's own figure — never
            # a chip inviting the candidate to type it into their document
            # (2026-08-01 adversarial pass finding #2).
            if is_scope_entry(entry):
                continue
            # keyword-only entries (fit_weight 0) never hint in sections —
            # parity with category_b/c; US204 routes them via the ATS panel.
            if not (entry.get("fit_weight") or 0.0):
                continue
            claimable = bool(entry.get("claimable"))
            status = entry.get("status")
            if not claimable and status != "gap":
                continue  # defensive: unknown status without claim support
            forms = tuple(f for f in (entry.get("surface_forms") or []) if f) or (concept,)
            seen.add(_norm(concept))
            out.append(_Candidate(
                label=concept,
                kind="claimable" if claimable else "honest",
                surface_forms=forms,
            ))
        return out

    # Legacy fallback: pre-ledger analyses only carry flat labels.
    for label, kind in [*((g, "claimable") for g in category_b),
                        *((g, "honest") for g in category_c)]:
        label = (label or "").strip()
        if label and _norm(label) not in seen:
            seen.add(_norm(label))
            out.append(_Candidate(label=label, kind=kind, surface_forms=(label,)))
    return out


def _covered(candidate: _Candidate, document_norm: str) -> bool:
    # US212 (#122): coverage judged by THE shared presence predicate (ats_audit),
    # morphological fold included — hints and panel can never disagree.
    return any(surface_present(form, document_norm) for form in candidate.surface_forms)


def _document_norm(section_contents: dict[str, str]) -> str:
    return _norm("\n".join(section_contents.values()))


def _merge_cluster_duplicates(
    open_candidates: list[_Candidate],
    gap_clusters: list[dict[str, Any]] | None,
) -> list[_Candidate]:
    """Collapse near-duplicate concepts the gap clusters already group (#111).

    The ledger's deterministic prefix/mirror collapse can't see that "Azure"
    and "Cloud qualification" are one gap — but the semantic clusters can. Two
    or more open candidates whose labels are members of the same cluster merge
    into ONE hint labelled by the cluster; a lone member keeps its own concept
    label. The merged hint is honest if ANY member is honest (a claim must
    never cover an honest half), and carries the union of surface forms.
    """
    if not gap_clusters:
        return open_candidates

    member_to_cluster: dict[str, dict[str, Any]] = {}
    for cluster in gap_clusters:
        for member in cluster.get("gaps") or []:
            member_to_cluster.setdefault(_norm(str(member)), cluster)

    grouped: dict[str, list[_Candidate]] = {}
    order: list[tuple[str, _Candidate | None]] = []  # (cluster_id, single) preserving position
    for cand in open_candidates:
        cluster = member_to_cluster.get(_norm(cand.label))
        if cluster is None:
            order.append(("", cand))
            continue
        cid = str(cluster.get("id") or cluster.get("label"))
        if cid not in grouped:
            order.append((cid, None))
        grouped.setdefault(cid, []).append(cand)

    clusters_by_id = {
        str(c.get("id") or c.get("label")): c for c in gap_clusters
    }
    out: list[_Candidate] = []
    for cid, single in order:
        if single is not None:
            out.append(single)
            continue
        members = grouped[cid]
        if len(members) == 1:
            out.append(members[0])
            continue
        cluster = clusters_by_id[cid]
        label = str(cluster.get("label") or members[0].label)
        kind = "honest" if any(m.kind == "honest" for m in members) else "claimable"
        forms = tuple(dict.fromkeys(f for m in members for f in m.surface_forms))
        out.append(_Candidate(label=label, kind=kind, surface_forms=forms))
    return out


def build_gap_hints(
    ledger: list[dict[str, Any]] | None,
    category_b: list[str],
    category_c: list[str],
    section_contents: dict[str, str],
    gap_clusters: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, list[GapHintItem]], list[GapHintItem]]:
    """Return (section_id -> hints, general hints) for the current document.

    Coverage is document-wide: a keyword present in ANY section suppresses its
    hint everywhere. Placement of the surviving hints reuses the deterministic
    token-overlap mapper (zero overlap -> general bucket).
    """
    doc = _document_norm(section_contents)
    open_candidates = [c for c in _candidates(ledger, category_b, category_c)
                       if not _covered(c, doc)]
    open_candidates = _merge_cluster_duplicates(open_candidates, gap_clusters)
    if not open_candidates:
        return {}, []

    by_label = {c.label: c for c in open_candidates}
    raw_map = map_gaps_to_sections(list(by_label.keys()), section_contents)

    def _items(labels: list[str]) -> list[GapHintItem]:
        return [GapHintItem(id=lbl, label=lbl, kind=by_label[lbl].kind) for lbl in labels]

    gap_map = {sid: _items(labels) for sid, labels in raw_map.items() if sid != "__general__"}
    return gap_map, _items(raw_map.get("__general__", []))


def resolved_gap_hints(
    ledger: list[dict[str, Any]] | None,
    category_b: list[str],
    category_c: list[str],
    contents_before: dict[str, str],
    contents_after: dict[str, str],
) -> list[str]:
    """Hint ids that an edit just covered (uncovered before, covered after).

    Purely informational for the UI — nothing is written back to the gap
    analysis (the evidence axis only moves via profile enrichment).
    """
    before = _document_norm(contents_before)
    after = _document_norm(contents_after)
    return [
        c.label
        for c in _candidates(ledger, category_b, category_c)
        if not _covered(c, before) and _covered(c, after)
    ]


# ══════════════════════════════════════════════════════════════════════════════
# ADR-076 clause 5 (#542) — under-claiming as a bound signal
# ══════════════════════════════════════════════════════════════════════════════
#
# The honesty machinery binds one way. Five mechanisms guard over-claiming; for the
# opposite direction — evidence present in the vault, absent from the delivered
# document — four instruments read the coverage substrate and NONE of them can see the
# class ADR-076 clause 5 names:
#
#   verified_missing_claimable      scans the WHOLE serialised draft (`skills` and
#                                   `summary` included) → a bare tag ENDS the demand
#   rank_gate_missing_claimable     the same list, split by fit_weight
#   verified_missing_load_bearing   narrative-scoped, but only `claimable` + `direct` +
#                                   evidence-carries-a-figure — the load-bearing minority
#   build_gap_hints (above)         covered ⇒ no hint, by construction
#
# So a claimable, JD-required concept that reaches the document ONLY as a skills chip
# passes every one of them. Clause 5's own sentence — *satisfied only by narrative or
# bullet content, never by adding a bare skill tag* — has had no instrument for the
# non-load-bearing majority. #315's root-cause note says exactly this one class down:
# "Budgetverantwortung" satisfied the whole-document scan from the first draft while
# its "6 Mio. €" bullet was silently dropped, and two blind reviewers scored the
# requirement unmet.
#
# ADR-062 classification: **fact**. Set membership of surface forms over a scoped string
# corpus, through THE shared presence predicate (`ats_audit.surface_present`) every other
# coverage reader uses. Nothing here reads prose for meaning; whether the concept is
# worth a bullet, and how to write one, stays the corrector's judgement.

#: At most this many concepts are demanded of the corrector per round. Copied from the
#: letter reviewer's own check-5 bound ("DEMAND AT MOST TWO terms per round"), which
#: exists for the reason that applies here unchanged: #525's letter loop exhausted 5/5
#: demanding two new keywords per round while the corrector's insertions displaced
#: earlier ones. Terms beyond the cap stay eligible next round and are reported at the
#: send seat regardless (ADR-039 check `narrative-evidence`).
UNDERCLAIM_ISSUE_LIMIT = 2


@dataclass(frozen=True)
class UnderclaimedConcept:
    """A claimable, JD-required concept the delivered document does not evidence.

    ``tag_only`` distinguishes the two sub-classes, and they are deliberately NOT
    merged into one number: ``True`` means the document already CLAIMS the concept
    somewhere (a skills chip, a summary word) and shows nothing behind it — the class
    no other instrument sees, and the one where adding evidence cannot manufacture an
    over-claim because the claim is already made. ``False`` means it is absent
    altogether — the class the VERIFIED COVERAGE CHECK block already puts in front of
    the reviewer, carried here so the CORRECTOR hears it too rather than depending on
    the reviewer echoing it (ADR-083 measured that echo at 2/5).
    """

    concept: str
    evidence: str
    fit_weight: float
    surface_forms: tuple[str, ...]
    tag_only: bool


def narrative_corpus_view(draft: dict[str, Any] | None) -> dict[str, Any]:
    """Adapt either document shape to the one ``keyword_ledger._tailored_narrative_texts``
    understands, without duplicating its corpus rule (ADR-066).

    The writer's response schema names the list ``work`` (``prompts/cv_tailoring.py``);
    ``TailoredCVData`` names it ``work_history``. The shared helper reads
    ``work_history`` only, so a signal handed the loop's PROSE draft would see an EMPTY
    narrative corpus and report every claimable concept as missing — a control firing on
    everything is as useless as one firing on nothing. This adapter is the single place
    that difference is handled for this signal.

    (The same shape difference makes ``keyword_ledger.cv_coverage_budget``'s ``measure``
    return 0 on the CV drafting loop's prose draft — reported, not fixed here: it is
    ADR-076 clause 6 / #543 territory and changing it changes prompt-effect behaviour
    that owes its own real-run evidence.)
    """
    if not draft:
        return {"work_history": []}
    entries = draft.get("work_history")
    if entries is None:
        entries = draft.get("work")
    return {"work_history": entries or []}


def _underclaim_candidates(keyword_ledger: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The entry universe, filtered exactly as ``_coverage_split`` filters it.

    Same exclusions, same reasons, so this signal can never demand something the
    coverage gate has already ruled out: honest gaps (they must stay absent), an
    ADJACENT ``partial`` (ADR-048 amended 2026-07-27 — the candidate does not hold the
    JD's term, so demanding it literally is a demand to over-claim), and an ADR-069
    scope entry (its concept embeds the JD's own figure).
    """
    from applire.services.keyword_ledger import is_positioning_only

    return [
        e
        for e in (keyword_ledger or [])
        if e.get("claimable")
        and not is_positioning_only(e)
        and not is_scope_entry(e)
        and (e.get("concept") or "").strip()
    ]


def _entry_forms(entry: dict[str, Any]) -> tuple[str, ...]:
    forms = [f for f in (entry.get("surface_forms") or []) if f]
    concept = (entry.get("concept") or "").strip()
    if concept:
        forms.append(concept)
    return tuple(dict.fromkeys(forms))


def verified_narrative_underclaim(
    draft: dict[str, Any] | None,
    keyword_ledger: list[dict[str, Any]] | None,
    *,
    min_fit_weight: float | None = None,
) -> list[UnderclaimedConcept]:
    """Claimable, JD-required concepts absent from the document's NARRATIVE corpus.

    Corpus: work-entry bullets + nested project bullets (via
    ``keyword_ledger._tailored_narrative_texts`` through :func:`narrative_corpus_view`)
    — the same corpus ``cv_coverage_budget`` measures occupancy in, so the demand and
    the budget read one definition of "narrative space".

    Rank filter: ``fit_weight >= REQUIRED_WEIGHT`` (the JD's own stated requirements),
    applied **unconditionally** — unlike ADR-076 clause 6's gate, which only engages
    ``under_pressure``. A bullet list has a hard per-role ceiling
    (``RoleBudget.max_bullets``) whether or not the page budget currently binds, so
    narrative space is always scarce, and a below-rank concept living only as a skills
    tag is a legal outcome rather than a finding.

    Ordered by ``fit_weight`` descending, ledger order breaking ties, so a caller taking
    the top K takes the K most central to the role.
    """
    from applire.services.keyword_ledger import REQUIRED_WEIGHT, _draft_strings, _tailored_narrative_texts

    bar = REQUIRED_WEIGHT if min_fit_weight is None else min_fit_weight
    candidates = [e for e in _underclaim_candidates(keyword_ledger) if (e.get("fit_weight") or 0.0) >= bar]
    if not candidates:
        return []

    narrative_norm = _norm("\n".join(_tailored_narrative_texts(narrative_corpus_view(draft))))
    document_norm = _norm("\n".join(_draft_strings(draft or {})))

    out: list[UnderclaimedConcept] = []
    for index, entry in enumerate(candidates):
        forms = _entry_forms(entry)
        if any(surface_present(f, narrative_norm) for f in forms):
            continue
        out.append(
            UnderclaimedConcept(
                concept=(entry.get("concept") or "").strip(),
                evidence=str(entry.get("evidence") or ""),
                fit_weight=float(entry.get("fit_weight") or 0.0),
                surface_forms=forms,
                tag_only=any(surface_present(f, document_norm) for f in forms),
            )
        )
    return sorted(
        out,
        key=lambda c: (-c.fit_weight, [x.concept for x in out].index(c.concept)),
    )


def _issue_text(concept: UnderclaimedConcept) -> str:
    """The demand, written for the CORRECTOR's audience (ADR-083 clause 4).

    ``corrector_feedback.render_blocking_issues`` prefixes each line with ``- Fix: ``,
    so this completes an instruction rather than reporting a measurement — the
    2026-08-26 precedent where a WRITER-audience block reached the corrector with the
    wrong imperative and defeated the reviewer's own feedback.

    Two sentences are load-bearing and neither is decoration. The skills-list refusal is
    clause 5's own wording and the reason this signal does not re-open #250's
    keyword-stuffing door. The grounding sentence is the house rule (ADR-048 §8,
    repeated verbatim in both reviewer prompts): without it a deterministic demand for
    a term could push the corrector into stretching the evidence, which is the
    over-claim this whole direction exists to avoid making worse.
    """
    where = (
        f'the CV claims "{concept.concept}" but no work-entry or project bullet shows it'
        if concept.tag_only
        else f'the CV does not carry "{concept.concept}" in any work-entry or project bullet'
    )
    evidence = f' The candidate\'s profile evidence for it: "{concept.evidence}".' if concept.evidence else ""
    return (
        f"{where} — a hiring reviewer reads this requirement as unmet.{evidence} "
        "Surface it as narrative in a bullet under the work entry the evidence belongs "
        "to, in the candidate's own terms. A skills-list entry does NOT satisfy this. "
        "If surfacing it would stretch beyond that evidence, leave it out — grounding "
        "outranks coverage, and never fabricate to close a gap."
    )


def underclaim_signal_issues(
    draft: dict[str, Any] | None,
    keyword_ledger: list[dict[str, Any]] | None,
    *,
    limit: int = UNDERCLAIM_ISSUE_LIMIT,
    min_fit_weight: float | None = None,
) -> list[ReviewIssue]:
    """The top-``limit`` under-claimed concepts as ``ReviewIssue``s for the corrector.

    Minted ``blocking`` deliberately: ``corrector_feedback.render_blocking_issues``
    filters to blocking by design (its constraint 1), so a ``minor`` signal issue would
    be computed every round and silently dropped — the exact "partial consumption reads
    as consumption" defect ADR-083 was written to close. The severity says *render
    this*; it forces nothing, because ``review_and_refine`` evaluates the signal only
    after it has already decided to run a corrector round.
    """
    return [
        ReviewIssue(text=_issue_text(c), severity=SEVERITY_BLOCKING)
        for c in verified_narrative_underclaim(draft, keyword_ledger, min_fit_weight=min_fit_weight)[:limit]
    ]


def underclaim_signal_issues_fn(
    keyword_ledger: list[dict[str, Any]] | None,
    *,
    limit: int = UNDERCLAIM_ISSUE_LIMIT,
) -> Callable[[dict[str, Any]], Sequence[ReviewIssue]]:
    """Bind a ledger to :func:`underclaim_signal_issues` for ``review_and_refine``'s
    ``signal_issues_fn`` parameter — recomputed per round on the CURRENT draft, exactly
    like ``coverage_reviewer_prompt_fn``'s wrapper, so a concept the corrector has since
    surfaced stops being demanded without any state of its own."""

    def fn(draft: dict[str, Any]) -> Sequence[ReviewIssue]:
        return underclaim_signal_issues(draft, keyword_ledger, limit=limit)

    return fn
