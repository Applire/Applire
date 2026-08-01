# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US243 — deterministic claim extraction for the Truthfulness Oracle.

Structured documents (tailored_data / letter_data) are segmented into claims
with NO LLM call; the free-prose fallback is bounded-output-by-contract
(ADR-047) and only fires for oversized unsplittable prose.
"""
import pytest

from applire.services.oracle.extract import (
    extract_claims_from_tailored,
    extract_claims_from_letter,
    extract_claims_from_text,
    letter_named_experience_ids,
    split_sentences,
    split_clauses,
)
from applire.services.oracle.matchers.figures import extract_figures


class _SpyProvider:
    """Counts calls; fails the test if the deterministic path touches the LLM."""

    def __init__(self, response: dict | None = None):
        self.calls: list[dict] = []
        self._response = response or {"claims": []}

    async def aparse_json(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return self._response

    async def acomplete(self, prompt, **kwargs):  # pragma: no cover - unused
        self.calls.append({"prompt": prompt, **kwargs})
        return ""


# ── sentence splitting ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text,expected",
    [
        ("One. Two! Three?", ["One.", "Two!", "Three?"]),
        (
            "Led migrations, z.B. SAP-Rollouts. Reduced costs by 12%.",
            ["Led migrations, z.B. SAP-Rollouts.", "Reduced costs by 12%."],
        ),
        (
            "Improved uptime, e.g. via failover tests. Shipped v2.",
            ["Improved uptime, e.g. via failover tests.", "Shipped v2."],
        ),
        ("Delivered 3.5 years of support.", ["Delivered 3.5 years of support."]),
        ("", []),
        ("   ", []),
        # #398 / charter run 12 (real provider, 2026-08-01): "Mio." split the
        # sentence, orphaning "€" — extract_figures then produced a plain
        # ``number`` figure ("78") instead of a ``currency`` figure ("78m"),
        # which never matched the vault's indexed currency/78m entry and
        # produced a false "No vault evidence for figure(s): 78".
        (
            "Ich verantwortete den rollierenden Forecast für 78 Mio. € "
            "Umsatz als Key-Userin in SAP CO/FI.",
            [
                "Ich verantwortete den rollierenden Forecast für 78 Mio. € "
                "Umsatz als Key-Userin in SAP CO/FI."
            ],
        ),
        (
            "Wir planten 2 Mrd. € Investitionsvolumen. Das Team wuchs auf "
            "5 Tsd. Mitarbeiter.",
            [
                "Wir planten 2 Mrd. € Investitionsvolumen.",
                "Das Team wuchs auf 5 Tsd. Mitarbeiter.",
            ],
        ),
        # Titled salutations (separately-tracked bug, #398 board item): a
        # bare "Mr." must not read as a sentence terminator either.
        (
            "Ich sprach mit Mr. Smith über das Projekt.",
            ["Ich sprach mit Mr. Smith über das Projekt."],
        ),
        (
            "I met with Mrs. Jones and Ms. Lee today.",
            ["I met with Mrs. Jones and Ms. Lee today."],
        ),
    ],
)
def test_split_sentences(text, expected):
    assert split_sentences(text) == expected


def test_split_sentences_run12_currency_survives_into_one_currency_figure():
    """#398 / charter run 12 ground truth: the whole "78 Mio. €" span must
    stay inside a single sentence so ``extract_figures`` classifies it as a
    ``currency`` figure ("78m"), never a bare ``number`` ("78") that can
    never match the vault's indexed currency entry."""
    text = (
        "Ich verantwortete den rollierenden Forecast für 78 Mio. € Umsatz "
        "als Key-Userin in SAP CO/FI."
    )
    sentences = split_sentences(text)
    assert len(sentences) == 1
    figures = extract_figures(sentences[0])
    assert ("currency", "78m") in [(f.kind, f.value) for f in figures]
    assert ("number", "78") not in [(f.kind, f.value) for f in figures]


# ── tailored_data (generated CV) ──────────────────────────────────────────────

