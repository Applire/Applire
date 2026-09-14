# Copyright (C) 2026 Tobias Rosenbaum
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

"""M5.7.1 / ADR-040 amendment 2026-09-13 — the CV-assist suggestion is checked before it is shown.

`services/cv_assist.py` is the oldest LLM *writing* surface in the product and was outside
every truthfulness control: no ADR-021 reviewer, no grounding check, no ADR-040
transparency surface. What it produces is prose the candidate pastes into a CV section —
exactly the content ADR-040 clause 1 governs.

Ruling W-2 settled the one question the amendment turns on: **what the suggestion is
grounded AGAINST.** Not the vault alone — the vault PLUS the candidate's own fresh input
in that micro-session, because the answer they just typed is by construction not in the
vault yet, and a vault-only check would withhold almost everything the feature produces.
The tests below pin both halves: the ungrounded sentence is withheld and counted, and the
sentence grounded only in the candidate's own answer survives.
"""
from __future__ import annotations


import pytest

from applire.services.cv_assist import _WITHHOLD_VERDICTS, _ground_suggestion

# Most tests here drive the async helper; the three synchronous ones opt out per-test.
pytestmark = pytest.mark.asyncio

_PROFILE = {
    "personal_info": {"full_name": "Stefan Brandt"},
    "professional_summary": {
        "de": "Produktionsleiter mit 14 Jahren Erfahrung in der Kunststofffertigung."
    },
    "work_experience": [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "company": "Weberit Kunststofftechnik GmbH",
            "role": "Produktionsleiter",
            "start_date": "2017-04",
            "responsibilities": [
                "Führung von zwei Fertigungsbereichen mit 38 Mitarbeitenden im Dreischichtbetrieb.",
            ],
            "achievements": [
                "Ausschussquote von 4,1 % auf 2,3 % gesenkt.",
            ],
        }
    ],
    "skills": [{"name": "SMED"}, {"name": "KVP"}],
}


class _Record:
    """The one field `_ground_suggestion` reads off a `MasterProfile` row."""

    profile_json = _PROFILE


class _FakeResult:
    def __init__(self, record):
        self._record = record

    def scalar_one_or_none(self):
        return self._record


class _FakeDB:
    """A stand-in for the `AsyncSession`: the helper issues exactly one SELECT."""

    def __init__(self, record=_Record()):
        self._record = record
        self.executed = 0

    async def execute(self, _stmt):
        self.executed += 1
        return _FakeResult(self._record)


async def test_a_sentence_grounded_only_in_the_candidates_fresh_answer_survives():
    """The ruling's whole point (SF-ASSIST.2). The candidate just told us this; it is not
    in the vault and never will be until a reconcile writes it. Withholding it would mean
    telling a user that a true fact they typed ten seconds ago is unsupported — and the
    feature exists precisely to elicit facts the vault does not hold."""
    answer = (
        "Ich habe 2023 bei Weberit ein MES an 14 Spritzgussmaschinen eingeführt "
        "und die OEE von 61 auf 73 Prozent gesteigert."
    )
    suggestion = (
        "2023 die Einführung eines MES an 14 Spritzgussmaschinen verantwortet und "
        "die OEE von 61 auf 73 Prozent gesteigert."
    )
    kept, withheld = await _ground_suggestion(
        suggestion, _FakeDB(), session_evidence=[("session.answer", answer)]
    )
    assert withheld == 0, kept
    assert "MES" in kept


async def test_a_figure_neither_the_vault_nor_the_answer_carries_is_withheld_and_counted():
    """The control firing. "220 Mitarbeitende" appears in no vault entry and in nothing
    the candidate said; it is the shape ADR-040 clause 1 exists for."""
    answer = "Ich habe zwei Fertigungsbereiche geleitet."
    suggestion = "Verantwortung für ein Werk mit 220 Mitarbeitenden in drei Schichten."
    kept, withheld = await _ground_suggestion(
        suggestion, _FakeDB(), session_evidence=[("session.answer", answer)]
    )
    assert withheld == 1, (kept, withheld)
    assert "220" not in kept


async def test_the_withheld_text_is_never_returned_to_the_caller():
    """Ruling W-2 chose withhold-and-count over show-flagged. A flagged-but-visible
    suggestion is one paste away from the document, and ADR-040 clause 4's own residual
    ("a user who skims and dismisses still ships unverified content") is the reason this
    surface does not get a second dismissible warning."""
    suggestion = "Verantwortung für ein Werk mit 220 Mitarbeitenden in drei Schichten."
    kept, withheld = await _ground_suggestion(
        suggestion, _FakeDB(), session_evidence=[("session.answer", "Nichts dazu.")]
    )
    assert withheld == 1
    assert "220 Mitarbeitenden" not in kept


async def test_the_vault_alone_still_grounds_a_suggestion_that_restates_it():
    """The other half of the evidence set. A suggestion that re-words what the vault
    already holds needs no session testimony at all."""
    suggestion = "Zwei Fertigungsbereiche mit 38 Mitarbeitenden im Dreischichtbetrieb geführt."
    kept, withheld = await _ground_suggestion(
        suggestion, _FakeDB(), session_evidence=[("session.answer", "")]
    )
    assert withheld == 0, kept
    assert kept


