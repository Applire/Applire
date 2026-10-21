# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#701 (re-scoped, founder ruling H-1 = B, ADR-052 amended 2026-10-16).

A claim that names a standard/regulation identifier (ISO/DIN/EN/CFR …) whose
digits the vault never states cannot be graded ``grounded`` — not by its other
figures, not by the prose chain, not by a model. Where it would otherwise be
``grounded`` it reads ``unbacked`` (checker ``numbers``) with a detail naming
the identifier.

Why it exists: #374 excludes an identifier's digits from figure matching, so
before this rule NOTHING checked them, and adversarial probe A2's fabricated
"ISO-45001-Lead-Auditor-Zertifikat" rode on a true "38 Mitarbeitenden".

Every guard test asserts its BASELINE first: the same claim through
``_verify_claim_unchecked`` (the pre-rule grading) is ``grounded`` — so the
test proves the rule, not a fixture that was never grounded to begin with.

The three corpus true positives (writer output mistyping the vault's own
"ISO 45001" as ISO-4501 / ISO-450001 / ISO-45000) are pinned as SYNTHETIC
restatements, never as the captured text.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document, verify_claim
from applire.services.oracle.audit import (
    IDENTIFIER_ABSENT_VERDICT,
    _unmentioned_identifiers,
    _verify_claim_unchecked,
)
from applire.services.oracle.extract import Claim
from applire.services.oracle.matchers import build_vault_index
from applire.services.oracle.selfaudit import build_self_audit_report

pytestmark = pytest.mark.asyncio

_W = "11111111-1111-1111-1111-111111111111"

# A synthetic operations profile: 38 Mitarbeitende are real; no ISO 45001
# anywhere (the A2 vault shape).
PROFILE = {
    "personal_info": {"name": "Jonas Keller"},
    "professional_summary": {
        "de": "Produktionsleiter in der Kunststofffertigung mit Fokus auf Lean."
    },
    "work_experience": [
        {
            "id": _W,
            "company": "Brennwerk Kunststofftechnik GmbH",
            "role": "Produktionsleiter",
            "start_date": "2016-03",
            "responsibilities": [
                "Zwei Fertigungsbereiche mit 38 Mitarbeitenden im Dreischichtbetrieb gefuehrt.",
            ],
            "achievements": [
                "Ausschussquote von 4,1 % auf 2,3 % gesenkt.",
            ],
        }
    ],
    "skills": [{"name": "SMED"}, {"name": "ISO 9001"}],
}

A2_LONG = (
    "Zwei Fertigungsbereiche mit 38 Mitarbeitenden gefuehrt und dabei den "
    "ISO-45001-Lead-Auditor-Zertifikat erworben"
)
A2_SHORT = "Zwei Fertigungsbereiche mit 38 Mitarbeitenden gefuehrt, ISO-45001-Lead-Auditor"


async def _baseline(text: str, profile=PROFILE, **kw):
    return await _verify_claim_unchecked(Claim(text=text, location="claim[0]", **kw), profile)


# ── the rule itself ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [A2_LONG, A2_SHORT], ids=["a2-long", "a2-short-tail"])
async def test_a2_fabricated_certificate_no_longer_grounded(text):
    base = await _baseline(text)
    assert base.verdict == "grounded", base  # the #701 hole, reproduced
    v = await verify_claim(text, PROFILE)
    assert v.verdict == "unbacked" == IDENTIFIER_ABSENT_VERDICT
    assert v.checker == "numbers"
    assert "ISO-45001" in v.detail
    assert "never mentions" in v.detail


async def test_detail_sentence_is_the_ruled_wording():
    v = await verify_claim(A2_LONG, PROFILE)
    assert v.detail == (
        "Names ISO-45001 — a standard identifier your profile never mentions, "
        "so the claim's other figures cannot back it."
    )