TAILORED = {
    "contact": {"name": "Anna Bauer"},
    "summary": "Engineering leader with 10 years of experience. Reduced costs by 30%.",
    "work_history": [
        {
            "id": "w1",
            "company": "Acme",
            "role": "Head of IT",
            "bullets": ["Cut deployment time by 40%.", ""],
            "projects": [{"name": "Rollout", "bullets": ["Migrated 200 users."]}],
        }
    ],
    "projects": [{"name": "OSS", "bullets": ["Maintained a plugin."]}],
    "skills": ["Python", "Kubernetes", ""],
    "education": [],
    "languages": [],
    "certifications": [],
}


def test_extract_from_tailored_locations_and_kinds():
    claims = extract_claims_from_tailored(TAILORED)
    by_loc = {c.location: c for c in claims}

    assert by_loc["summary[0]"].text == "Engineering leader with 10 years of experience."
    assert by_loc["summary[1]"].text == "Reduced costs by 30%."
    assert by_loc["work_history[0].bullets[0]"].kind == "bullet"
    assert by_loc["work_history[0].projects[0].bullets[0]"].text == "Migrated 200 users."
    assert by_loc["projects[0].bullets[0]"].text == "Maintained a plugin."
    assert by_loc["skills[0]"].kind == "skill"
    assert by_loc["skills[1]"].text == "Kubernetes"
    # Empty strings never become claims.
    assert "work_history[0].bullets[1]" not in by_loc
    assert "skills[2]" not in by_loc


def test_extract_from_tailored_is_deterministic_no_llm():
    """Structured extraction takes no provider at all — LLM-free by signature."""
    import inspect

    sig = inspect.signature(extract_claims_from_tailored)
    assert "provider" not in sig.parameters


# ── letter_data (generated cover letter) ──────────────────────────────────────

LETTER = {
    "header": {"name": "Anna Bauer"},
    "body": {
        "paragraphs": [
            "I led the SAP rollout. It cut onboarding time by half.",
            "",
            "I look forward to hearing from you.",
        ]
    },
    "signature": {"closing": "Mit freundlichen Grüßen"},
}


def test_extract_from_letter_paragraph_sentences():
    claims = extract_claims_from_letter(LETTER)
    locs = [c.location for c in claims]
    assert locs == [
        "body.paragraphs[0][0]",
        "body.paragraphs[0][1]",
        "body.paragraphs[2][0]",
    ]
    assert claims[0].text == "I led the SAP rollout."
    assert all(c.kind == "sentence" for c in claims)


def test_extract_from_letter_tolerates_missing_body():
    assert extract_claims_from_letter({}) == []
    assert extract_claims_from_letter({"body": {}}) == []


# ── raw external text (US248 audit-any-document) ─────────────────────────────

@pytest.mark.asyncio
async def test_extract_from_text_bullets_and_sentences():
    text = (
        "Senior engineer with a decade of experience. Based in Berlin.\n"
        "- Reduced costs by 30%\n"
        "• Led a team of 12\n"
    )
    spy = _SpyProvider()
    claims = await extract_claims_from_text(text, provider=spy)
    texts = [c.text for c in claims]
    assert "Senior engineer with a decade of experience." in texts
    assert "Reduced costs by 30%" in texts
    assert "Led a team of 12" in texts
    kinds = {c.text: c.kind for c in claims}
    assert kinds["Led a team of 12"] == "bullet"
    # Splittable text never reaches the LLM.
    assert spy.calls == []


@pytest.mark.asyncio
async def test_extract_from_text_llm_fallback_is_bounded(monkeypatch):
    """A prose blob with no sentence boundaries above the size threshold uses
    the bounded ADR-047 fallback — and only then."""
    from applire.services.oracle import extract as mod

    monkeypatch.setattr(mod, "ORACLE_PROSE_FALLBACK_CHARS", 50)
    blob = "led everything and delivered many results without punctuation " * 4
    spy = _SpyProvider({"claims": ["led everything", "delivered many results"]})
    claims = await extract_claims_from_text(blob, provider=spy)
    assert [c.text for c in claims] == ["led everything", "delivered many results"]
    assert len(spy.calls) == 1
    # Bounded-output-by-contract: an explicit max_tokens cap is always set.
    assert spy.calls[0]["max_tokens"] <= mod.ORACLE_SEGMENT_MAX_TOKENS


