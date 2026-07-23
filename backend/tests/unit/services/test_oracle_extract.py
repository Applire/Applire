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
    split_sentences,
    split_clauses,
)


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
    ],
)
def test_split_sentences(text, expected):
    assert split_sentences(text) == expected


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
            "At BioNTech, I led AI automation projects, with comprehensive "
            "testing, observability, and reliability practices.",
            [
                "At BioNTech, I led AI automation projects",
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
    ],
)
def test_split_clauses(text, expected):
    assert split_clauses(text) == expected


def test_split_clauses_never_returns_empty_for_nonempty_text():
    assert split_clauses("") == []
    assert split_clauses("   ") == []


# ── #237 employer anchoring for letter claims ─────────────────────────────────

PROFILE_WITH_TWO_EMPLOYERS = {
    "work_experience": [
        {"id": "w-biontech", "company": "BioNTech", "role": "Automation Lead"},
        {"id": "w-acme", "company": "Acme GmbH", "role": "Engineer"},
    ],
}


def test_extract_from_letter_anchors_en_employer_prefix():
    letter = {
        "body": {
            "paragraphs": [
                "At BioNTech, I led AI automation projects, with comprehensive "
                "testing, observability, and reliability practices."
            ]
        }
    }
    claims = extract_claims_from_letter(letter, PROFILE_WITH_TWO_EMPLOYERS)
    assert claims  # sanity: decomposition produced something
    assert all(c.source_experience_id == "w-biontech" for c in claims)


def test_extract_from_letter_anchors_de_employer_prefix():
    letter = {
        "body": {"paragraphs": ["Bei BioNTech habe ich die Automatisierung geleitet."]}
    }
    claims = extract_claims_from_letter(letter, PROFILE_WITH_TWO_EMPLOYERS)
    assert claims
    assert all(c.source_experience_id == "w-biontech" for c in claims)


def test_extract_from_letter_ambiguous_two_employers_stays_unanchored():
    letter = {
        "body": {
            "paragraphs": [
                "I moved from BioNTech to Acme GmbH and grew in both roles."
            ]
        }
    }
    claims = extract_claims_from_letter(letter, PROFILE_WITH_TWO_EMPLOYERS)
    assert claims
    assert all(c.source_experience_id is None for c in claims)


def test_extract_from_letter_no_profile_stays_unanchored():
    letter = {"body": {"paragraphs": ["At BioNTech, I led automation projects."]}}
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
