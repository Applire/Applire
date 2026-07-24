# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US265 (E048 / ADR-058 exception b, PO-approved 2026-07-24) — deterministic
quantification + availability elicitation signals for the EXISTING interview
QuestionGenerator (services/interview_graph.py + prompts/interview.py).

STRICT boundary: this module supplies pure, deterministic INPUTS to the
existing question-generation prompt. It is not a new interview mode, not a new
engine, does not touch ceilings, does not touch denial-machinery (ADR-059),
and never calls an LLM itself.

Trigger (founder acceptance, 2026-07-23): a blind hiring panel named missing
NUMBERS (team size, production scale, eval metrics) on evidenced JD-critical
claims as invite-flipping facts — the interview collects capabilities but
never their quantification.
"""
from __future__ import annotations

from applire.services.oracle.matchers import extract_figures


def _concept_evidence_text(concept: str, cluster: dict, profile: dict) -> str:
    """All text already available to the QuestionGenerator prompt that touches
    ``concept`` — the cluster's own ``jd_context`` plus any per-role
    technology/responsibility/achievement bullet (the same #236 profile-summary
    slice ``build_question_prompt`` sends). Empty string = no evidence at all,
    i.e. a normal unevidenced gap, not a quantification candidate.
    """
    concept_l = (concept or "").strip().lower()
    if not concept_l:
        return ""
    texts: list[str] = []
    jd_context = cluster.get("jd_context", "") or ""
    if concept_l in jd_context.lower():
        texts.append(jd_context)
    for entry in profile.get("work_experience", []) or []:
        bullets = (
            [str(t) for t in (entry.get("technologies") or [])]
            + list(entry.get("responsibilities") or [])
            + list(entry.get("achievements") or [])
        )
        for bullet in bullets:
            if concept_l in str(bullet).lower():
                texts.append(str(bullet))
    return " ".join(texts)


def detect_unquantified_concepts(cluster: dict, profile: dict) -> list[str]:
    """Cluster concepts (``cluster["gaps"]``) that are evidenced but whose
    evidence text carries no figure — reusing
    ``services/oracle/matchers/figures.py`` (the SAME instrument the
    Truthfulness Oracle uses for number/percent/currency/year extraction; this
    is deliberately not a second regex).

    - evidenced + no figures  → flagged (a quantification prompt fits)
    - evidenced + has figures → not flagged (already quantified)
    - unevidenced             → not flagged (a normal gap question, never a
      quantification ask — never invite a number for something the profile
      shows no sign of at all)
    """
    flagged: list[str] = []
    for concept in cluster.get("gaps", []) or []:
        evidence = _concept_evidence_text(concept, cluster, profile)
        if not evidence.strip():
            continue
        if not extract_figures(evidence):
            flagged.append(concept)
    return flagged


# ---------------------------------------------------------------------------
# Availability elicitation (US265 task 3 — approved fold-in)
# ---------------------------------------------------------------------------

# Small, conservative, EN+DE marker list — a JD signalling an availability /
# commitment requirement. Deliberately narrow: a false positive only adds one
# invitingly-phrased, terminal-answer question; a false negative silently
# skips it — the safe direction to err in.
_AVAILABILITY_MARKERS: tuple[str, ...] = (
    "permanent employment",
    "notice period",
    "availability",
    "verfügbarkeit",
    "unbefristet",
)


def detect_availability_signal(jd_raw_text: str) -> bool:
    """True if the JD's raw text names an availability/commitment requirement."""
    text = (jd_raw_text or "").lower()
    return any(marker in text for marker in _AVAILABILITY_MARKERS)


def _is_open_ended_role(entry: dict) -> bool:
    """A role counts as "open-ended/current" via the explicit ``is_current``
    flag when present; otherwise falls back to an empty ``end_date`` (the
    schema's own "current" convention, ADR-044)."""
    is_current = entry.get("is_current")
    if is_current is True:
        return True
    if is_current is False:
        return False
    return not entry.get("end_date")


def has_multiple_open_roles(profile: dict) -> bool:
    """True if the profile shows >= 2 open-ended ("current") roles — the
    signal that the candidate's availability genuinely needs clarifying."""
    roles = profile.get("work_experience", []) or []
    return sum(1 for entry in roles if _is_open_ended_role(entry)) >= 2


def should_ask_availability(jd_raw_text: str, profile: dict) -> bool:
    """Both deterministic conditions required: a JD availability/commitment
    marker AND >= 2 open-ended current roles on the candidate's profile."""
    return detect_availability_signal(jd_raw_text) and has_multiple_open_roles(profile)