@pytest.mark.asyncio
async def test_segmentation_calls_are_budget_capped(monkeypatch):
    """Adversarial review 2026-07-18 MAJOR-2: the fallback is bounded in CALL
    COUNT per document, not only in per-call output — beyond the budget a
    qualifying block degrades to one claim instead of another LLM call."""
    from applire.services.oracle import extract as mod

    monkeypatch.setattr(mod, "ORACLE_PROSE_FALLBACK_CHARS", 50)
    monkeypatch.setattr(mod, "ORACLE_MAX_SEGMENT_CALLS", 2)
    long_line = "led everything and delivered many results without punctuation " * 4
    text = "\n".join([long_line, long_line, long_line])
    spy = _SpyProvider({"claims": ["part one", "part two"]})
    claims = await extract_claims_from_text(text, provider=spy)
    assert len(spy.calls) == 2  # hard cap
    # two segmented lines (2 claims each) + the third line as one whole claim
    assert len(claims) == 5
    assert claims[-1].text.startswith("led everything")


@pytest.mark.asyncio
async def test_extract_from_text_without_provider_degrades_to_single_claim(monkeypatch):
    from applire.services.oracle import extract as mod

    monkeypatch.setattr(mod, "ORACLE_PROSE_FALLBACK_CHARS", 50)
    blob = "led everything and delivered many results without punctuation " * 4
    claims = await extract_claims_from_text(blob, provider=None)
    assert len(claims) == 1
    assert claims[0].text.startswith("led everything")


@pytest.mark.asyncio
async def test_extract_from_text_llm_failure_degrades(monkeypatch):
    from applire.services.oracle import extract as mod

    monkeypatch.setattr(mod, "ORACLE_PROSE_FALLBACK_CHARS", 50)

    class _Boom:
        async def aparse_json(self, prompt, **kwargs):
            raise RuntimeError("provider down")

    blob = "led everything and delivered many results without punctuation " * 4
    claims = await extract_claims_from_text(blob, provider=_Boom())
    assert len(claims) == 1  # graceful degradation, never an exception


# ── #237 clause-level decomposition (letter narrative sentences) ─────────────

@pytest.mark.parametrize(
    "text,expected",
    [
        # ", with" boundary — the exact F14 blend shape.
        (
            "At NordPharm, I led AI automation projects, with comprehensive "
            "testing, observability, and reliability practices.",
            [
                "At NordPharm, I led AI automation projects",
                "comprehensive testing, observability",
                "reliability practices.",
            ],
        ),
        # Semicolon boundary.
        (
            "I led the rollout; it cut onboarding time in half.",
            ["I led the rollout", "it cut onboarding time in half."],
        ),
        # "including" boundary.
        (
            "I modernised the stack, including the CI pipeline.",
            ["I modernised the stack", "the CI pipeline."],
        ),
        # Spaced em-dash boundary (real-model text uses these).
        (
            "I led the migration — cutting costs by 30 percent.",
            ["I led the migration", "cutting costs by 30 percent."],
        ),
        # DE: ", und" boundary.
        (
            "Ich leitete das Projekt, und ich verbesserte die Prozesse.",
            ["Ich leitete das Projekt", "ich verbesserte die Prozesse."],
        ),
        # DE: "; " boundary.
        (
            "Ich leitete das Team; wir lieferten pünktlich.",
            ["Ich leitete das Team", "wir lieferten pünktlich."],
        ),
        # No boundary at all — the whole sentence is the only clause.
        (
            "I look forward to hearing from you.",
            ["I look forward to hearing from you."],
        ),
        # Unicode apostrophe must not confuse the splitter or get dropped.
        (
            "I’m proud of the team’s results, with strong retention.",
            ["I’m proud of the team’s results", "strong retention."],
        ),
        # #237 round-3 (live MCP probe residual): a PAIRED, unspaced em-dash
        # marks a parenthetical aside — must isolate the aside as its OWN
        # clause rather than let an Oxford-comma "and" INSIDE it win instead
        # (which used to fragment the aside's own enumeration list, "GxP
        # documentation workflows, audit trails" | "and validation reports
        # ...", mid-list).
        (
            "my regulated-industry background—building GxP documentation "
            "workflows, audit trails, and validation reports for "
            "pharma-adjacent customers—gives me hands-on experience.",
            [
                "my regulated-industry background",
                "building GxP documentation workflows, audit trails, and "
                "validation reports for pharma-adjacent customers",
                "gives me hands-on experience.",
            ],
        ),
    ],
)
def test_split_clauses(text, expected):
    assert split_clauses(text) == expected