async def test_the_same_sentence_is_grounded_when_the_vault_holds_the_certificate():
    profile = {
        **PROFILE,
        "certifications": [{"name": "ISO 45001 Lead Auditor", "issuing_organization": "TÜV"}],
    }
    v = await verify_claim(A2_LONG, profile)
    assert v.verdict == "grounded", v


@pytest.mark.parametrize(
    "vault_form,claim_form",
    [
        ("ISO/IEC 27001", "ISO 27001"),
        ("DIN EN ISO 13485", "ISO 13485"),
        ("ISO45001", "ISO-45001"),  # separator-less in the vault: bare-token fallback
        ("21 CFR Part 11", "21 CFR"),
    ],
)
async def test_another_spelling_of_the_same_standard_counts_as_mentioned(vault_form, claim_form):
    profile = {
        **PROFILE,
        "work_experience": [
            {
                **PROFILE["work_experience"][0],
                "responsibilities": [
                    "Zwei Fertigungsbereiche mit 38 Mitarbeitenden im Dreischichtbetrieb "
                    f"gefuehrt, Audits nach {vault_form} begleitet.",
                ],
            }
        ],
    }
    text = (
        "Zwei Fertigungsbereiche mit 38 Mitarbeitenden im Dreischichtbetrieb gefuehrt, "
        f"Audits nach {claim_form} begleitet."
    )
    assert _unmentioned_identifiers(text, build_vault_index(profile)) == []
    v = await verify_claim(text, profile)
    assert v.verdict == "grounded", v


async def test_a_denial_statement_in_the_vault_counts_as_a_mention():
    profile = {
        **PROFILE,
        "metadata": {
            "denied_concepts": [
                {
                    "concept": "ISO 13485",
                    "statement": "Mit ISO 13485 habe ich nie gearbeitet.",
                    "source": "interview",
                    "date": "2026-09-01T00:00:00+00:00",
                }
            ]
        },
    }
    assert _unmentioned_identifiers("Kein ISO-13485-Audit geleitet", build_vault_index(profile)) == []


async def test_an_identifier_named_in_the_claims_own_denial_segment_is_not_counted():
    """Refuter finding (2026-10-16): an honest gap disclosure followed by a true
    claim in one sentence must not be accused for the standard it disclaims."""
    text = (
        "Mit ISO 13485 habe ich keine Erfahrung, aber ich habe zwei "
        "Fertigungsbereiche mit 38 Mitarbeitenden gefuehrt."
    )
    assert _unmentioned_identifiers(text, build_vault_index(PROFILE)) == []
    v = await verify_claim(text, PROFILE)
    assert v.verdict != "unbacked", v


async def test_a_would_be_unverifiable_claim_is_never_upgraded_to_an_accusation():
    text = "Ich moechte mich in die Anforderungen der ISO 13485 einarbeiten."
    base = await _baseline(text)
    assert base.verdict != "grounded"
    v = await verify_claim(text, PROFILE)
    assert v.verdict == base.verdict, (base, v)


async def test_an_unmatched_figure_keeps_its_own_accusation():
    text = "Drei Fertigungsbereiche mit 120 Mitarbeitenden gefuehrt, ISO-45001-zertifiziert."
    v = await verify_claim(text, PROFILE)
    assert v.verdict == "unbacked"
    assert v.detail.startswith("No vault evidence for figure(s)"), v


async def test_a_skill_chip_naming_an_unmentioned_standard_is_never_grounded():
    """A chip is graded by the skill branch (whole-token presence); a mistyped
    standard is already ``unbacked`` there and stays so — the rule only ever
    replaces a ``grounded``."""
    profile = {**PROFILE, "skills": [{"name": "ISO 45001"}]}
    v = await verify_claim(Claim(text="ISO 4501", location="skills[0]", kind="skill"), profile)
    assert v.verdict == "unbacked"
    ok = await verify_claim(Claim(text="ISO 45001", location="skills[0]", kind="skill"), profile)
    assert ok.verdict == "grounded"


