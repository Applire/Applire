# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US265 (E048 / ADR-058 exception b, PO-approved 2026-07-24) — deterministic
quantification elicitation signals for the EXISTING interview
QuestionGenerator (services/interview_graph.py + prompts/interview.py).

STRICT boundary: this module supplies pure, deterministic INPUTS to the
existing question-generation prompt. It is not a new interview mode, not a new
engine, does not touch ceilings, does not touch denial-machinery (ADR-059),
and never calls an LLM itself.

Trigger (founder acceptance, 2026-07-23): a blind hiring panel named missing
NUMBERS (team size, production scale, eval metrics) on evidenced JD-critical
claims as invite-flipping facts — the interview collects capabilities but
never their quantification.

Hazelnut build 3, ruling V-4 (founder 2026-10-22): the availability /
notice-period trigger that lived here (posting marker + >= 2 open roles) is
REMOVED with the interview add-on it fed — the interview never asks the notice
period; the profile field is prompted by a login info card (V + F2).
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