def test_split_clauses_single_unpaired_em_dash_uses_normal_boundary_rules():
    """Over-relax guard: exactly ONE em-dash (not a pair) must still use the
    pre-existing spaced-dash boundary rule — the paired-aside rule is scoped
    to EXACTLY two em-dashes only."""
    assert split_clauses("I led the migration — cutting costs by 30 percent.") == [
        "I led the migration",
        "cutting costs by 30 percent.",
    ]


def test_split_clauses_en_dash_date_range_is_not_split():
    """Over-relax guard: an unspaced en-dash date range ("2020–2023") must
    never be treated as a clause boundary — the paired-aside rule is scoped
    to EM-DASH specifically (U+2014), not the broader ``_DASH_CHARS`` set
    that also carries en-dash (U+2013, commonly a date-range separator)."""
    assert split_clauses("I worked there from 2020–2023 and grew the team.") == [
        "I worked there from 2020–2023 and grew the team."
    ]


def test_split_clauses_never_returns_empty_for_nonempty_text():
    assert split_clauses("") == []
    assert split_clauses("   ") == []


# ── #237 employer anchoring for letter claims ─────────────────────────────────

PROFILE_WITH_TWO_EMPLOYERS = {
    "work_experience": [
        {"id": "w-nordpharm", "company": "NordPharm", "role": "Automation Lead"},
        {"id": "w-acme", "company": "Acme GmbH", "role": "Engineer"},
    ],
}


def test_extract_from_letter_anchors_en_employer_prefix():
    letter = {
        "body": {
            "paragraphs": [
                "At NordPharm, I led AI automation projects, with comprehensive "
                "testing, observability, and reliability practices."
            ]
        }
    }
    claims = extract_claims_from_letter(letter, PROFILE_WITH_TWO_EMPLOYERS)
    assert claims  # sanity: decomposition produced something
    assert all(c.source_experience_id == "w-nordpharm" for c in claims)


def test_extract_from_letter_anchors_de_employer_prefix():
    letter = {
        "body": {"paragraphs": ["Bei NordPharm habe ich die Automatisierung geleitet."]}
    }
    claims = extract_claims_from_letter(letter, PROFILE_WITH_TWO_EMPLOYERS)
    assert claims
    assert all(c.source_experience_id == "w-nordpharm" for c in claims)


def test_extract_from_letter_ambiguous_two_employers_stays_unanchored():
    letter = {
        "body": {
            "paragraphs": [
                "I moved from NordPharm to Acme GmbH and grew in both roles."
            ]
        }
    }
    claims = extract_claims_from_letter(letter, PROFILE_WITH_TWO_EMPLOYERS)
    assert claims
    assert all(c.source_experience_id is None for c in claims)


def test_extract_from_letter_no_profile_stays_unanchored():
    letter = {"body": {"paragraphs": ["At NordPharm, I led automation projects."]}}
    claims = extract_claims_from_letter(letter)
    assert all(c.source_experience_id is None for c in claims)


def test_extract_from_letter_single_clause_sentence_keeps_kind_sentence():
    """Backward compatibility: a sentence with no clause boundary is still a
    single 'sentence' claim at the same location as before #237."""
    letter = {"body": {"paragraphs": ["I led the SAP rollout."]}}
    claims = extract_claims_from_letter(letter)
    assert len(claims) == 1
    assert claims[0].kind == "sentence"
    assert claims[0].location == "body.paragraphs[0][0]"


def test_extract_from_letter_multi_clause_sentence_uses_kind_clause():
    letter = {
        "body": {
            "paragraphs": [
                "I led the rollout; it cut onboarding time in half."
            ]
        }
    }
    claims = extract_claims_from_letter(letter)
    assert len(claims) == 2
    assert all(c.kind == "clause" for c in claims)
    assert claims[0].location == "body.paragraphs[0][0].clauses[0]"
    assert claims[1].location == "body.paragraphs[0][0].clauses[1]"