async def test_no_model_can_rescue_an_unmentioned_identifier():
    """ADR-068 cl. 1 amended: the identifier miss is a non-seam. A provider that
    would happily say "grounded" is never consulted."""

    class _Yes:
        calls = 0

        async def aparse_json(self, *a, **kw):
            _Yes.calls += 1
            return {"verdict": "grounded", "entailed": True, "results": []}

        async def acomplete(self, *a, **kw):
            _Yes.calls += 1
            return "grounded"

    v = await verify_claim(A2_LONG, PROFILE, provider=_Yes(), document_language="en")
    assert v.verdict == "unbacked"
    assert _Yes.calls == 0


# ── the three corpus true positives, as synthetic restatements ──────────────

_TP_VAULT = {
    **PROFILE,
    "work_experience": [
        {
            **PROFILE["work_experience"][0],
            "achievements": [
                "Die ISO-45001-Zertifizierung fuer die Produktion vorbereitet; die "
                "Unfallquote (LTIF) von 8,2 auf 3,1 gesenkt.",
                "Budgetverantwortung von ca. 6 Mio. € fuer Personal und Instandhaltung.",
            ],
        }
    ],
}


@pytest.mark.parametrize(
    "text,ident",
    [
        (
            "Budget von ca. 6 Mio. € verantwortet und die ISO-4501-Zertifizierung "
            "vorbereitet; LTIF von 8,2 auf 3,1 gesenkt.",
            "ISO-4501",
        ),
        (
            "Die LTIF sank von 8,2 auf 3,1, weil ich die ISO-450001-Zertifizierung "
            "vorbereitet habe.",
            "ISO-450001",
        ),
        (
            "Meine Massnahmen senkten die Unfallquote von 8,2 auf 3,1 und begleiteten "
            "die ISO-45000-Zertifizierung.",
            "ISO-45000",
        ),
    ],
    ids=["cv-4501", "letter-450001", "letter-45000"],
)
async def test_corpus_true_positive_mistyped_standard_number(text, ident):
    base = await _baseline(text, _TP_VAULT)
    assert base.verdict == "grounded", base
    v = await verify_claim(text, _TP_VAULT)
    assert v.verdict == "unbacked"
    assert ident in v.detail


# ── one seam test per caller ─────────────────────────────────────────────────


def _a2_cv():
    return {"summary": "", "work_history": [{"id": _W, "bullets": [A2_LONG + "."]}], "skills": []}


def _a2_letter():
    return {
        "body": {
            "paragraphs": [
                "Sehr geehrte Damen und Herren,",
                "bei der Brennwerk Kunststofftechnik GmbH habe ich " + A2_LONG + ".",
            ]
        }
    }


def _a2_results(report):
    claims = report["claims"] if isinstance(report, dict) else [
        r.model_dump(mode="json") for r in report.claims
    ]
    hits = [c for c in claims if "45001" in c["claim"]["text"]]
    assert hits, claims
    return hits


async def test_SEAM_self_audit_cv():
    report = await build_self_audit_report(PROFILE, tailored_data=_a2_cv())
    assert report is not None
    assert all(c["verdict"]["verdict"] == "unbacked" for c in _a2_results(report))


async def test_SEAM_self_audit_letter():
    report = await build_self_audit_report(PROFILE, letter_data=_a2_letter())
    assert report is not None
    assert all(c["verdict"]["verdict"] == "unbacked" for c in _a2_results(report))


async def test_SEAM_agent_door_external_text_with_entailment_on():
    """The agent door passes a real provider (entailment off since D2-4); the
    rule must hold there too, and the provider must never be asked."""

    class _Refuse:
        async def aparse_json(self, *a, **kw):
            raise AssertionError("provider consulted for an unmentioned identifier")

        async def acomplete(self, *a, **kw):
            raise AssertionError("provider consulted for an unmentioned identifier")

    report = await audit_document("external", PROFILE, text=A2_LONG + ".", provider=None)
    assert all(c["verdict"]["verdict"] == "unbacked" for c in _a2_results(report))
    v = await verify_claim(A2_LONG, PROFILE, provider=_Refuse())
    assert v.verdict == "unbacked"


