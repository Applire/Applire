# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#617 axis (a) — the posting's language is a COMPUTED FACT in the user prompt,
not a rule the model re-derives on every call (ADR-064's state-the-fact shape).

Why this is a fact and not prose, with the number that decided it: the first
version was a ~1,400-char rule in the system prompt. Measured extractor-only on
the real provider, n=5 per arm, `openai/gpt-5.6-luna`:

    controlling_emma_de (German)  language agreement across runs  4/5 -> 5/5
                                  required_skills Jaccard         0.47 -> 0.81
    it_backend_daniel  (English)  keywords Jaccard                0.48 -> 0.20

On an already-English posting the rule was a no-op that still cost attention.
`detect_language` already runs on this exact text one line later (it stamps
`jd_language`), so the answer is stated instead of argued for.
"""
import pytest


_DE = (
    "Leiter Operations (m/w/d) — Rheinwerk Verpackungen GmbH. Führung und "
    "Entwicklung der Schicht- und Bereichsleiter. Mehrjährige Führungserfahrung "
    "in der Produktion eines Industrieunternehmens."
)
_EN = (
    "Senior Backend Engineer at NovaPay. You will design and operate the services "
    "that move money, working with Python, Kafka and Kubernetes, and you will own "
    "them in production."
)


@pytest.mark.parametrize(
    "jd, expected, forbidden", [(_DE, "German", "English"), (_EN, "English", "German")]
)
def test_the_user_prompt_states_the_detected_posting_language(jd, expected, forbidden):
    from applire.prompts.job_analysis import build_user_prompt
    from applire.utils.language_detection import detect_language

    prompt = build_user_prompt(jd)
    assert detect_language(jd) == ("de" if expected == "German" else "en")
    assert f"POSTING LANGUAGE: {expected}." in prompt
    # One language, stated once — a prompt naming both teaches nothing.
    assert f"POSTING LANGUAGE: {forbidden}" not in prompt


def test_the_language_fact_is_OUR_instruction_not_quoted_posting_text():
    """ADR-084 Form B. The fact must sit OUTSIDE the fence: inside it, a hostile
    posting could restate or contradict it and the model would read our
    instruction and the attacker's with the same authority."""
    from applire.prompts.job_analysis import build_user_prompt

    prompt = build_user_prompt(_DE)
    assert prompt.index("POSTING LANGUAGE:") < prompt.index("JOB DESCRIPTION")


def test_the_language_fact_exempts_the_controlled_vocabularies():
    """The corpus already shows the model writing "Leitung" — and a whole German
    sentence — into seniority_level, and `_seniority_threshold_met` matches
    English keys. A language instruction that does not carve these out makes that
    failure MORE likely, not less."""
    from applire.prompts.job_analysis import build_user_prompt

    prompt = build_user_prompt(_DE)
    fact = prompt.split("JOB DESCRIPTION")[0]
    assert "seniority_level" in fact
    assert "stay English" in fact


def test_the_fact_names_exactly_the_four_free_text_list_fields():
    """Scope is the point: these four are matched literally against the
    candidate's documents; everything else is a quote, a number or an enum."""
    from applire.prompts.job_analysis import build_user_prompt

    fact = build_user_prompt(_EN).split("JOB DESCRIPTION")[0]
    for field in ("required_skills", "nice_to_have_skills", "keywords",
                  "company_culture_signals"):
        assert field in fact, f"{field} missing from the language fact"
    assert "berufsbild_label" not in fact  # always German by definition
    assert "language_requirement" not in fact  # its own format, not a term list
