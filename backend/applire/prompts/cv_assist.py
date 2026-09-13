# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kaile micro-session assist prompts (M5.7.1 inline-prompt move).

These are the system prompts and user-prompt builders behind the three
``services/cv_assist.py`` LLM calls — moved here verbatim from that module's
former ``system=`` literals and ``_question_prompt``/``_suggestion_prompt``/
``_rewrite_prompt`` private functions (Sprint 10, ADR-004 micro-session
concept). This is a placement change only: every rendered string is
byte-identical to what shipped before the move (pinned by
``tests/unit/test_cv_assist_prompts.py``).

  * :data:`ASSIST_QUESTION_SYSTEM_PROMPT` / :func:`build_assist_question_prompt`
    — POST assist: one focused question for a gap in a CV section. #311: the
    Du-form register clause lives ONLY here — the question is the one call
    whose output the candidate reads directly as address (BRAND.md §2.3 pins
    the product UI to Du); the other two calls write CV section prose, which
    addresses nobody and must stay register-free. Needs charter-run
    verification (ADR-062 clause 7).
  * :data:`ASSIST_SUGGESTION_SYSTEM_PROMPT` / :func:`build_assist_suggestion_prompt`
    — PATCH assist: suggested section text from the candidate's answer.
  * :data:`ASSIST_REWRITE_SYSTEM_PROMPT` / :func:`build_assist_rewrite_prompt`
    — single-turn directed rewrite. ADR-084 embedding point 28 (Form A,
    inline): the target role title is the posting's own text, and this
    prompt's output is written straight into the candidate's CV section, so
    ``role_title`` is fenced with ``fence_inline`` before it is embedded.
    ``fence_inline`` is imported inside the builder (not at module level) to
    avoid a cycle, exactly as it was imported inside ``_rewrite_prompt``
    before this move.
"""

ASSIST_QUESTION_SYSTEM_PROMPT = (
    "Du bist Kaile, ein KI-Karriereassistent. "
    "Deine Aufgabe ist es, dem Nutzer mit einer einzigen präzisen Frage zu helfen, "
    "eine Lücke in seinem Lebenslauf zu schließen."
)

ASSIST_SUGGESTION_SYSTEM_PROMPT = (
    "Du bist Kaile, ein KI-Karriereassistent. "
    "Generiere verbesserten Lebenslauf-Text, der natürlich klingt und die "
    "identifizierte Lücke schließt."
)

ASSIST_REWRITE_SYSTEM_PROMPT = (
    "Du bist Kaile, ein KI-Karriereassistent. "
    "Rewrite the given CV section exactly as directed by the user. "
    "Output only the improved section text — no commentary, no introduction."
)


def build_assist_question_prompt(section_label: str, section_content: str, gap_id: str) -> str:
    """Kaile's assist question — German prose the candidate reads directly.

    #311: the register clause is category B (never asked, so the model
    defaulted to Sie). BRAND.md §2.3 pins the product UI to Du. Only the
    *question* carries it; ``build_assist_suggestion_prompt``/
    ``build_assist_rewrite_prompt`` write CV section prose, which addresses
    nobody and must stay register-free. Needs charter-run verification
    (ADR-062 clause 7).
    """
    return (
        f"Abschnitt: {section_label}\n"
        f"Aktueller Inhalt:\n{section_content}\n\n"
        f"Identifizierte Lücke: {gap_id}\n\n"
        "Stelle eine einzige, kurze, konkrete Frage auf Deutsch, die dem Nutzer hilft, "
        "Informationen zu liefern, mit denen diese Lücke im Lebenslauf geschlossen werden kann. "
        "Sprich den Nutzer dabei in der Du-Form an („du\", „dein\", „dir\") — "
        "niemals in der Sie-Form. "
        "Nur die Frage, keine Erklärung."
    )


def build_assist_suggestion_prompt(
    section_label: str,
    section_content: str,
    gap_id: str,
    answer: str,
) -> str:
    return (
        f"Abschnitt: {section_label}\n"
        f"Aktueller Inhalt:\n{section_content}\n\n"
        f"Lücke: {gap_id}\n"
        f"Antwort des Nutzers: {answer}\n\n"
        "Generiere einen verbesserten Text für diesen Abschnitt, der die Lücke schließt "
        "und natürlich klingt. Gib nur den verbesserten Text aus, ohne Kommentar oder Einleitung."
    )


def build_assist_rewrite_prompt(
    section_label: str,
    section_content: str,
    directions: str,
    gap_ids: list[str],
    role_title: str | None,
) -> str:
    # ADR-084 embedding point 28 (Form A, inline): the target role title is the
    # posting's own text, and this prompt's output is written straight into the
    # candidate's CV section.
    from applire.services.untrusted_text import fence_inline

    lines = [f"Abschnitt: {section_label}"]
    if role_title:
        lines.append(f"Zielrolle: {fence_inline(role_title)}")
    lines.append(f"Aktueller Inhalt:\n{section_content}")
    if gap_ids:
        lines.append(f"Zu schließende Lücken: {', '.join(gap_ids)}")
    if directions:
        lines.append(f"Anweisungen des Nutzers: {directions}")
    lines.append(
        "\nSchreibe den Abschnitt neu und berücksichtige dabei die Anweisungen und Lücken. "
        "Gib nur den verbesserten Text aus."
    )
    return "\n".join(lines)