# ── #248 — legal-form-suffix tolerance + sentence_named_ids + clause anchors ──
#
# Live-reproduced 2026-07-24 (generated_cover_letters 37ee8f77-...): the vault
# stores "NordPharm SE" (the legal entity name), but a real-model letter
# naturally drops the legal-form suffix ("at NordPharm"). The OLD exact-name
# anchor match therefore treated the sentence as naming NO employer at all —
# not just failing to anchor it (`source_experience_id` stays `None` by
# design, matching #237's own "fail open, never guess" rule for the STRICT
# anchor), but also making the letter-wide `letter_named_experience_ids` scan
# blind to NordPharm entirely, which silently widened the #243-adjacent
# ownership check's "letter names exactly one employer" escape hatch to fire
# on a letter that, read narratively, names TWO. `Claim.sentence_named_ids`
# (loose — ambiguity across same-company duplicate ids tolerated, unlike the
# strict per-claim anchor) is the new, additive signal that lets
# ``audit._unattributable_evidence_flag`` tell "this clause's OWN sentence
# already names its true owner" apart from "the letter names an owner
# somewhere, but not here" — see test_oracle_letter_nonfigure_ownership.py for
# the full audit-level regression.

PROFILE_LEGAL_SUFFIX = {
    "work_experience": [
        {"id": "w-nordpharm", "company": "NordPharm SE", "role": "Automation Lead"},
        {"id": "w-applire", "company": "Applire", "role": "Founder"},
    ],
}


def test_letter_named_experience_ids_tolerates_legal_form_suffix():
    """The letter-wide scan finds NordPharm even though it never spells out
    the legal-form suffix stored in the vault ("NordPharm SE")."""
    letter = {
        "body": {
            "paragraphs": [
                "In my recent role at NordPharm, I led automation projects.",
                "As Founder of Applire, I built a platform.",
            ]
        }
    }
    ids = letter_named_experience_ids(letter, PROFILE_LEGAL_SUFFIX)
    assert ids == {"w-nordpharm", "w-applire"}


def test_strict_anchor_now_tolerates_legal_form_suffix_via_current_role_tiebreak():
    """SUPERSEDES the #248-era pin that the STRICT anchor stayed ``None`` on
    a bare "NordPharm" mention. #237 run-4 residual (live self-audit,
    2026-07-24): that exact-name-only behaviour was disproved by production
    data — "NordPharm SE" vs. a letter that simply says "NordPharm" is the
    COMMON case, not a rare edge, and starved the attribution matcher of an
    anchor on almost every real NordPharm-mentioning sentence in a realistic
    letter (10/14 claims unverifiable on an honest letter). The strict
    anchor now retries against the legal-form-suffix-tolerant candidate set
    when the exact set matches nothing at all (see
    ``extract._find_employer_anchor``'s #237 docstring section) — for a
    single-role company like this fixture's, that alone resolves it (no
    tie-break needed, since exactly one loose candidate matches). The
    ownership-check-only signal this test used to isolate,
    ``sentence_named_ids``, still carries the same id (loose matching was
    already this permissive) — it and the strict anchor now agree here."""
    letter = {
        "body": {
            "paragraphs": [
                "In my recent role at NordPharm, I initiated an agentic GenAI "
                "system automating validation documentation."
            ]
        }
    }
    claims = extract_claims_from_letter(letter, PROFILE_LEGAL_SUFFIX)
    assert len(claims) == 1
    assert claims[0].source_experience_id == "w-nordpharm"
    assert claims[0].sentence_named_ids == frozenset({"w-nordpharm"})


def test_strict_anchor_stays_unanchored_on_genuine_same_company_multi_role_ambiguity():
    """Over-drop guard for the #237 run-4 widening above: when a company was
    held across MULTIPLE internal roles and none of them is marked
    ``is_current``, the tie-break can't decide either — the strict anchor
    must still fail open, exactly as before."""
    profile = {
        "work_experience": [
            {"id": "w-old1", "company": "NordPharm SE", "role": "System Engineer"},
            {"id": "w-old2", "company": "NordPharm SE", "role": "Architect"},
        ],
    }
    letter = {"body": {"paragraphs": ["At NordPharm, I led automation projects."]}}
    claims = extract_claims_from_letter(letter, profile)
    assert all(c.source_experience_id is None for c in claims)


