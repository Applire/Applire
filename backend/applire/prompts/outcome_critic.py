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

"""ADR-060 Pass B judgement prompt (#322).

ADR-062 clause 2: the replacement for a text heuristic is "the underlying
facts, verbatim, plus the narrowest instruction that prevents them being
over-read" — not a better heuristic. This prompt is exactly that shape: it
hands the model a short list of ALREADY-COMPUTED candidate concepts (each one
a claimable Keyword-Ledger concept the letter states with more depth than the
CV — see ``services/outcome_critic.py:compute_presence_facts``), never the
raw documents, and asks one narrow yes/no per candidate.

ADR-060 clause 7 (input budget): deliberately does NOT re-send cv_data/
letter_data — the deterministic fact layer has already reduced the input to
the handful of concepts that could possibly be an incoherence, so the model
never re-reads either document in full. This is a stricter reading of clause
7 than "the drafted documents" implies literally; see the module docstring
in ``services/outcome_critic.py`` and the session report for why that
narrower design was chosen over sending both full drafts.
"""

from typing import Any

SYSTEM_PROMPT = (
    "You are Applire's outcome critic (ADR-060 Pass B, issue #322). Your ONLY "
    "job is a narrow judgement over a short list of already-computed candidate "
    "concepts: for each one, decide whether the cover letter's claim would "
    "read, to a recruiter who cross-reads the CV and the letter, as an "
    "INVENTED addition — because the letter states something about the "
    "candidate's history at more depth (a duration, a figure, a scope) than "
    "the CV substantiates for the SAME concept, or because the concept is "
    "entirely absent from the CV.\n\n"
    "You are given ONLY the facts already computed by code, never the raw "
    "documents — do not ask for more context, do not invent a fact of your "
    "own, and do not judge anything except the candidates listed below. A "
    "rewording at the SAME depth is not an incoherence — worth_surfacing must "
    "be false whenever the letter's version is merely differently phrased, "
    "not more specific. When genuinely unsure, answer false: a missed "
    "incoherence costs nothing (the document already shipped); a wrong "
    "positive costs the candidate's trust in the advisory."
)


def build_pass_b_prompt(
    candidates: list[dict[str, Any]],
    job_role_title: str | None,
    jd_excerpt: str | None,
) -> str:
    """Build the judgement prompt from pre-computed candidates.

    Each ``candidates`` entry: ``{"concept": str, "cv_state": str, "letter_state": str}``
    — ``cv_state`` is ``"not mentioned in the CV"`` for a letter-only concept, or the
    CV's own less-specific verbatim mention for a same-concept-different-depth pair.
    """
    lines = [
        f"Target role: {job_role_title or 'unspecified'}",
        "",
        "Job description excerpt (context only — do not judge JD coverage here, "
        "that is a separate, already-existing check):",
        jd_excerpt or "(none)",
        "",
        "Candidate concepts — one row per claimable concept where the letter "
        "states more than the CV does:",
    ]
    for c in candidates:
        lines.append(
            f"- concept: {c['concept']!r}\n"
            f"  CV state: {c['cv_state']!r}\n"
            f"  Letter state: {c['letter_state']!r}"
        )
    lines.append("")
    lines.append(
        "Return JSON only, exactly this shape, one entry per candidate concept "
        'above, no others: {"findings": [{"concept": "<exact concept string '
        'from above>", "worth_surfacing": true|false}, ...]}'
    )
    return "\n".join(lines)