async def test_a_rewrite_that_preserves_an_existing_section_sentence_is_not_a_new_claim():
    """`prior_text`: the directed rewrite's job is to re-word what is already in the
    document. A sentence the section already carried is not a claim this call introduced,
    and treating it as one would make the rewrite feature unusable on any CV whose
    content the vault paraphrases rather than repeats."""
    existing = "Sondermaschinen für die Verpackungsindustrie in Betrieb genommen."
    kept, withheld = await _ground_suggestion(
        existing,
        _FakeDB(),
        session_evidence=[("session.directions", "Kürzer fassen.")],
        prior_text=existing,
    )
    assert withheld == 0, kept


async def test_no_profile_yet_means_nothing_is_withheld():
    """A brand-new user has no vault. Withholding here would block the very first thing
    they do, and there is nothing to check against."""
    kept, withheld = await _ground_suggestion(
        "Irgendein Satz über 500 Mitarbeitende.",
        _FakeDB(record=None),
        session_evidence=[("session.answer", "")],
    )
    assert withheld == 0
    assert "500" in kept


async def test_the_check_fails_open_and_the_feature_survives_a_broken_audit():
    """Prevention tier, never a gate (ADR-040 clause 4). A failure here must not take the
    feature down: the delivery-time truthfulness audit over the persisted `tailored_data`
    is what still looks at what actually ships."""

    class _Exploding:
        async def execute(self, _stmt):
            raise RuntimeError("database is on fire")

    suggestion = "Verantwortung für ein Werk mit 220 Mitarbeitenden."
    kept, withheld = await _ground_suggestion(
        suggestion, _Exploding(), session_evidence=[("session.answer", "")]
    )
    assert kept == suggestion
    assert withheld == 0


async def test_an_empty_suggestion_is_a_no_op_and_reads_no_profile():
    db = _FakeDB()
    assert await _ground_suggestion("   ", db, session_evidence=[]) == ("", 0)
    assert db.executed == 0


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_the_withhold_polarity_is_the_three_adverse_verdicts_only():
    """`unverifiable` means the Oracle could not decide, and ADR-068's fail-safe polarity
    is permissive; `not_applicable` is an explicit exemption. Withholding on either would
    make this over-withhold, which is the direction that costs the candidate a TRUE
    sentence — and the one the delivery-time audit cannot repair."""
    assert _WITHHOLD_VERDICTS == {"inflated", "misattributed", "unbacked"}
    assert "unverifiable" not in _WITHHOLD_VERDICTS
    assert "grounded" not in _WITHHOLD_VERDICTS
    assert "not_applicable" not in _WITHHOLD_VERDICTS


async def test_the_question_call_site_is_not_triaged():
    """ADR-040 amendment clause 3, as a seam assertion: `start_assist_session` produces a
    QUESTION for the candidate, not CV content, and nothing in it can leave the system as
    a truth claim. If a future edit routes it through the grounding helper, the candidate
    starts being told that the app's own question is unsupported."""
    import inspect

    from applire.services import cv_assist

    src = inspect.getsource(cv_assist.start_assist_session)
    assert "_ground_suggestion" not in src
    for fn in (cv_assist.submit_assist_answer, cv_assist.rewrite_section):
        assert "_ground_suggestion" in inspect.getsource(fn), fn.__name__


# ── Ruling D-1 (2026-09-14, ADR-059 precedent) ────────────────────────────────
#
# Delivery run 2026-09-13-operations-marcus-de, outcome 12: a fresh PATCH answer
# to the targeted gap "Investitionsverantwortung" produced a suggestion claiming
# "Eigenverantwortliche Entscheidung ... einer Investition von 2 Mio. €" —
# directly reversing the on-file `denied_concepts` entry "Investitionsentscheidungen
# selbst treffen" (denial_level "direct"), recorded 20 minutes earlier in the SAME
# interview — and shipped with `withheld_count: 0`. W-2's "fresh input is evidence"
# stays; ADR-059's containment floor binds every writer surface, and assist is one.
#
# Measured (this file's own reproduction, before deciding the fix shape): the
# gap's own concept "Investitionsverantwortung" shares no bounded literal token
# with the denial's concept label "Investitionsentscheidungen selbst treffen" —
# German compound-noun morphology ("-verantwortung" vs "-entscheidungen") defeats
# `is_denied_concept`'s bounded-substring matching in EITHER direction, and so
# does every one of the ledger entry's own `surface_forms`
# ("Investitionsplanung", "Investitionsvorbereitung"). Using the denial's raw
# free-text STATEMENT instead of its `concept` label DOES catch this exact pair —
# but was measured and REJECTED: the same statement's OTHER sentence explicitly
# AFFIRMS "Budgetverantwortung" ("das operative Budget habe ich verantwortet"),
# and `is_denied_concept("Budgetverantwortung", [statement])` also returns True
# with `corpus=None` (fail-closed, no release), which would wrongly withhold a
# fact the candidate explicitly holds. So this fix reuses `is_denied_concept` at
# its EXISTING, safe calling convention — `dc["concept"]` only, exactly how
# `keyword_ledger.py`'s ledger floor already calls it — which closes the DIRECT/
# literal-overlap case without opening the affirmed-fact false positive. It does
# NOT close the exact 2026-09-13 case, because that contradiction is semantic
# (a paraphrase), not lexical, and no bounded-token matcher — reused or new —
# can prove a paraphrase without a judgement call ADR-062 reserves for a model.
# Recorded as a `decide:` collector line for a judgement-seam or clause-scoped
# corpus follow-up, not silently claimed as closed here.