def test_sentence_named_ids_empty_when_sentence_names_no_employer():
    """The exact #248 blend shape: a bare continuation sentence ("This
    system...") names no employer at all — the loose signal must stay empty,
    not silently inherit anything from neighbouring sentences."""
    letter = {
        "body": {
            "paragraphs": [
                "This system, running on Databricks, demonstrated my "
                "hands-on expertise, with a deterministic verification "
                "layer ensuring trustworthiness."
            ]
        }
    }
    claims = extract_claims_from_letter(letter, PROFILE_LEGAL_SUFFIX)
    assert claims
    assert all(c.sentence_named_ids == frozenset() for c in claims)


# ── #248 direction 1 — clause-level anchoring for a two-employer sentence ────

PROFILE_TWO_EMPLOYERS_CLAUSE = {
    "work_experience": [
        {"id": "w-nordpharm", "company": "NordPharm", "role": "Automation Lead"},
        {"id": "w-acme", "company": "Acme GmbH", "role": "Engineer"},
    ],
}


def test_two_employer_sentence_anchors_each_clause_independently_en():
    """A sentence naming BOTH employers stays ambiguous at the SENTENCE
    level (unchanged #237 behaviour), but each CLAUSE that names exactly one
    of them now gets that clause's own anchor — the writer-blend signature
    direction: a clause mentioning neither stays unanchored."""
    letter = {
        "body": {
            "paragraphs": [
                "At NordPharm, I led automation, and at Acme GmbH, I "
                "redesigned onboarding."
            ]
        }
    }
    claims = extract_claims_from_letter(letter, PROFILE_TWO_EMPLOYERS_CLAUSE)
    assert len(claims) == 2
    assert claims[0].source_experience_id == "w-nordpharm"
    assert claims[1].source_experience_id == "w-acme"


def test_two_employer_sentence_anchors_each_clause_independently_de():
    """DE 'bei X' variant of the same two-employer, per-clause shape."""
    letter = {
        "body": {
            "paragraphs": [
                "Bei NordPharm habe ich die Automatisierung geleitet, und "
                "bei Acme GmbH habe ich das Onboarding neu gestaltet."
            ]
        }
    }
    claims = extract_claims_from_letter(letter, PROFILE_TWO_EMPLOYERS_CLAUSE)
    assert len(claims) == 2
    assert claims[0].source_experience_id == "w-nordpharm"
    assert claims[1].source_experience_id == "w-acme"


def test_single_employer_multi_clause_sentence_anchor_unchanged():
    """A single-employer sentence split into several clauses (comma + 'with')
    keeps anchoring EVERY clause via the sentence-level anchor, same as
    before #248 — the new per-clause fallback only ever engages when the
    SENTENCE itself couldn't decide."""
    letter = {
        "body": {
            "paragraphs": [
                "At NordPharm, I led automation, with strong reliability "
                "practices."
            ]
        }
    }
    claims = extract_claims_from_letter(letter, PROFILE_TWO_EMPLOYERS_CLAUSE)
    assert len(claims) == 2
    assert all(c.source_experience_id == "w-nordpharm" for c in claims)


def test_clause_naming_neither_employer_stays_unanchored_in_two_employer_sentence():
    """Within an ambiguous two-employer sentence, a clause naming NEITHER
    employer stays unanchored (falls through to the ownership check, not a
    guessed anchor)."""
    letter = {
        "body": {
            "paragraphs": [
                "I have worked at both NordPharm and Acme GmbH, and it went "
                "very well overall."
            ]
        }
    }
    claims = extract_claims_from_letter(letter, PROFILE_TWO_EMPLOYERS_CLAUSE)
    assert len(claims) == 2
    assert claims[0].source_experience_id is None
    assert claims[1].source_experience_id is None


# ── #372 (2) — distinctive-token employer-anchor fallback (2026-08-01 recon) ──
#
# Recon-verified 2026-08-01: matching was one-directional — the FULL vault
# company name (minus a trailing legal-form suffix, #237) had to appear
# verbatim in the sentence. A letter's natural shortened mention ("Bei
# Nordkette bewährte sich…" for vault "Nordkette Systemtechnik GmbH") never
# matched at all (not even via the #248 loose/legal-form pass, since
# "Nordkette Systemtechnik" itself still never appears verbatim) →
# source_experience_id stayed None → downstream false-flag. The fallback
# tries each vault company's DISTINCTIVE leading token only after the
# stricter exact/legal-form passes found nothing.