async def test_SEAM_cv_assist_withholds_a_suggestion_naming_an_unmentioned_standard():
    from applire.services.cv_assist import _ground_suggestion

    class _Rec:
        profile_json = PROFILE

    class _Res:
        def scalar_one_or_none(self):
            return _Rec()

    class _DB:
        async def execute(self, _stmt):
            return _Res()

    kept, withheld = await _ground_suggestion(
        A2_LONG + ".", _DB(), session_evidence=[("session.answer", "Ich habe 38 Leute gefuehrt.")]
    )
    assert withheld == 1, kept
    # …and the candidate's own fresh answer naming the standard is evidence (W-2).
    kept, withheld = await _ground_suggestion(
        A2_LONG + ".",
        _DB(),
        session_evidence=[("session.answer", "Ich habe 2022 das ISO-45001-Lead-Auditor-Zertifikat erworben.")],
    )
    assert withheld == 0, kept


# ── adversarial pass on the integrated tree (2026-10-17) ─────────────────────
# (a) honest ENGLISH denials were accused; (b) a substring marker let a
# non-negation ("not only", "did not stop at") exempt a fabricated standard.

_TAIL_EN = "I led two production areas with 38 employees."


@pytest.mark.parametrize(
    "text",
    [
        f"I have no experience with ISO 13485, but {_TAIL_EN}",
        f"I am not familiar with ISO 13485, but {_TAIL_EN}",
        f"No hands-on exposure to ISO 13485, but {_TAIL_EN}",
        f"Without ISO 13485 experience, {_TAIL_EN}",
        "Mit ISO 13485 habe ich keine Erfahrung, aber ich habe zwei "
        "Fertigungsbereiche mit 38 Mitarbeitenden gefuehrt.",
        "Keine Erfahrung mit ISO 13485; zwei Fertigungsbereiche mit 38 Mitarbeitenden gefuehrt.",
    ],
    ids=["en-no-experience", "en-not-familiar", "en-no-hands-on", "en-without", "de-verb-final", "de-keine"],
)
async def test_an_honest_denial_of_the_standard_is_never_accused(text):
    assert _unmentioned_identifiers(text, build_vault_index(PROFILE)) == []
    v = await verify_claim(text, PROFILE)
    assert v.verdict != "unbacked" or "never mentions" not in (v.detail or ""), v


@pytest.mark.parametrize(
    "text",
    [
        "I have not only introduced ISO 13485 aligned access controls but also "
        "led two production areas with 38 employees.",
        "I did not stop at ISO 13485: I led two production areas with 38 employees.",
        "I didn't stop at ISO 13485 and led two production areas with 38 employees.",
        "Nicht nur ISO-13485-Audits begleitet, sondern zwei Fertigungsbereiche mit "
        "38 Mitarbeitenden gefuehrt.",
        "Introduced ISO 13485 controls with no downtime across two production areas "
        "with 38 employees.",
    ],
    ids=["not-only", "did-not-stop-at", "didnt-stop-at", "nicht-nur", "negation-after-the-id"],
)
async def test_a_non_negation_never_exempts_a_fabricated_standard(text):
    assert _unmentioned_identifiers(text, build_vault_index(PROFILE)), text


async def test_not_only_shape_is_unbacked_when_it_would_otherwise_be_grounded():
    text = "Not only introduced ISO 13485 controls: led two production areas with 38 employees."
    base = await _baseline(text)
    v = await verify_claim(text, PROFILE)
    if base.verdict == "grounded":
        assert v.verdict == "unbacked" and "ISO 13485" in v.detail
    else:
        assert v.verdict == base.verdict


