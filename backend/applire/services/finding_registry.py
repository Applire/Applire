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
"""ADR-099 — the ONE finding registry: every kind a user can be shown after a
generation or import, mapped to its delivery class and the one action that
resolves it.

Classes (ADR-099 cl. 2):
  F — fixed by the generation loop before delivery; not rendered while the
      loop ran clean (cl. 4(d)); its fallback class applies otherwise.
  A — needs the user: ONE action (one control, or one question with a closed
      answer set) whose every answer clears the finding on recompute.
  U — the producer did not run: rendered as unknown with ONE re-run action.
  S — silent: report and operator log only.

CONTRACT COMMIT (main session, UAT-2 fix round): the vocabulary below is
fixed — WP-B fills ``REGISTRY`` and wires ``delivery_class``/``resolver`` into
the report; WP-C and WP-A2 render from those fields. Add keys; never rename.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal, Optional

logger = logging.getLogger(__name__)

DeliveryClass = Literal["F", "A", "U", "S"]

#: Resolver vocabulary — the one action a class-A/U row offers (ADR-099 cl. 6–11).
RESOLVERS: frozenset[str] = frozenset(
    {
        "ask_station",        # misattributed / restatementBorrowed: "Wohin gehört das?" (owning stations + Entfernen)
        "ask_true",           # unbacked kinds: "Stimmt das?" (Ja + wo → testimony; no change → Entfernen; Nein → take-out)
        "ask_achieved",       # inflatedAspirational: "Ist das erreicht?" (Ja → testimony; Nein → rewritten as goal)
        "remove_only",        # residue whose only end state is take-out
        "page_length",        # ONE card: keep N pages / cut to target (closest reachable)
        "translate",          # mixed-language residue: "Diese Stellen übersetzen"
        "recheck",            # class U: "Jetzt prüfen" — full report re-run on this version
        "regenerate",         # class U terminal-review-not-run: "Neue Fassung erstellen"
        "answer_unasked",     # letter unasked requirements: "Jetzt beantworten"
        "signature",          # letter only: add signature / "Ohne Unterschrift"
        "open_gap_analysis",  # group 3: one link to the gap analysis
        "conflict_list",      # hub: four outcomes (keep · take · keep both · merge)
        "conflict_scalar",    # hub: two outcomes
        "confirmation",       # hub: parked question + its options (unchanged)
        "recompute_span",     # hub span_provenance (unchanged)
        "confirm_match",      # hub suspect_binding / recognised pair: Ja / Nein-getrennt
        "unit_input",         # hub unit: the input in place
        "adopt_not_applied",  # hub not_applied: "Übernehmen" per item
        "complete_section",   # hub completeness with field_gaps: "Verbessern"
    }
)


@dataclass(frozen=True)
class Entry:
    cls: DeliveryClass
    #: class-A/U action; for class F the resolver of the A/U FALLBACK (cl. 4(d)/(e)).
    resolver: Optional[str]
    #: class F only — the class its residue / no-loop render falls back to.
    fallback: Optional[DeliveryClass] = None


#: kind key → Entry. Kind keys: "ats:<check id>", "oracle:<detail key>",
#: "keyword:<bucket>", "critic:<kind>", "health:<thread>", "page:<signal>".
#: Filled by WP-B (document side) and WP-A1 (health side).
#:
#: WP-B key grammar (document side; arc42 §5.3.29a is the table's documentation):
#:   ``ats:<family>``                 an ATS check id with any trailing ``-<n>`` index
#:                                    stripped (``work-2`` → ``work``)
#:   ``ats:<family>@<status>``        the same check in one status (``not_applicable``)
#:   ``ats:<family>~<details_key>``   the same check in one keyed band
#:                                    (``page-length~page-length-kept``) — most specific wins
#:   ``oracle:<detail_key>``          a flagged Oracle verdict by its detail key
#:   ``oracle@<verdict>``             an Oracle verdict without (or beyond) a detail key
#:   ``keyword:<bucket>``             an ``ATSKeywordCoverage`` bucket field
#:   ``terminal:<check>``             one terminal-review code line (``CheckNote.key``)
#:   ``terminal:finding`` / ``terminal:note:<key>``  the card's other item kinds
#:   ``critic:<kind>``                an outcome-critic advisory kind
#:   ``page:<signal>``                a condition the result PAGE renders from data
#:                                    other than a report row (signature, unasked …)
_F = "F"


def _e(cls: DeliveryClass, resolver: Optional[str] = None, fallback: Optional[DeliveryClass] = None) -> Entry:
    return Entry(cls=cls, resolver=resolver, fallback=fallback)


#: ADR-099 cl. 4(c): the declared order class-F families claim the loop —
#: truthfulness first, then language, then craft. The pipeline REALISES it by
#: call sequence, not by reading this tuple: the truth round (own 1-call bound)
#: runs before the terminal loop, the second language round before compose, and
#: inside a terminal round ``cv_v2`` ranks blocking checks → critic consistency →
#: advisory craft under ``MAX_FINDINGS_PER_ROUND``. Documentation for arc42
#: §5.3.29a and agents; changing it changes no behaviour.
FAMILY_PRIORITY: tuple[str, ...] = (
    "truth.misattributed",   # attribution round, CV and letter (cl. 6)
    "truth.unbacked",        # corrector removes / narrows (cl. 6, removal line cl. 5)
    "language",              # second bounded language round (cl. 9)
    "consistency",           # critic numeric / internal inconsistency (cl. 8)
    "coverage",              # narrative evidence / claim / project missing (cl. 8)
    "duplicates",            # redundancy route (cl. 8)
    "craft",                 # the remaining v2 code checks
)

_DOC: dict[str, Entry] = {
    # ── PDF read-back (cl. 8): a template defect, never the user's task ──────────
    **{f"ats:{c}": _e("S") for c in (
        "contact-name", "contact-email", "contact-phone", "work", "education", "skills",
        "content", "reading-order", "recipient-company", "body",
    )},
    # ── duplicates (cl. 8, ADR-094 cl. 3 am.): redundancy route → corrector ─────
    "ats:skills-near-dupe": _e(_F, None, "S"),
    "ats:duplicate-bullets": _e(_F, None, "S"),
    "ats:duplicate-project": _e(_F, None, "S"),
    # ── mixed language (cl. 9): second language round; residue one action ───────
    "ats:document-language": _e(_F, "translate", "A"),
    # ADV-6 (main ruling, ADR-099 cl. 2): n.a. = the caller stated no language
    # (a row without ``document_language``; the v1 letter never passes one). The
    # recheck re-reads the SAME stamped column, so "Jetzt prüfen" cannot clear
    # it — a scope statement, not a producer that failed → S.
    "ats:document-language@not_applicable": _e("S"),
    "ats:document-language~document-language-not-applicable": _e("S"),
    # ── page length (cl. 7): ONE card, CV and letter ─────────────────────────────
    "ats:page-length": _e("A", "page_length"),
    "ats:page-length~page-length-kept": _e("S"),          # ADR-077 pin driver, report only
    "ats:page-length~page-length-target": _e("S"),        # advisory pass
    "ats:page-length~page-length-senior": _e("S"),        # advisory pass
    "ats:page-length@not_applicable": _e("S"),            # .docx: no pagination by kind
    "ats:page-length~page-length-not-applicable": _e("S"),
    # ── coverage (cl. 8): the coverage loop; residue S ───────────────────────────
    "ats:narrative-evidence": _e(_F, None, "S"),
    # ADV-6: n.a. = no Keyword Ledger for the job (no gap analysis ran — e.g. an
    # agent-door CV). Neither the recheck nor a regeneration builds one (both read
    # the latest GapAnalysis) → no one action clears it → S.
    "ats:narrative-evidence@not_applicable": _e("S"),
    # ── unknown states (cl. 9, ADR-081 cl. 9 stands) ─────────────────────────────
    # ADV-6 (a): every bilingual profile — a scope statement of the keyword
    # check ("compares wording only"), never a producer that did not run → S.
    "ats:keyword-other-language": _e("S"),
    # ── advisory passes (cl. 8) ──────────────────────────────────────────────────
    "ats:skills-weak-vault-tie": _e("S"),
    "ats:employer-repetition": _e("S"),
    # ── the terminal review (cl. 8/9): open notes S; did not run / finish U ──────
    "ats:terminal-review": _e("S"),
    "ats:terminal-review@not_applicable": _e("U", "regenerate"),
    "ats:terminal-review~terminal-review-not-run": _e("U", "regenerate"),

    # ── Oracle group 1 (cl. 6) ───────────────────────────────────────────────────
    "oracle:misattributed": _e(_F, "ask_station", "A"),
    "oracle:restatementBorrowed": _e(_F, "ask_station", "A"),
    "oracle@misattributed": _e(_F, "ask_station", "A"),
    **{f"oracle:{k}": _e(_F, "ask_true", "A") for k in (
        "credentialUnbacked", "identifierAbsent", "skillNoEvidence", "figuresNoEvidence",
        "tenureStartBeforeVault", "tenureExceedsVault", "subjectTenureExceeds",
    )},
    "oracle@unbacked": _e(_F, "ask_true", "A"),          # unsupported term / bare unbacked
    "oracle:inflatedAspirational": _e("A", "ask_achieved"),
    "oracle@inflated": _e("A", "ask_achieved"),
    # an Oracle verdict that is not a finding (grounded / n.a.) — report only
    "oracle@grounded": _e("S"),
    "oracle@not_applicable": _e("S"),
    # unverifiable: S, except where the JUDGEMENT did not run (cl. 9 "Nicht geprüft")
    "oracle@unverifiable": _e("S"),
    # ADV-6 (c): a literal miss over a mixed-language vault — deterministic, the
    # recheck reproduces it → S (scope statement, as keyword-other-language).
    "oracle:otherLanguageNotChecked": _e("S"),
    # Only ever the UNAVAILABLE verdict of the cross-language judgement (provider
    # failure / citation drop); the recheck re-runs the judgement with a provider
    # (cv._truth_audit / cover_letter._letter_truth_audit) → can clear → U.
    "oracle:crossLanguageUnconfirmed": _e("U", "recheck"),
    # ADV-6 (c): produced by the deterministic attribution checker AND by a
    # resolved restatement judgement ("uncertain"/true) — the recheck reproduces
    # both; an honest "left open, not accused" (ADR-068) → S.
    "oracle:attributionUnresolved": _e("S"),
    # the remaining detail keys, all on non-flag verdicts (report only)
    **{f"oracle:{k}": _e("S") for k in (
        "crossLanguageEquivalent", "entailmentNarrow", "figuresFromStatedLimit", "figuresTrace",
        "fromLetterForm", "groundedUnion", "honestDenial", "noCheckableContent",
        "noCloseEvidence", "qualificationNamed", "triageEmployerFact",
    )},

    # ── keyword buckets ──────────────────────────────────────────────────────────
    "keyword:present_unsupported": _e(_F, "ask_true", "A"),
    "keyword:present_unsupported_employer_sentence": _e(_F, "remove_only", "A"),
    "keyword:present_unsupported_denials": _e(_F, "remove_only", "A"),
    "keyword:present_denied": _e(_F, "remove_only", "A"),
    "keyword:missing_claimable": _e("S"),                 # group 2 (cl. 7)
    "keyword:missing_honest_gap": _e("A", "open_gap_analysis"),  # group 3, one link
    "keyword:present_other_language": _e("S"),  # ADV-6 (a): the bucket behind the row
    "keyword:present": _e("S"),
    "keyword:missing": _e("S"),                           # back-compat union of the two missing buckets
    "keyword:claimable_concepts": _e("S"),
    "keyword:keyword_liability_concepts": _e("S"),

    # ── terminal-review card items (S11.2: one row per code) ─────────────────────
    "terminal:finding": _e("S"),
    **{f"terminal:{c}": _e(_F, None, "S") for c in (
        "cv.role_budget", "cv.recorded_name", "cv.level_code_changed", "cv.vault_field_missing",
        "cv.empty_project", "cv.claim_missing", "cv.project_missing", "cv.project_not_mentioned",
        "cv.skill_spelling", "cv.skills_cap", "cv.skill_duplicate", "cv.skill_named_in_bullet",
        "letter.salutation", "letter.signoff", "letter.closing", "letter.names",
        "letter.availability",
    )},
    # one condition, one place (cl. 10): these codes restate a row rendered elsewhere
    "terminal:cv.page_length": _e("S"),                   # → the page-length card
    "terminal:letter.page_length": _e("S"),               # → the page-length card
    "terminal:cv.language_shape": _e("S"),                # → document-language
    "terminal:cv.language_mixed": _e("S"),                # → document-language (second round)
    "terminal:letter.language": _e("S"),                  # → document-language
    "terminal:cv.identifier_not_in_profile": _e("S"),     # → oracle:identifierAbsent
    "terminal:letter.identifier": _e("S"),                # → oracle:identifierAbsent
    "terminal:cv.skill_untied": _e("S"),                  # → oracle:skillNoEvidence
    "terminal:letter.figure": _e("S"),                    # → oracle:figuresNoEvidence
    "terminal:letter.limit": _e("S"),
    "terminal:note:letter.limit_cut": _e("S"),
    "terminal:note:letter.limit_kept": _e("S"),
    "terminal:note:letter.missing_terms": _e("S"),
    "terminal:letter.post_condense": _e("S"),
    # the in-loop critic's advisory findings (cl. 8): never a delivered note, residue S
    "terminal:letter.critic_numeric": _e(_F, None, "S"),
    "terminal:critic.internal_inconsistency": _e(_F, None, "S"),
    "terminal:critic.numeric_inconsistency": _e(_F, None, "S"),

    # ── outcome critic (cl. 8, UAT-3j) ───────────────────────────────────────────
    "critic:numeric_inconsistency": _e(_F, None, "S"),
    "critic:internal_inconsistency": _e(_F, None, "S"),
    "critic:letter_only": _e("S"),
    "critic:letter_richer": _e("S"),

    # ── conditions the result page renders from non-row data ─────────────────────
    "page:page-overflow": _e("A", "page_length"),         # CV + letter card (cl. 7)
    "page:signature-hint": _e("A", "signature"),          # letter only (cl. 9)
    "page:unasked-requirements": _e("A", "answer_unasked"),  # letter (cl. 9)
    "page:open-requirements": _e("A", "open_gap_analysis"),  # group 3 (not counted)
    "page:review-degradation": _e("U", "regenerate"),     # a loop step did not run
    "page:unchecked": _e("U", "recheck"),                 # "Nicht geprüft"
    "page:form-input": _e("S"),                           # letter attribution, not a defect
    "page:cross-document": _e("S"),                       # removed (supersedes R-1)
    "page:group-two": _e("S"),                            # covered but cut (cl. 7)
}

REGISTRY: dict[str, Entry] = dict(_DOC)


def classify(kind: str) -> Entry:
    """The registry entry for ``kind``. Raises ``KeyError`` for an unregistered
    kind — the CI test (T-registry) enumerates every producer's kinds through
    this, so a new kind without an entry fails the build instead of rendering
    raw or vanishing."""
    return REGISTRY[kind]


# ── WP-B: the producers' kinds → the registry, and the report stamp ─────────────

_INDEX_SUFFIX = re.compile(r"-\d+$")


def check_family(check_id: str) -> str:
    """``work-2`` → ``work``; ids without an index are returned unchanged."""
    return _INDEX_SUFFIX.sub("", check_id or "")


def check_kinds(check: dict) -> list[str]:
    """The registry keys one ATS check may resolve to, most specific first."""
    fam = check_family(str(check.get("id") or ""))
    out = []
    if check.get("details_key"):
        out.append(f"ats:{fam}~{check['details_key']}")
    if check.get("status"):
        out.append(f"ats:{fam}@{check['status']}")
    out.append(f"ats:{fam}")
    return out


_FLAG_VERDICTS = frozenset({"inflated", "misattributed", "unbacked"})


def verdict_kinds(verdict: dict) -> list[str]:
    """The registry keys one Oracle verdict may resolve to, most specific first.
    A non-flag verdict (grounded, n.a.) resolves by verdict before detail key —
    a ``skillNoEvidence`` key on a ``grounded`` verdict is no finding."""
    v = str(verdict.get("verdict") or "")
    key = verdict.get("detail_key")
    if v in ("grounded", "not_applicable"):
        return [f"oracle@{v}"]
    out = []
    # A detail key registered as report-only is verdict-neutral (``entailmentNarrow``
    # carries whatever verdict the narrow judge returned): on a FLAG verdict it must
    # not silence the row — the verdict decides (T-registry found this).
    if key and not (v in _FLAG_VERDICTS and REGISTRY.get(f"oracle:{key}", Entry("A", None)).cls == "S"):
        out.append(f"oracle:{key}")
    out.append(f"oracle@{v}")
    return out


def note_kinds(note: dict) -> list[str]:
    kind = note.get("kind")
    key = note.get("key")
    if kind == "code" and key:
        return [f"terminal:{key}"]
    if kind == "note" and key:
        return [f"terminal:note:{key}"]
    if kind == "finding" and key:
        return [f"terminal:{key}", "terminal:finding"]
    return ["terminal:finding"]


def resolve(kinds: list[str]) -> tuple[str, Entry] | None:
    for k in kinds:
        if k in REGISTRY:
            return k, REGISTRY[k]
    return None


def rendered_class(entry: Entry) -> tuple[DeliveryClass, Optional[str]]:
    """The class + resolver a ROW of this kind is stamped with.

    A row of a class-F kind that is still in the DELIVERED report is residue:
    the loop's self-audit and the delivered report are one instrument (cl. 4(b)),
    so a fixed instance is simply absent. Residue is never dropped (cl. 4(e)) —
    it renders as its fallback. Hence a row is never stamped "F": the surface
    hides nothing on its own, and a loop that did not run (agent door,
    ``loop_clean is None``) or degraded shows the same fallback rows."""
    if entry.cls == _F:
        return (entry.fallback or "A"), entry.resolver
    return entry.cls, entry.resolver


def _stamp(row: dict, kinds: list[str], unregistered: list[str], clean: Optional[bool] = True,
           silent: list[str] | None = None) -> None:
    hit = resolve(kinds)
    if hit is None:
        unregistered.append(kinds[-1])
        row["delivery_class"], row["resolver"] = None, None
        return
    cls, res = rendered_class(hit[1])
    if hit[1].cls == _F and cls == "S" and clean is not True:
        # ADR-099 cl. 4(d) / adv S7: nothing fixed it (agent door, degraded loop) —
        # S may only stand for a producer that answered, so it is unknown.
        cls, res = "U", "regenerate"
    row["delivery_class"], row["resolver"] = cls, res
    if cls == "S" and silent is not None:
        silent.append(hit[0])


#: keyword buckets stamped on ``ATSKeywordCoverage.delivery``
KEYWORD_BUCKETS: tuple[str, ...] = (
    "present_unsupported", "present_unsupported_employer_sentence",
    "present_unsupported_denials", "present_denied", "missing_claimable",
    "missing_honest_gap", "present_other_language",
)


def annotate_report(report: dict | None) -> list[str]:
    """Stamp ``delivery_class``/``resolver`` on every check, terminal-review
    note and keyword bucket of a persisted ATS report dict, IN PLACE. A passing
    check is "S" (it is no finding; the passed line counts it by status).
    Returns the unregistered kinds (logged by the caller; the CI registry test
    keeps the list empty). Never raises."""
    unregistered: list[str] = []
    if not isinstance(report, dict):
        return unregistered
    delivery = report.get("delivery")
    # No delivery state yet (the audit seam, before generation records it): stamp
    # as if clean; :func:`apply_delivery` re-stamps once the loop's state is known.
    clean = delivery.get("loop_clean") if isinstance(delivery, dict) else True
    by_agent = bool(delivery.get("authored_by_agent")) if isinstance(delivery, dict) else False
    silent: list[str] = []
    try:
        for check in report.get("checks") or []:
            if not isinstance(check, dict):
                continue
            if check.get("status") == "pass" and check_family(str(check.get("id"))) != "terminal-review":
                hit = resolve(check_kinds(check))
                if hit is None:
                    unregistered.append(check_kinds(check)[-1])
                check["delivery_class"], check["resolver"] = "S", None
            else:
                _stamp(check, check_kinds(check), unregistered, clean, silent)
                if (
                    by_agent
                    and check_family(str(check.get("id"))) == "terminal-review"
                    and check.get("delivery_class") == "U"
                ):
                    # RULING D-AG1: the user's own agent wrote this document; Applire's
                    # final review was never its step, so "not run" is no finding.
                    check["delivery_class"], check["resolver"] = "S", None
                    silent.append(check_kinds(check)[-1])
                if check.get("delivery_class") in ("A", "U") and not check.get("details_key"):
                    # ADR-099 cl. 12: no server prose — an unkeyed row is silent
                    # until its detail is keyed in the catalogs.
                    logger.info("FINDING_UNKEYED check=%s — rendered S until keyed", check.get("id"))
                    check["delivery_class"], check["resolver"] = "S", None
                    silent.append(check_kinds(check)[-1])
            for note in check.get("notes") or []:
                if isinstance(note, dict):
                    _stamp(note, note_kinds(note), unregistered, clean, silent)
        if isinstance(delivery, dict) and silent:
            logger.info(
                "DELIVERY_SILENT loop_clean=%s count=%d kinds=%s",
                clean, len(silent), sorted(set(silent)),
            )
        kw = report.get("keywords")
        if isinstance(kw, dict):
            delivery = {}
            for bucket in KEYWORD_BUCKETS:
                cls, res = rendered_class(REGISTRY[f"keyword:{bucket}"])
                delivery[bucket] = {"delivery_class": cls, "resolver": res}
            kw["delivery"] = delivery
    except Exception:  # noqa: BLE001 — a stamp failure must never fail an audit
        logger.exception("finding_registry.annotate_report failed; report left unstamped")
    return unregistered


def annotate_truthfulness(report: dict | None) -> list[str]:
    """Stamp every Oracle verdict row of a persisted truthfulness report dict,
    IN PLACE (``ClaimVerdict.delivery_class``/``resolver``). Never raises."""
    unregistered: list[str] = []
    if not isinstance(report, dict):
        return unregistered
    try:
        for cr in report.get("claims") or []:
            verdict = cr.get("verdict") if isinstance(cr, dict) else None
            if isinstance(verdict, dict):
                _stamp(verdict, verdict_kinds(verdict), unregistered)
    except Exception:  # noqa: BLE001
        logger.exception("finding_registry.annotate_truthfulness failed; report left unstamped")
    return unregistered


def stamp_ats_model(report):
    """:func:`annotate_report` on an ``ATSReport`` model (the audit's ``_finish``
    seam, so every audit door — generation, section-editor re-audit, agent
    render, the .docx report — carries the stamp). Returns a new model."""
    try:
        data = report.model_dump()
        bad = annotate_report(data)
        if bad:
            logger.warning("FINDING_REGISTRY_UNREGISTERED kinds=%s", sorted(set(bad)))
        return type(report).model_validate(data)
    except Exception:  # noqa: BLE001
        logger.exception("finding_registry.stamp_ats_model failed; report left unstamped")
        return report


def stamp_truthfulness_model(report):
    """:func:`annotate_truthfulness` on a ``TruthfulnessReport`` model."""
    try:
        for cr in report.claims:
            v = cr.verdict
            hit = resolve(verdict_kinds({"verdict": v.verdict, "detail_key": v.detail_key}))
            if hit is None:
                logger.warning("FINDING_REGISTRY_UNREGISTERED kinds=%s", [v.verdict, v.detail_key])
                continue
            v.delivery_class, v.resolver = rendered_class(hit[1])
    except Exception:  # noqa: BLE001
        logger.exception("finding_registry.stamp_truthfulness_model failed")
    return report


# ── ADR-099 cl. 4(d)/5 (WP-B): the delivery state on the report ──────────────


def loop_clean_of(review_degradation) -> Optional[bool]:
    """``[]`` → True (the loop ran and every step answered); non-empty → False;
    ``None`` (agent-door render, legacy row: nothing recorded) → None."""
    if review_degradation is None:
        return None
    return not bool(review_degradation)


def apply_delivery(
    report: dict | None,
    *,
    review_degradation,
    removals: list[dict] | None = None,
    fixed: list[str] | None = None,
    previous: dict | None = None,
    origin: str | None = None,
) -> dict | None:
    """Return a COPY of ``report`` carrying ``delivery`` (``DeliveryState``) with
    every row re-stamped for it (:func:`annotate_report`). Never raises.

    * ``origin`` = the document row's persisted ``origin`` (``"agent"`` = authored
      by the user's own agent via ``render_document``; ``"pipeline"`` = Applire's
      writer) → ``authored_by_agent`` (RULING D-AG1). ``None`` → carried from
      ``previous``. Never inferred from ``loop_clean is None``: that also means a
      legacy or degraded row.
    * ``removals``/``fixed`` None → carried from ``previous`` (the report this
      one replaces): a re-audit after an edit describes the same generation.
    * Not clean (``loop_clean`` False or None): a class-F kind whose residue is
      silent renders as class U "regenerate" (adv S7; the T-S negative test).
    * The S rows are counted into one operator log line (T-S)."""
    if not isinstance(report, dict):
        return report
    try:
        import copy as _copy

        out = _copy.deepcopy(report)
        prev = (previous or {}).get("delivery") if isinstance(previous, dict) else None
        prev = prev if isinstance(prev, dict) else {}
        out["delivery"] = {
            "loop_clean": loop_clean_of(review_degradation),
            "removals": list(removals if removals is not None else prev.get("removals") or []),
            "fixed": list(fixed if fixed is not None else prev.get("fixed") or []),
            "authored_by_agent": (origin == "agent") if origin is not None
            else bool(prev.get("authored_by_agent")),
        }
        bad = annotate_report(out)
        if bad:
            logger.warning("FINDING_REGISTRY_UNREGISTERED kinds=%s", sorted(set(bad)))
        return out
    except Exception:  # noqa: BLE001
        logger.exception("finding_registry.apply_delivery failed; report left as is")
        return report


def removal_rows(removed, *, pins=None, section_of=None) -> list[dict]:
    """``DeliveryRemoval`` dicts for the truth round's removed findings. ``pins``:
    FactPin-like objects (``quote``) — a removal whose text holds a pinned quote is
    ``pinned`` (ADR-077 cl. 2: always shown expanded)."""
    from applire.services.review_state import finding_key, norm_quote

    quotes = [norm_quote(getattr(p, "quote", "") or "") for p in pins or [] if not getattr(p, "stale", False)]
    rows = []
    for f in removed or []:
        t = norm_quote(f.text)
        rows.append({
            "text": f.text,
            "section": (section_of(f.location) if section_of else None) or (f.location.split("[")[0] or None),
            "kind": f.kind,
            "finding_key": finding_key("oracle", f.text),
            "narrowed_to": None,
            "pinned": any(q and (q in t or t in q) for q in quotes),
        })
    return rows


_TOKEN = re.compile(r"[\w%+-]+")


def _tokens(s: str) -> set[str]:
    return {t.casefold() for t in _TOKEN.findall(s or "")}


def _narrowed_forms(text: str, claims: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
    """Delivered claims that are a strict narrowing of ``text``: every word of the
    delivered claim (≥ 2 words) is a word of the removed one — a presence FACT
    (ADR-062 cl. 1), never a judgement of meaning."""
    src = _tokens(text)
    out = []
    for t, is_flag in claims:
        tok = _tokens(t)
        if len(tok) >= 2 and tok < src:
            out.append((t, is_flag))
    return out


def delivered_report(
    report: dict | None,
    *,
    review_degradation,
    truthfulness: dict | None,
    delivered_data,
    truth_round=None,
    pins=None,
) -> dict | None:
    """The generation path's delivered ATS report with ``delivery`` (ADR-099 cl.
    4(d)/5), CV and letter alike: removals = what the truth round removed that the
    delivered document does not carry again; fixed = truth-round kinds the
    delivered truthfulness report (the same instrument) no longer flags. Never
    raises."""
    from applire.services.attribution_round import claim_present

    removals: list[dict] = []
    fixed: list[str] = []
    try:
        if truth_round is not None and truth_round.findings:
            claims = [
                (str((c.get("claim") or {}).get("text") or ""),
                 (c.get("verdict") or {}).get("verdict") in _FLAG_VERDICTS)
                for c in (truthfulness or {}).get("claims") or [] if isinstance(c, dict)
            ]
            flagged = {t.casefold() for t, is_flag in claims if is_flag}
            gone = [f for f in truth_round.removed if not claim_present(f.text, delivered_data)]
            rows = removal_rows(gone, pins=pins)
            kept_rows = []
            for f, row in zip(gone, rows):
                # A NARROWED claim whose remainder is itself still flagged is residue
                # (it renders as its class-A card) — never also a removal (one
                # condition, one place, cl. 10). A remainder the Oracle backs is the
                # narrowing: named on the row.
                narrowed = _narrowed_forms(f.text, claims)
                if any(is_flag for _t, is_flag in narrowed):
                    continue
                if narrowed:
                    row["narrowed_to"] = max((t for t, _ in narrowed), key=len)
                kept_rows.append(row)
            removals = kept_rows
            if truth_round.applied:
                fixed = sorted({
                    f.kind for f in truth_round.findings
                    if f.text.casefold() not in flagged
                    and not any(is_flag for _t, is_flag in _narrowed_forms(f.text, claims))
                })
    except Exception:  # noqa: BLE001
        logger.exception("delivered_report: delivery state failed — none recorded")
    return apply_delivery(report, review_degradation=review_degradation, removals=removals, fixed=fixed)

# UAT-3d (Hazelnut build 5, WP-A1 hub) — the profile-health hub's kinds.
REGISTRY.update(
    {
        "health:conflict_list": Entry("A", "conflict_list"),
        "health:conflict_scalar": Entry("A", "conflict_scalar"),
        "health:confirmation": Entry("A", "confirmation"),
        "health:unit": Entry("A", "unit_input"),
        "health:span_provenance": Entry("A", "recompute_span"),
        "health:suspect_binding": Entry("A", "confirm_match"),
        "health:duplicate_entry": Entry("A", "confirm_match"),
        "health:aspiration": Entry("A", "ask_achieved"),  # RULING B-1
        "health:not_applied": Entry("A", "adopt_not_applied"),
        "health:not_applied_unadoptable": Entry("A", "complete_section"),
        "health:accuracy": Entry("S", None),
        "health:completeness": Entry("A", "complete_section"),
        "health:completeness_no_field_gaps": Entry("S", None),
    }
)
