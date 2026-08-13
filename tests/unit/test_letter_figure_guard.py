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

"""#254 — the deterministic letter-figure attribution guard.

Ground truth (2026-07-24, live LLM debug log): the writer draft never
contained "5+"; the ADR-021 CORRECTOR call — reacting to a reviewer demand to
cover the absent keyword "Team management" — minted "mentoring teams of 5+"
by borrowing the headcount from an unrelated vault fact ("Lead a team of
five tech leads and system owners", a different current role).
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.letter_figure_guard import (  # noqa: E402
    _extract_letter_figures,
    figure_ownership_facts,
    figure_ownership_reviewer_prompt_fn,
    guard_letter_figures,
    render_figure_ownership_block,
)

# ── the live #254 shape ──────────────────────────────────────────────────────
PROFILE = {
    "personal_info": {"name": "Anna Bauer"},
    "professional_summary": {
        "en": "Engineering leader shipping production systems across DACH."
    },
    "work_experience": [
        {
            "id": "w-datacore",
            "company": "DataCore Systems",
            "role": "Platform Engineering Lead",
            "achievements": [
                "Lead a team of five tech leads and system owners across the "
                "platform organisation.",
            ],
        },
        {
            "id": "w-vector",
            "company": "Vector Analytics",
            "role": "Senior Backend Engineer",
            "achievements": [
                "Delivered a 70% reduction in checkout latency through async "
                "pipeline redesign.",
            ],
        },
    ],
}


def _letter(paragraphs):
    return {
        "header": {},
        "recipient": {},
        "body": {"paragraphs": paragraphs},
        "signature": {},
    }


# ── pure figure extraction ───────────────────────────────────────────────────

def test_extracts_plus_form():
    figs = _extract_letter_figures("mentoring teams of 5+ engineers")
    assert [(f.kind, f.value, f.raw) for f in figs] == [("number", "5", "5+")]


def test_extracts_percent():
    figs = _extract_letter_figures("a 70% reduction in latency")
    assert [(f.kind, f.value) for f in figs] == [("percent", "70")]


def test_extracts_spelled_english():
    figs = _extract_letter_figures("I led a team of five engineers")
    assert ("number", "5") in [(f.kind, f.value) for f in figs]


def test_extracts_spelled_german():
    figs = _extract_letter_figures("Ich leitete ein Team von fünf Ingenieuren")
    assert ("number", "5") in [(f.kind, f.value) for f in figs]


def test_exempts_years():
    figs = _extract_letter_figures("Since 2019 I have driven platform strategy.")
    assert figs == []


def test_year_does_not_leak_as_plain_number():
    figs = _extract_letter_figures("In 2024, we shipped 3 major releases.")
    values = [(f.kind, f.value) for f in figs]
    assert ("number", "2024") not in values
    assert ("number", "3") in values


def test_unicode_apostrophe_does_not_break_matching():
    # U+2019 between words must not defeat spelled-number matching.
    figs = _extract_letter_figures("the team’s five engineers")
    assert ("number", "5") in [(f.kind, f.value) for f in figs]


# ── the live #254 vector: misattributed headcount ────────────────────────────

def test_borrowed_headcount_takes_its_whole_sentence_with_it():
    """The pinned bug: 'five' is a DataCore fact; a Vector Analytics clause
    must never keep a '5+' borrowed from it.

    #296 changed the REMOVAL UNIT from the figure's character span to the
    sentence. The old pin here asserted 'mentoring teams of' survives — i.e.
    it actively required the mutilated remainder that charter run #7 delivered
    to a hiring manager. A figure is a noun-phrase argument; its neighbours are
    grammatically load-bearing, so the sentence is the smallest unit that still
    reads once the figure has to go.
    """
    letter = _letter(
        [
            "Dear Hiring Team,",
            "At Vector Analytics, I have experience mentoring teams of 5+ "
            "engineers and driving delivery excellence.",
            "Sincerely,",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    # Exact-equality, not substring absence: the point of #296 is WHAT REMAINS,
    # and only a full comparison can catch a surviving fragment.
    assert result["body"]["paragraphs"] == ["Dear Hiring Team,", "Sincerely,"]


def test_headcount_survives_when_clause_actually_names_datacore():
    """The SAME figure, honestly attributed to the role it belongs to, must
    survive — the guard targets misattribution, not the figure itself."""
    letter = _letter(
        [
            "At DataCore Systems, I lead a team of five tech leads and system "
            "owners across the platform organisation.",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    assert result is letter or result["body"]["paragraphs"] == letter["body"]["paragraphs"]
    body_text = " ".join(result["body"]["paragraphs"])
    assert "five" in body_text


# ── required regression: legitimate figures must survive ────────────────────

def test_grounded_percent_survives_in_its_own_sentence():
    letter = _letter(
        [
            "At Vector Analytics, I delivered a 70% reduction in checkout "
            "latency through async pipeline redesign.",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "70%" in body_text


def test_profile_level_years_of_experience_survives():
    """'over 20 years of experience' is a profile-level claim never literally
    stored as the digit string '20' anywhere in the vault (it is derived from
    date spans) — the guard must not treat 'no literal match' as fabricated;
    that is the Oracle's unbacked-figure job, not this guard's."""
    letter = _letter(
        [
            "With over 20 years of experience shipping production systems, "
            "I am excited to apply.",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "20 years" in body_text


def test_role_agnostic_evidence_clears_any_context():
    """A figure backed (also) by a role-agnostic vault unit (e.g. the
    professional summary) is never foreign, anchored or not."""
    profile = {
        **PROFILE,
        "professional_summary": {"en": "Delivered 42 major platform migrations."},
    }
    letter = _letter(["I have personally delivered 42 major platform migrations."])
    result = guard_letter_figures(letter, profile)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "42" in body_text


def test_unmatched_figure_is_left_alone():
    """A figure with NO vault match anywhere is not this guard's concern
    (belt-and-suspenders: Oracle's unbacked-figure detection handles it)."""
    letter = _letter(["I shipped 17 major releases last year."])
    result = guard_letter_figures(letter, PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "17" in body_text


# ── unanchored-but-named-elsewhere escape (#248-style) ───────────────────────

def test_unanchored_clause_cleared_when_letter_names_the_true_owner_elsewhere():
    letter = _letter(
        [
            "At DataCore Systems, I led the platform organisation.",
            "I also grew and mentored a team of five across the group.",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "five" in body_text


def test_an_unanchored_sentence_is_the_reviewers_call_not_the_floors():
    """The #248 shape, re-pinned by #299: two employers named in the letter,
    the figure-bearing sentence naming neither.

    The floor used to strip it. Which employer an unanchored sentence is about
    is a judgement about prose (ADR-062 clause 1) — the same judgement that,
    computed deterministically, deleted six grounded sentences in run #8 and
    every Cargonaut figure in #296. The protection is not dropped, it MOVES:
    the reviewer is told the vault owner of the very same figure and can
    re-anchor or remove the claim, which deterministic code cannot do.
    """
    letter = _letter(
        [
            "At DataCore Systems, I led platform strategy.",
            "At Vector Analytics, I owned backend delivery.",
            "Separately, I have mentored teams of 5+ across my career.",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    assert result["body"]["paragraphs"] == letter["body"]["paragraphs"]
    # …and the fact the floor no longer acts on reaches the reviewer instead.
    block = render_figure_ownership_block(figure_ownership_facts(letter, PROFILE))
    assert "DataCore Systems" in block


# ── #283 shape: multi-employer letter, achievement paragraph unanchored ─────
# Invented fixture mirroring the run-6 ground truth (NordPharm "record-breaking
# QC LIMS implementation, 7 months, 3 sites" — belongs to an OLDER position at
# the SAME employer the letter names in a DIFFERENT paragraph; the letter
# separately names a second, unrelated employer too, so the "letter names
# exactly one employer" escape can never fire).
MULTI_ROLE_PROFILE = {
    "personal_info": {"name": "Jordan Lee"},
    "work_experience": [
        {
            "id": "w-northwind-current",
            "company": "Northwind Labs",
            "role": "Director of Platform Engineering",
            "is_current": True,
            "achievements": ["Set strategic direction for the platform org."],
        },
        {
            "id": "w-northwind-past",
            "company": "Northwind Labs",
            "role": "Systems Architect",
            "is_current": False,
            "achievements": [
                "Led a record-breaking LIMS rollout (7 months, 3 sites) as "
                "solution architect.",
            ],
        },
        {
            "id": "w-founder",
            "company": "Riverstone",
            "role": "Founder",
            "achievements": ["Built an open-source developer platform."],
        },
    ],
}


def test_the_283_achievement_paragraph_now_goes_to_the_reviewer_intact():
    """The #283 shape: paragraph 1 names Northwind Labs, paragraph 2 carries
    the LIMS achievement and names nobody, and the letter also names Riverstone.

    The old pin asserted the achievement sentence was deleted and called that
    "the guard working AS INTENDED — the fix belongs at the prompt level". #299
    is that prompt-level fix, so the pin turns over: the figures survive the
    floor and the reviewer gets the ownership fact. The two figures here are
    the candidate's OWN, honestly earned at the employer the paragraph above
    names — deleting them is the #296 damage, not a protection.
    """
    letter = _letter(
        [
            "At Northwind Labs, I currently serve as Director of Platform "
            "Engineering.",
            "My technical leadership spans strategic direction, and I have "
            "delivered record-breaking projects like the LIMS rollout in 7 "
            "months across 3 sites while working fully remote.",
            "As founder of Riverstone, I also built an open-source developer "
            "platform.",
        ]
    )
    result = guard_letter_figures(letter, MULTI_ROLE_PROFILE)
    assert result["body"]["paragraphs"] == letter["body"]["paragraphs"]
    block = render_figure_ownership_block(
        figure_ownership_facts(letter, MULTI_ROLE_PROFILE)
    )
    assert "Northwind Labs" in block


def test_same_sentence_anchor_lets_the_figures_survive():
    """The fix target: when the achievement-bearing SENTENCE ITSELF names the
    employer (not just an earlier paragraph), the figures survive — this is
    what the prompt-level anchoring requirement asks the writer/corrector to
    produce. Restoring '7 months across 3 sites' WITH the correct anchor,
    never a bare unattributed figure."""
    letter = _letter(
        [
            "At Northwind Labs, I currently serve as Director of Platform "
            "Engineering.",
            "At Northwind Labs, I also delivered record-breaking projects "
            "like the LIMS rollout in 7 months across 3 sites while working "
            "fully remote.",
            "As founder of Riverstone, I also built an open-source developer "
            "platform.",
        ]
    )
    result = guard_letter_figures(letter, MULTI_ROLE_PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "7 months" in body_text
    assert "3 sites" in body_text


def test_cross_role_borrowed_figure_still_dropped_even_when_anchored_elsewhere():
    """Guard against #254 regression: an anchor naming the WRONG employer must
    never launder a figure that genuinely belongs to a different one. The
    LIMS figures belong to Northwind Labs, not Riverstone — anchoring the
    sentence to Riverstone must still strip them."""
    letter = _letter(
        [
            "As founder of Riverstone, I delivered record-breaking projects "
            "like the LIMS rollout in 7 months across 3 sites while working "
            "fully remote.",
        ]
    )
    result = guard_letter_figures(letter, MULTI_ROLE_PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "7 months" not in body_text
    assert "3 sites" not in body_text


# ── #296: prose does not restate the employer in every sentence ────────────
# Charter run #7's letter named two employers, so the whole-letter
# single-employer escape could never fire, and every figure in every sentence
# that did not itself restate the employer was dropped: "At Acme I owned the
# platform. I cut deploy time from 45 to 8 minutes." #296 answered this with a
# paragraph-scoped running anchor; #299 replaced that heuristic with fail-open
# (the reviewer judges), which covers the same shape and the cross-paragraph
# one the carry-forward never reached.

def test_a_follow_on_sentence_keeps_a_legitimately_owned_figure():
    """A follow-on sentence in the same paragraph names no employer, and a
    legitimately-owned figure in it survives — without the writer having to
    restate the employer in every sentence."""
    letter = _letter(
        [
            "At Northwind Labs, I currently serve as Director of Platform "
            "Engineering. I delivered record-breaking projects like the LIMS "
            "rollout in 7 months across 3 sites.",
            "As founder of Riverstone, I also built an open-source developer "
            "platform.",
        ]
    )
    result = guard_letter_figures(letter, MULTI_ROLE_PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "7 months" in body_text
    assert "3 sites" in body_text


def test_a_sentence_that_names_its_own_employer_is_still_checked_against_it():
    """The floor's own shape: the second sentence names Riverstone in its own
    words, and the LIMS figures are Northwind's, so it still goes. This is the
    #254 catch, and it is what survives #299's demotion."""
    letter = _letter(
        [
            "At Northwind Labs, I currently serve as Director of Platform "
            "Engineering. As founder of Riverstone, I delivered the LIMS "
            "rollout in 7 months across 3 sites.",
        ]
    )
    result = guard_letter_figures(letter, MULTI_ROLE_PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "7 months" not in body_text
    assert "3 sites" not in body_text


# ── #296: what remains must be readable ─────────────────────────────────────

def test_no_run7_wreckage_shape_can_survive_a_drop():
    """The three shapes charter run #7 actually delivered to a hiring manager,
    each produced by blanking the figure's own span: "deploy time from 45 to 8
    minutes" -> "deploy time from to 8 minutes"; "EKS for 12 services" -> "EKS
    for services"; "a 99.9% availability target" -> "a availability target".

    Asserting the FIGURE is gone (which the old pins did) cannot catch any of
    these — the mutilated remainder passes that check perfectly. Only looking
    at what is left does.
    """
    profile = {
        "personal_info": {"name": "Jordan Lee"},
        "work_experience": [
            {
                "id": "w-northwind-current",
                "company": "Northwind Labs",
                "role": "Director of Platform Engineering",
                "achievements": ["Set strategic direction for the platform org."],
            },
            {
                "id": "w-founder",
                "company": "Riverstone",
                "role": "Founder",
                "achievements": [
                    "Cut deploy time from 45 to 8 minutes.",
                    "Ran EKS for 12 services.",
                    "Held a 99.9% availability target.",
                ],
            },
        ],
    }
    letter = _letter(
        [
            "At Northwind Labs, I currently serve as Director of Platform "
            "Engineering.",
            "I reduced deploy time from 45 to 8 minutes. I ran EKS for 12 "
            "services. I held a 99.9% availability target.",
        ]
    )
    result = guard_letter_figures(letter, profile)
    body_text = " ".join(result["body"]["paragraphs"])
    for wreckage in ("from to", "EKS for services", "a availability"):
        assert wreckage not in body_text, f"shipped run-#7 wreckage: {wreckage!r}"
    # No half-sentence left behind either — the whole unattributable paragraph
    # went, and the anchored one stands untouched.
    assert result["body"]["paragraphs"] == [
        "At Northwind Labs, I currently serve as Director of Platform "
        "Engineering.",
    ]


def test_a_truthful_figure_in_the_same_sentence_is_collateral():
    """The acknowledged cost of a sentence-sized removal unit, pinned so it
    stays visible rather than being discovered again in a run report.

    "At Vector Analytics, I mentored teams of 5+ engineers, having delivered a
    70% reduction in checkout latency" mixes a BORROWED DataCore headcount with
    a genuine Vector Analytics figure. Deterministic code cannot keep the
    second without rebuilding the sentence around it, so the floor takes both.

    #299 predicted this expectation would flip when the review loop got the
    ownership fact. It does NOT, and the reason is worth pinning: the sentence
    NAMES its employer, so this is the fact-grade case the floor still acts on
    — and the floor has to stay, because ``review_and_refine`` ships the last
    corrector draft unreviewed on exhaustion, cycle-stop and reviewer failure
    (the #254 vector). What #299 changed is upstream of here: the reviewer is
    told "5 is DataCore's" every round and can rewrite the sentence, so this
    state should reach the floor far less often. When it does reach it, the
    honest half is still collateral. Prompt effect, charter run, not CI.
    """
    letter = _letter(
        [
            "At Vector Analytics, I have experience mentoring teams of 5+ "
            "engineers, having delivered a 70% reduction in checkout latency.",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "5+" not in body_text
    assert "70%" not in body_text  # collateral — the honest half goes too


# ── no-op path ────────────────────────────────────────────────────────────

def test_letter_with_no_figures_is_returned_unchanged_object():
    letter = _letter(["Dear Hiring Team,", "I am excited to apply.", "Sincerely,"])
    result = guard_letter_figures(letter, PROFILE)
    assert result is letter


def test_empty_body_is_tolerated():
    assert guard_letter_figures({}, PROFILE) == {}
    assert guard_letter_figures({"body": {}}, PROFILE) == {"body": {}}


# ── charter run #8 (2026-07-28): two false-positive classes ──────────────────
# Six removals in one German letter, all six wrong. Both causes were FACTS the
# code computed incorrectly (ADR-062), not judgements it should have delegated:
# tenure was never actually exempt, and the #296 carry-forward was cleared by
# the sentence that established it.

RUN8_PROFILE = {
    "personal_info": {"name": "Stefan Brandt"},
    "work_experience": [
        {
            "id": "w-weberit",
            "company": "Weberit Kunststofftechnik GmbH",
            "role": "Produktionsleiter",
            "achievements": [
                "Verantwortung für zwei Fertigungsbereiche (Spritzguss, Montage) "
                "mit 38 Mitarbeitenden im Dreischichtbetrieb",
                "Senkung der Ausschussquote von 4,1 % auf 2,3 % durch "
                "Shopfloor-Management und KVP-Routinen",
                "Steigerung der Termintreue von 87 % auf 96 % durch "
                "SMED-Rüstworkshops und Feinplanung",
            ],
        },
        {
            "id": "w-rasselstein",
            "company": "Rasselstein Umformtechnik GmbH",
            "role": "Fertigungsmeister",
            "achievements": ["Führung einer Schicht mit 14 Mitarbeitenden"],
        },
    ],
    # The project that broke the carry-forward: nested under Weberit, so it
    # shares w-weberit's id, so w-weberit had TWO names in the candidate list.
    "projects": [
        {
            "id": "p-mes",
            "name": "Einführung eines MES-Systems",
            "associated_experience": "w-weberit",
            "description": "Rollout auf 14 Spritzgussmaschinen in beide "
            "Fertigungsbereiche",
        }
    ],
}


def test_a_tenure_figure_is_exempt_even_when_a_headcount_shares_its_digit():
    """The run-8 closing. "meine 14-jährige Expertise" is a duration derived
    from date spans; the 14s in the vault are a shift headcount and a machine
    count. Sharing a digit is a coincidence, not a misattribution — and the
    sentence it cost was the letter's closing, delivered as a bare
    "Mein Eintrittstermin kann flexibel vereinbart werden."
    """
    closing = (
        "Ich freue mich auf die Möglichkeit, meine 14-jährige Expertise in "
        "Lean-Methoden und meine 11-jährige Führungserfahrung in Ihr Team "
        "einzubringen, und stehe für ein persönliches Gespräch gerne zur "
        "Verfügung. Mein Eintrittstermin kann flexibel vereinbart werden."
    )
    result = guard_letter_figures(_letter([closing]), RUN8_PROFILE)
    assert result["body"]["paragraphs"] == [closing]


def test_tenure_exemption_covers_spelled_and_english_forms():
    """``value`` of the tenure number must not appear — other numbers in the
    same text are none of the exemption's business (ISO-9001's "9001" is a
    standard's identifier and is still extracted, as it was before)."""
    for text, tenure_value in (
        ("Als Produktionsleiter mit 14 Jahren Expertise in Lean-Management", "14"),
        ("zehn Jahre ISO-9001-Audit-Praxis", "10"),
        ("11 years of leadership experience", "11"),
        ("meine 14-jährige Expertise", "14"),
    ):
        values = [f.value for f in _extract_letter_figures(text)]
        assert tenure_value not in values, text


def test_the_plus_form_of_a_tenure_figure_is_exempt_too():
    """The one-character gap flagged during #214: "12+ years of experience" is
    the SAME duration as "12 years of experience", but ``_TENURE_RE`` required
    the unit to follow the digits directly and the "+" broke the adjacency —
    so the growth-quantifier form fell through to ``_PLUS_RE`` and was matched
    against every unrelated vault count containing 12.

    The Oracle's twin extractor (``oracle/matchers/figures._TENURE_RE``) closed
    this in #459; this is the same ``\\+?``, ported rather than re-derived.
    """
    for text in (
        "12+ years of Python experience",
        "12+ Jahre Erfahrung in der Fertigung",
        "meine 14+-jährige Expertise",
    ):
        assert [f.value for f in _extract_letter_figures(text)] == [], text


def test_a_headcount_is_still_not_a_tenure_figure():
    """The exemption keys on the UNIT, so it must not swallow a bare count in
    the same sentence — otherwise it would disarm the #254 catch."""
    figs = _extract_letter_figures(
        "In 14 Jahren führte ich eine Schicht mit 14 Mitarbeitenden."
    )
    assert [(f.kind, f.value) for f in figs] == [("number", "14")]


def test_the_four_run8_achievement_figures_survive():
    """The four grounded achievement figures run #8 deleted, kept as the
    regression they are.

    Run #8's cause was the carry-forward counting NAMES: w-weberit appeared in
    the candidate list twice — once as the company, once as its nested MES
    project — so one employer counted as two and the inheritance was cleared by
    the very sentence that established it. #299 deleted the carry-forward
    outright, so sentence 2 now survives because it names no employer at all
    and attribution there is not a fact. Same four figures, same letter, a
    mechanism with one fewer thing to get wrong.
    """
    para = (
        "Bei der Weberit Kunststofftechnik GmbH verantworte ich seit 2017 als "
        "Produktionsleiter zwei Fertigungsbereiche mit 38 Mitarbeitenden im "
        "Dreischichtbetrieb. Durch die Einführung von Shopfloor-Management und "
        "KVP-Routinen senkte ich die Ausschussquote von 4,1 % auf 2,3 %, "
        "während SMED-Rüstworkshops und Feinplanung die Termintreue von 87 % "
        "auf 96 % steigerten."
    )
    result = guard_letter_figures(_letter([para]), RUN8_PROFILE)
    body = " ".join(result["body"]["paragraphs"])
    for figure in ("4,1 %", "2,3 %", "87 %", "96 %"):
        assert figure in body, f"{figure} was removed"


def test_a_german_unanchored_sentence_after_a_two_employer_one_also_goes_to_the_reviewer():
    """The carry-forward's own boundary case, re-pinned by #299 (German, the
    run-8 profile): a sentence naming TWO employers resolves nothing, so the
    sentence after it is unanchored — and an unanchored sentence is no longer
    the floor's to cut. The reviewer is told that the 14 is Rasselstein's."""
    para = (
        "Bei Weberit Kunststofftechnik GmbH und Rasselstein Umformtechnik GmbH "
        "habe ich Fertigungsverantwortung getragen. Ich führte eine Schicht "
        "mit 14 Mitarbeitenden."
    )
    letter = _letter([para])
    result = guard_letter_figures(letter, RUN8_PROFILE)
    assert result["body"]["paragraphs"] == [para]
    block = render_figure_ownership_block(figure_ownership_facts(letter, RUN8_PROFILE))
    assert "Rasselstein Umformtechnik GmbH" in block


# ── #296 (charter run #7, `it_backend_daniel`, EN) ──────────────────────────
# The issue's own reproduction case, with the letter's delivered prose verbatim
# and a vault shaped like the case's CV plus the Kubernetes gap interview
# (dossier: "12 services over 9 months", "deploy time 45 -> 8 minutes",
# "99.9% availability target" — all Cargonaut facts).
DANIEL_PROFILE = {
    "personal_info": {"name": "Daniel Kovač"},
    "professional_summary": {
        "en": "Backend engineer designing and operating Python services."
    },
    "work_experience": [
        {
            "id": "w-cargonaut",
            "company": "Cargonaut Logistics GmbH",
            "role": "Senior Backend Engineer",
            "is_current": True,
            "achievements": [
                "Design and operate the shipment-tracking backend (Python, Django, "
                "PostgreSQL) serving ~40k daily active logistics customers.",
                "Own the AWS deployment (ECS, RDS, S3) and the GitLab CI/CD "
                "pipelines for four services.",
                "Led the migration from ECS to Kubernetes (EKS): 12 services over "
                "9 months, cutting deploy time from 45 to 8 minutes.",
                "Built Prometheus/Grafana observability with a 99.9% availability "
                "target for the tracking API.",
            ],
        },
        {
            "id": "w-finleap",
            "company": "Finleap Build GmbH",
            "role": "Backend Engineer",
            "achievements": [
                "Implemented the invoice-dunning workflow engine processing ~200k "
                "invoices/month.",
            ],
        },
    ],
}

DANIEL_DELIVERED_PARAGRAPH = (
    "At Cargonaut Logistics GmbH, I designed and operated a Python/Django backend "
    "serving ~40k daily active logistics customers, ensuring correctness-critical "
    "data handling with PostgreSQL. I led the migration from ECS to Kubernetes on "
    "EKS for 12 services, reducing deploy time from 45 to 8 minutes using Helm, "
    "ArgoCD, and GitOps. Additionally, I built a Prometheus and Grafana "
    "observability stack, established SLOs with error budgets for a 99.9% "
    "availability target, and implemented an Apache Kafka pipeline with idempotent "
    "consumers to prevent double-billing. My experience also includes AWS "
    "deployment (RDS, S3) and CI/CD ownership for four services."
)

DANIEL_FINLEAP_PARAGRAPH = (
    "At Finleap Build GmbH, I built REST APIs for a B2B invoicing product and "
    "implemented the dunning workflow engine processing ~200k invoices/month."
)


def test_the_delivered_run7_paragraph_keeps_every_grounded_figure():
    """#296 as it shipped: four grounded Cargonaut figures (12, 45, 8, 99.9%)
    in continuation sentences of the Cargonaut-anchored paragraph. Wave-8's
    ``_distinct_employers`` fix already covers this shape — pinned here on the
    issue's verbatim prose so it stays covered."""
    letter = _letter(
        [
            "Dear Hiring Team,",
            DANIEL_DELIVERED_PARAGRAPH,
            DANIEL_FINLEAP_PARAGRAPH,
            "Sincerely,",
        ]
    )
    result = guard_letter_figures(letter, DANIEL_PROFILE)
    assert result["body"]["paragraphs"] == letter["body"]["paragraphs"]


def test_the_same_sentences_survive_when_the_writer_splits_them_into_paragraphs():
    """#296's residual after wave 8: the SAME sentences, same vault, same
    employer — one per paragraph, which is ordinary letter shape.

    The running anchor is paragraph-scoped, so nothing crosses the break and
    every figure below was removed although the employer that owns them is
    named in the paragraph directly above. The employer a paragraph is about
    is a judgement about prose (ADR-062); the floor no longer makes it, and
    the fact goes to the reviewer instead.
    """
    letter = _letter(
        [
            "At Cargonaut Logistics GmbH, I designed and operated a Python/Django "
            "backend serving ~40k daily active logistics customers.",
            "I led the migration from ECS to Kubernetes on EKS for 12 services, "
            "reducing deploy time from 45 to 8 minutes using Helm, ArgoCD, and "
            "GitOps.",
            "Additionally, I established SLOs with error budgets for a 99.9% "
            "availability target.",
            DANIEL_FINLEAP_PARAGRAPH,
        ]
    )
    result = guard_letter_figures(letter, DANIEL_PROFILE)
    assert result["body"]["paragraphs"] == letter["body"]["paragraphs"]


# ── #299 / ADR-062 clause 2: the fact goes to the reviewer ──────────────────
# "Figure N appears in the vault only under owners X, Y, Z" is a data-structure
# lookup — a FACT. Which employer a sentence is ABOUT is a judgement, and it now
# belongs to the reviewer, which sees the prose, the vault owners and the reason.

def test_ownership_facts_name_the_owners_of_every_grounded_figure_in_the_draft():
    letter = _letter(
        [
            "At Vector Analytics, I have experience mentoring teams of 5+ "
            "engineers.",
            "I delivered a 70% reduction in checkout latency.",
        ]
    )
    facts = figure_ownership_facts(letter, PROFILE)
    assert {(f.kind, f.value, f.owners) for f in facts} == {
        ("number", "5", ("DataCore Systems",)),
        ("percent", "70", ("Vector Analytics",)),
    }


def test_a_figure_with_no_vault_backing_is_not_a_fact_this_module_owns():
    """Unbacked figures are the Oracle's verdict, not this module's — telling
    the reviewer "17 is owned by nobody" would invite it to strip a legitimate
    derived claim (the guard's own long-standing scope rule)."""
    facts = figure_ownership_facts(_letter(["I shipped 17 major releases."]), PROFILE)
    assert facts == []


def test_a_role_agnostic_figure_carries_no_ownership_constraint():
    """A figure also backed by role-agnostic evidence (e.g. the professional
    summary) belongs to no position in particular, so there is no attribution
    question to hand over."""
    profile = {
        **PROFILE,
        "professional_summary": {"en": "Delivered 42 major platform migrations."},
    }
    facts = figure_ownership_facts(_letter(["I delivered 42 migrations."]), profile)
    assert facts == []


def test_a_tenure_figure_is_never_handed_to_the_reviewer_as_owned():
    """The exemption is the same one the floor applies — otherwise the reviewer
    would be told "14 belongs to Rasselstein" about the candidate's own tenure
    (the run-8 closing-paragraph defect, moved into the prompt)."""
    letter = _letter(["Meine 14-jährige Expertise bringe ich gerne ein."])
    assert figure_ownership_facts(letter, RUN8_PROFILE) == []


def test_the_reviewer_block_states_the_fact_and_the_narrow_instruction():
    letter = _letter(
        [
            "At Vector Analytics, I have experience mentoring teams of 5+ "
            "engineers.",
        ]
    )
    block = render_figure_ownership_block(figure_ownership_facts(letter, PROFILE))
    # the fact: the figure, and the position the vault backs it under
    assert '"5+"' in block
    assert "DataCore Systems" in block
    # the judgement is explicitly the model's, and both remedies are offered —
    # #299's case (b) (the achievement is real here, only the number was
    # borrowed) must not be treated like case (a) (the claim is not this
    # employer's at all), because deleting the sentence is right for one and
    # wrong for the other.
    assert "re-anchor" in block.lower()
    assert "DROP the number" in block
    # the #299 laundering trap, stated as a requirement rather than implied: a
    # rewritten claim that survives WITHOUT naming its employer escapes this
    # guard (no figure left) and the Oracle's find_foreign_owner (fail open on
    # unanchored claims) alike.
    assert "MUST name" in block


def test_the_block_is_empty_when_there_is_no_ownership_fact_to_state():
    assert render_figure_ownership_block([]) == ""


def test_the_reviewer_wrapper_appends_the_block_to_the_base_prompt():
    """Composes exactly like the ledger/word-floor wrappers: same
    ``fn(source, draft)`` contract, recomputed per review iteration against the
    CURRENT draft, no new LLM call."""
    def base(source, draft):
        return "BASE PROMPT"

    fn = figure_ownership_reviewer_prompt_fn(base, PROFILE)

    # No owned figure in the draft -> nothing to state, prompt untouched.
    assert fn("src", _letter(["I am excited to apply."])) == "BASE PROMPT"

    # Every owned figure is stated, correctly attributed ones included: which
    # ones are misattributed is the reviewer's judgement, so the block may not
    # pre-select them (that selection IS the heuristic #299 retires).
    flagged = fn(
        "src",
        _letter(
            [
                "At Vector Analytics, I mentored teams of 5+ engineers and "
                "delivered a 70% reduction in checkout latency."
            ]
        ),
    )
    assert flagged.startswith("BASE PROMPT")
    assert "DataCore Systems" in flagged
    assert "Vector Analytics" in flagged


def test_a_project_owned_figure_names_its_employer_never_a_raw_uuid():
    """#526 side finding, pinned to gate charter run 1's own prompt.

    `_owner_labels`' docstring has always promised that ids are never shown to
    the model — "the reviewer reasons about the letter's prose, which names
    companies, not UUIDs". The code did not deliver it: labels came from
    `_employer_of_id` (work entries only) plus `oracle/extract.
    _employer_anchor_candidates`, and the latter deliberately re-targets a
    project to its PARENT work id — correct for anchoring a sentence, but it
    means the project's OWN id is never a label key. A figure whose vault
    evidence unit belongs to the project therefore rendered as

        "14" — backed only by evidence from: 85ff5f8a-ce5c-4290-…, Rasselstein …, Weberit …

    and the run-1 condense reviewer did exactly what the block forbids: it
    re-derived what the id was ("the vault records it under the project
    'Einführung eines MES-Systems'") and filed a blocking issue on it. An
    unreadable owner does not fail safe here — it manufactures work.
    """
    from applire.services.letter_figure_guard import figure_ownership_facts

    profile = {
        "work_experience": [
            {
                "id": "w-weberit",
                "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "achievements": ["Verantwortung für 38 Mitarbeitende"],
            }
        ],
        "projects": [
            {
                "id": "p-mes",
                "name": "Einführung eines MES-Systems",
                "associated_experience": "w-weberit",
                # `description`, not `achievements`: the run-1 vault carries the
                # figure here, and only this shape reproduces — a description
                # unit keeps the PROJECT's own id as its owner, while an
                # achievement unit is re-owned to the parent work entry. A
                # fixture using `achievements` passes without the fix and proves
                # nothing.
                "role": "Projektleiter",
                "start_date": "2023",
                "description": (
                    "Maschinendaten- und Betriebsdatenerfassung an 14 "
                    "Spritzgussmaschinen, von der Auswahl bis zum Rollout."
                ),
            }
        ],
    }
    letter = {
        "body": {
            "paragraphs": [
                "Bei Weberit Kunststofftechnik GmbH führte ich die Erfassung an "
                "14 Spritzgussmaschinen ein."
            ]
        }
    }

    facts = figure_ownership_facts(letter, profile)
    fourteen = [f for f in facts if f.value == "14"]
    assert fourteen, "fixture premise: the figure resolves to a vault owner at all"
    owners = set(fourteen[0].owners)
    assert owners, "a fact with no nameable owner tells the reviewer nothing"

    # Assert against the set of names the vault actually holds, NOT against a
    # "looks like a UUID" heuristic: the first version of this test used one and
    # passed vacuously, because the synthetic id "p-mes" is not UUID-shaped while
    # being just as unreadable to the reviewer as the real one.
    nameable = {"Weberit Kunststofftechnik GmbH", "Einführung eines MES-Systems"}
    assert owners <= nameable, f"unreadable owner(s) reached the reviewer: {owners - nameable}"
    assert "Weberit Kunststofftechnik GmbH" in owners