_DENIAL_INVESTMENT = {
    "concept": "Investitionsentscheidungen selbst treffen",
    "statement": (
        "Bei der Investitionsverantwortung muss ich differenzieren: das operative "
        "Budget habe ich verantwortet, aber Investitionsentscheidungen selbst lagen "
        "bei der Geschäftsführung, ich habe sie vorbereitet, nicht getroffen."
    ),
    "source": "interview",
    "date": "2026-09-13",
    "denial_level": "direct",
    "probe_asked": False,
}


def _profile_with_denial(**denial_over) -> dict:
    denial = {**_DENIAL_INVESTMENT, **denial_over}
    profile = dict(_PROFILE)
    profile["metadata"] = {"denied_concepts": [denial]}
    return profile


class _DenialRecord:
    def __init__(self, profile_json):
        self.profile_json = profile_json


async def test_a_suggestion_whose_TARGETED_GAP_CONCEPT_repeats_the_denial_is_withheld():
    """The mechanism fires when the assist session's own gap concept is a bounded
    literal match for (or of) a recorded denial's concept label — the SAFE,
    existing calling convention, proven to work on the shape it CAN see.

    The suggestion text here is deliberately something the ORDINARY per-claim
    grounding would pass cleanly (it restates the vault's own attested fact,
    word for word) — isolating that the withhold comes from the NEW denial
    floor, not from the pre-existing numbers/coverage check. Mutation-killed:
    hardcoding ``denied_hit = None`` left this suggestion grounded and kept,
    which is exactly the false-negative the mutation must produce for this
    test to be worth anything.
    """
    db = _FakeDB(record=_DenialRecord(_profile_with_denial(
        concept="Investitionsentscheidungen"
    )))
    kept, withheld = await _ground_suggestion(
        "Zwei Fertigungsbereiche mit 38 Mitarbeitenden im Dreischichtbetrieb geführt.",
        db,
        session_evidence=[("session.answer", "Ich habe eigenständig entschieden.")],
        gap_ids=["Investitionsentscheidungen"],
    )
    assert kept == ""
    assert withheld == 1


async def test_the_denial_floor_names_no_gap_and_is_a_no_op_when_nothing_denied():
    """No `denied_concepts` at all, or no `gap_ids` passed: the new check must be
    inert, never a false trigger on an ordinary vault."""
    kept, withheld = await _ground_suggestion(
        "Zwei Fertigungsbereiche mit 38 Mitarbeitenden geführt.",
        _FakeDB(),
        session_evidence=[("session.answer", "")],
        gap_ids=["Führung"],
    )
    assert withheld == 0
    assert kept


async def test_the_exact_delivery_run_pair_is_the_measured_honest_limit():
    """Pins the REFUTATION, so nobody re-claims this closed on a re-read of the
    docstring alone: the real gap concept from the 2026-09-13 run
    ("Investitionsverantwortung") does NOT match the real denial concept label
    ("Investitionsentscheidungen selbst treffen") under `is_denied_concept`, in
    either direction, nor via any of the ledger entry's own captured
    `surface_forms`. If this ever goes green, `is_denied_concept` gained cross-
    compound matching and the collector line recommending a judgement seam
    should be re-examined, not silently dropped."""
    from applire.services.profile.reconcile.stance import is_denied_concept

    denial_label = _DENIAL_INVESTMENT["concept"]
    for candidate in (
        "Investitionsverantwortung",
        "Investitionsplanung",
        "Investitionsvorbereitung",
    ):
        assert not is_denied_concept(candidate, [denial_label]), candidate


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_the_evidence_set_extension_is_pure_and_role_agnostic():
    """`extend_vault_index` must not let session testimony masquerade as an experience
    entry: an added unit carries no `owner_ids`, so the attribution matcher can never
    conclude a claim belongs to a position on the strength of something typed into a
    text box. And it must not mutate the vault index it was given."""
    from applire.services.oracle.matchers import build_vault_index, extend_vault_index

    base = build_vault_index(_PROFILE)
    extended = extend_vault_index(base, [("session.answer", "Ich habe 14 Maschinen betreut.")])

    assert len(base.units) + 1 == len(extended.units)
    assert base.units is not extended.units
    added = extended.units[-1]
    assert added.path == "session.answer"
    assert added.owner_ids == frozenset()
    # The denial rail may never be fed from here — a denial statement that became
    # grounding evidence is the control-defeated-by-its-own-receipt trap.
    assert extended.denial_units == base.denial_units
    # ADR-068 clause 2a: one short answer must not flip the corpus language.
    assert extended.dominant_language == base.dominant_language