# ── #790 lines L16 + L17 (2026-10-20) — notation variants and edition years ─
#
# The #374 pattern covered a subset of how standards are written; #701 checks
# exactly the spans it returns, so every variant it missed was neither a
# figure-exclusion nor an identifier check. Pure-matcher tests first, then the
# delivered verdict through ``verify_claim``, each asserting its baseline.

from applire.services.oracle.matchers.figures import (  # noqa: E402
    extract_figures,
    identifier_matches,
)


@pytest.mark.parametrize(
    "text,labels",
    [
        ("ISO 9001/45001", ["ISO 9001", "ISO 45001"]),
        ("ISO 9001, 14001 und 45001", ["ISO 9001", "ISO 14001", "ISO 45001"]),
        ("ISO 9001 and 14001", ["ISO 9001", "ISO 14001"]),
        ("iso 9001", ["iso 9001"]),
        ("Iso 45001", ["Iso 45001"]),
        ("ISO9001 eingeführt", ["ISO9001"]),
        ("IATF 16949", ["IATF 16949"]),
        ("IATF16949", ["IATF16949"]),
        ("VDA 6.3 Auditor", ["VDA 6.3"]),
        ("ISO/TS 16949", ["ISO/TS 16949"]),
        ("ISO-TS 16949", ["ISO-TS 16949"]),
        ("ISO 9001:2015 zertifiziert", ["ISO 9001"]),
        ("ISO 9001:2015/14001:2015", ["ISO 9001", "ISO 14001"]),
    ],
)
async def test_l16_notation_variants_are_identifiers_and_never_figures(text, labels):
    assert [label for _, label in identifier_matches(text)] == labels
    assert extract_figures(text) == []  # L17: the edition year is no year figure


@pytest.mark.parametrize(
    "text,kind,value",
    [
        # a 4-digit number after "und" is a headcount, not a continuation
        ("ISO 9001 und 1200 Mitarbeitende", "number", "1200"),
        # a year that is not attached by ":" stays a year
        ("ISO 9001 seit 2015", "year", "2015"),
        # "en" is German prose — EN stays uppercase-only
        ("kennen 9100 Teile", "number", "9100"),
        ("en 9100", "number", "9100"),
    ],
)
async def test_l16_the_widening_keeps_real_figures(text, kind, value):
    assert [(f.kind, f.value) for f in extract_figures(text)] == [(kind, value)]


async def test_l17_edition_year_does_not_unback_a_true_certification():
    text = "Zwei Fertigungsbereiche mit 38 Mitarbeitenden nach ISO 9001:2015 gefuehrt"
    v = await verify_claim(text, PROFILE)
    assert v.verdict == "grounded", v
    assert "2015" not in (v.detail or "")


@pytest.mark.parametrize(
    "text,named",
    [
        ("Zwei Fertigungsbereiche mit 38 Mitarbeitenden nach ISO 9001/45001 gefuehrt", "ISO 45001"),
        ("Zwei Fertigungsbereiche mit 38 Mitarbeitenden nach iso 45001 gefuehrt", "iso 45001"),
        ("Zwei Fertigungsbereiche mit 38 Mitarbeitenden nach IATF16949 gefuehrt", "IATF16949"),
    ],
)
async def test_l16_an_unmentioned_variant_is_unbacked(text, named):
    v = await verify_claim(text, PROFILE)
    assert v.verdict == "unbacked" == IDENTIFIER_ABSENT_VERDICT, v
    assert v.detail.startswith(f"Names {named} —"), v.detail


async def test_l16_a_continuation_the_vault_holds_is_grounded():
    profile = {**PROFILE, "certifications": [{"name": "ISO 45001", "issuing_organization": "TÜV"}]}
    text = "Zwei Fertigungsbereiche mit 38 Mitarbeitenden nach ISO 9001/45001 gefuehrt"
    v = await verify_claim(text, profile)
    assert v.verdict == "grounded", v