PROFILE_SHORTENED_MENTION = {
    "work_experience": [
        {
            "id": "w-nordkette",
            "company": "Nordkette Systemtechnik GmbH",
            "role": "Consultant",
        },
        {"id": "w-applire", "company": "Applire", "role": "Founder"},
    ],
}


def test_distinctive_token_fallback_resolves_shortened_mention():
    letter = {
        "body": {"paragraphs": ["Bei Nordkette bewährte sich mein Ansatz."]}
    }
    claims = extract_claims_from_letter(letter, PROFILE_SHORTENED_MENTION)
    assert claims
    assert all(c.source_experience_id == "w-nordkette" for c in claims)


def test_distinctive_token_fallback_never_overrides_exact_match():
    """Guard (c): the fallback must never fire when a stricter pass already
    resolved the anchor — the full company name is present verbatim here,
    so the distinctive-token pass is never even consulted."""
    letter = {
        "body": {
            "paragraphs": [
                "Bei Nordkette Systemtechnik GmbH bewährte sich mein Ansatz."
            ]
        }
    }
    claims = extract_claims_from_letter(letter, PROFILE_SHORTENED_MENTION)
    assert claims
    assert all(c.source_experience_id == "w-nordkette" for c in claims)


def test_distinctive_token_fallback_ambiguous_across_two_companies_stays_unanchored():
    """Guard (a): a shortened form resolving to TWO different vault
    employers must resolve to NO anchor, mirroring the existing multi-match
    ambiguity behaviour."""
    profile = {
        "work_experience": [
            {
                "id": "w-nordkette",
                "company": "Nordkette Systemtechnik GmbH",
                "role": "Consultant",
            },
            {
                "id": "w-ostsee",
                "company": "Ostsee Datentechnik AG",
                "role": "Consultant",
            },
        ],
    }
    letter = {
        "body": {
            "paragraphs": [
                "Bei Nordkette und Ostsee habe ich als Berater gearbeitet."
            ]
        }
    }
    claims = extract_claims_from_letter(letter, profile)
    assert claims
    assert all(c.source_experience_id is None for c in claims)


def test_distinctive_token_fallback_skips_generic_stoplist_token():
    """Guard (b): a leading token in the generic-company-word stoplist
    ("Service") must never anchor, even though it is >= 4 chars and
    otherwise unique across the candidate set."""
    profile = {
        "work_experience": [
            {
                "id": "w-service",
                "company": "Service Solutions GmbH",
                "role": "Consultant",
            },
        ],
    }
    letter = {"body": {"paragraphs": ["Bei Service habe ich mitgewirkt."]}}
    claims = extract_claims_from_letter(letter, profile)
    assert claims
    assert all(c.source_experience_id is None for c in claims)


def test_distinctive_token_fallback_skips_token_shared_by_two_company_names():
    """Guard (b): a token that appears in MORE THAN ONE company's name is
    excluded outright, even when it is not in the stoplist and even when it
    happens to be the leading token of only one of them."""
    profile = {
        "work_experience": [
            {
                "id": "w-1",
                "company": "Nordkette Systemtechnik GmbH",
                "role": "Consultant",
            },
            {"id": "w-2", "company": "Baltic Nordkette AG", "role": "Consultant"},
        ],
    }
    letter = {"body": {"paragraphs": ["Bei Nordkette habe ich gearbeitet."]}}
    claims = extract_claims_from_letter(letter, profile)
    assert claims
    assert all(c.source_experience_id is None for c in claims)


def test_distinctive_token_fallback_existing_exact_and_legal_form_tests_unchanged():
    """Sanity pin: the pre-existing exact-name anchor still resolves without
    ever touching the new fallback path."""
    letter = {
        "body": {
            "paragraphs": ["Bei NordPharm habe ich die Automatisierung geleitet."]
        }
    }
    claims = extract_claims_from_letter(letter, PROFILE_WITH_TWO_EMPLOYERS)
    assert claims
    assert all(c.source_experience_id == "w-nordpharm" for c in claims)
