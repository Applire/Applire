# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""WP-Q6 (b), Sorbet quality-1 wave 2a — the answer to the scope question is the
person's statement on THAT bar, stored structurally (ADR-075 amended 2026-11-15;
COORD MD-Q10 for the Oracle rail).

The miss (wave-1a delivery re-run, llmlog 2026-11-15 record 143): Stefan answered
the fixed scope question with "… Die dauerhafte Spanne von 120 wäre für mich der
nächste Schritt …" and the reconciler returned ``"denials": []`` — nothing bound,
and the letter named one gap fewer. The hit (delivery-1b run 3, record 151): the same
answer recorded as a denial.

Pinned here:
1. the scope turn keeps the answer on the scope cluster (only there), whatever the
   reconcile extracted; the next analysis reads it per bar;
2. the gap prompt shows it as SCOPE ANSWERS and asks for the shortfall SENTENCE;
   code binds only a verbatim sentence of THIS bar's answer;
3. writer block + Oracle together on the real sentence (lesson 2);
4. MD-Q10 condition 1: an answer stating no shortfall grounds no step sentence, and
   "verantwortete 120" needs the profile; condition 2: the answer grounds nothing
   beyond this bar and nothing on the CV path.

Synthetic persona data only (tests/files/panel_review_case/operations_marcus_de).
"""

import copy
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from test_q2_scope_question import (  # noqa: E402,F401
    _SCOPE,
    _W1B_SENTENCE,
    _llm_cluster,
    _scope_cluster,
    _scope_row,
    _seed,
    db,
)

#: The captured answer (identical in the miss and the hit run).
ANSWER = (
    "Direkt geführt habe ich 38 Mitarbeitende über drei Schichtleiter. In Urlaubs- und "
    "Krankheitsvertretung des Betriebsleiters habe ich mehrfach den Gesamtstandort mit rund "
    "90 Mitarbeitenden geführt, jeweils 2 bis 4 Wochen am Stück. Die dauerhafte Spanne von "
    "120 wäre für mich der nächste Schritt, genau deshalb reizt mich die Stelle."
)
#: What the Luna replay of the captured post-interview call quoted, 5/5.
QUOTE = "Die dauerhafte Spanne von 120 wäre für mich der nächste Schritt, genau deshalb reizt mich die Stelle."
#: The met-bar control answer, and the sentence Luna bound from it in 1 of 5 replays.
MET = ("Dauerhaft verantwortet habe ich am Standort Koblenz rund 120 Mitarbeitende über drei "
       "Bereichsleiter, seit 2021.")
ANSWER_ID = f"answer: {_SCOPE}"


def _answers(text=ANSWER):
    return {_SCOPE.casefold(): text}


# 1. the store -------------------------------------------------------------------

def test_only_a_scope_cluster_carries_its_answer_and_the_analysis_reads_it_per_bar():
    from applire.services.interview.scope_question import SCOPE_ANSWERS_KEY, turn_scope_answers

    scope = {**_scope_cluster(), SCOPE_ANSWERS_KEY: [ANSWER, "  Mehr  nicht. "]}
    other = {**_llm_cluster("cluster-sap", ["SAP"]), SCOPE_ANSWERS_KEY: ["Mit SAP nie."]}
    assert turn_scope_answers([scope, other]) == {_SCOPE.casefold(): ANSWER + " Mehr nicht."}
    assert turn_scope_answers([{**_scope_cluster()}]) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_kind", [True, False], ids=["scope-cluster", "llm-cluster"])
async def test_the_scope_turn_stores_the_answer_although_the_reconcile_recorded_no_denial(db, scope_kind):  # noqa: F811
    from applire.models.gap import GapAnalysis
    from applire.schemas.session import SessionCreateRequest
    from applire.services.interview.scope_question import SCOPE_ANSWERS_KEY
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult
    from applire.services.session import create_session, send_message
    from sqlalchemy import select

    cluster = _scope_cluster() if scope_kind else _llm_cluster("cluster-mes", ["MES"])
    job = await _seed(db, [cluster], [_scope_row()])
    provider = MagicMock()
    provider.aparse_json = AsyncMock(return_value={"question": "Wie war es mit MES?", "choices": []})
    provider.acomplete = AsyncMock(return_value="Wie war es mit MES?")
    created = await create_session(
        SessionCreateRequest(job_id=job.id, mode="targeted", target_gap=cluster["id"]), db, provider)

    async def _no_denial(db, *, profile_record, **_kw):  # the captured miss: "denials": []
        return InterviewTurnResult(profile_dict=copy.deepcopy(profile_record.profile_json),
                                   changes=[], addressed=True, denial_recorded=False,
                                   denied_concepts=[])

    with (
        patch("applire.services.session.reconcile_interview_turn", new=AsyncMock(side_effect=_no_denial)),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        await send_message(created.session_id, ANSWER, db, provider)
    row = (await db.execute(select(GapAnalysis).where(GapAnalysis.job_analysis_id == job.id))).scalar_one()
    kept = next(c for c in row.gap_clusters if c.get("id") == cluster["id"])
    if scope_kind:
        assert kept[SCOPE_ANSWERS_KEY] == [ANSWER]
    else:
        assert SCOPE_ANSWERS_KEY not in kept, "an LLM cluster's answer is no bar statement"


def test_the_carry_forward_keeps_the_stored_answer():
    from applire.services.interview.scope_question import SCOPE_ANSWERS_KEY, carve_scope_clusters

    scope = {**_scope_cluster(), SCOPE_ANSWERS_KEY: [ANSWER]}
    again = carve_scope_clusters([scope], [_scope_row()], [])
    assert next(c for c in again if c["id"] == scope["id"])[SCOPE_ANSWERS_KEY] == [ANSWER]


# 2. the prompt and the binding ----------------------------------------------------

def test_the_prompt_shows_the_answer_and_asks_for_the_shortfall_sentence():
    from applire.prompts.gap_analysis import SYSTEM_PROMPT, build_user_prompt, scope_answers_view
    from applire.services.gap_inference import PreClassification

    rules = " ".join(SYSTEM_PROMPT.split())
    assert "copy it VERBATIM into \"stated_limit_quote\"" in rules
    assert "Most answers have none" in rules and "never moves \"status\"" in rules
    assert '"stated_limit_quote":' in SYSTEM_PROMPT
    block = [{"concept": _SCOPE, "kind": "team_size"}]
    view = scope_answers_view(block, _answers())
    assert view == [{"id": ANSWER_ID, "concept": _SCOPE, "statement": ANSWER}]
    pre = PreClassification(matched=[], inferred_b=[], unresolved=[])
    prompt = build_user_prompt({}, {}, pre, block, scope_answers=view)
    assert "SCOPE ANSWERS (the candidate's own answer" in prompt and ANSWER in prompt
    assert prompt.index("SCOPE ANSWERS (") < prompt.rindex("REQUIREMENTS")
    assert "SCOPE ANSWERS (" not in build_user_prompt({}, {}, pre, [], scope_answers=view), (
        "no bar in the posting → no answer block")


def test_the_analysis_seam_passes_the_previous_rows_answers():
    import inspect

    from applire.services import gap

    src = inspect.getsource(gap)
    assert "scope_answers = turn_scope_answers(previous.gap_clusters" in src
    assert "scope_answers=scope_answers_view(scope_block, scope_answers)" in src
    assert "scope_answers=scope_answers,\n" in src


def test_a_quoted_sentence_of_this_bars_answer_binds_and_nothing_else_does():
    from applire.services.scope_requirements import scope_answer_limit

    bound = scope_answer_limit(ANSWER_ID, QUOTE, _SCOPE, _answers())
    assert bound == {"id": _SCOPE, "statement": QUOTE, "source": "scope_answer"}
    assert scope_answer_limit(ANSWER_ID, "  " + QUOTE.replace(" ", "  ") + " ", _SCOPE, _answers())
    for cited, quote, answers in (
        (ANSWER_ID, None, _answers()),                       # the silent arm: id, no quote
        (ANSWER_ID, "120 wären für mich der Schritt.", _answers()),  # not in the answer
        ("answer: Budget ~2 Mio. €", QUOTE, _answers()),    # another bar's answer id
        (ANSWER_ID, QUOTE, {}),                              # nothing stored
        ("dauerhafte Spanne von 120", QUOTE, _answers()),    # a denial id is the other path
    ):
        assert scope_answer_limit(cited, quote, _SCOPE, answers) is None, (cited, quote)


def test_the_ledger_row_carries_the_answer_bound_limit_without_any_recorded_denial():
    from applire.services.scope_requirements import build_scope_ledger_entries

    block = [{"concept": _SCOPE, "kind": "team_size", "jd_quote": "ca. 120 Mitarbeitende",
              "jd_value": 120.0, "jd_value_max": None, "comparator": "approx",
              "level": "required", "candidate_values": []}]
    cls = [{"concept": _SCOPE, "status": "partial", "reason": "38 < 120",
            "stated_limit": ANSWER_ID, "stated_limit_quote": QUOTE}]
    profile = {"metadata": {"denied_concepts": []}}
    (row,) = build_scope_ledger_entries(block, cls, profile, scope_answers=_answers())
    assert row["status"] in ("partial", "gap"), "the answer never moves the status"
    assert row["bar"]["recorded_limit"] == {"id": _SCOPE, "statement": QUOTE, "source": "scope_answer"}
    (unbound,) = build_scope_ledger_entries(block, cls, profile, scope_answers={})
    assert "recorded_limit" not in unbound["bar"]


# 3. writer block + Oracle together, on the real sentence (lesson 2) ---------------

_JD = "Ihr Profil: Gesamtverantwortung für Produktion (ca. 120 Mitarbeitende, Mehrschichtbetrieb)."


def _answer_ledger(statement=QUOTE):
    return [_scope_row(limit={"id": _SCOPE, "statement": statement, "source": "scope_answer"})]


def _miss_profile(team_size=38):
    """The miss: no denial recorded; the vault holds 38 / 14 / the 90 deputy cover."""
    return {
        "metadata": {"denied_concepts": []},
        "work_experience": [{
            "id": "w1", "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
            "start_date": "2017-04", "is_current": True, "team_size": team_size,
            "responsibilities": [
                f"Direkte Führung von {team_size} Mitarbeitenden über drei Schichtleiter.",
                "In Urlaubs- und Krankheitsvertretung des Betriebsleiters mehrfach den "
                "Gesamtstandort mit rund 90 Mitarbeitenden geführt, jeweils 2 bis 4 Wochen am Stück.",
            ],
        }],
    }


async def _verdicts(text, *, ledger=None, profile=None, kind="cover_letter"):
    from applire.services.oracle import audit_document

    report = await audit_document(
        kind, profile or _miss_profile(), text=text,
        keyword_ledger=_answer_ledger() if ledger is None else ledger, document_language="de",
    )
    return [c.verdict.verdict for c in report.claims]


_STEP = "Die dauerhafte Spanne von 120 wäre für mich der nächste Schritt."


@pytest.mark.asyncio
async def test_the_writer_names_the_step_and_the_oracle_grounds_the_persons_sentence():
    from applire.services.cross_document import letter_gap_ladder, render_gap_ladder_block

    gaps, named = letter_gap_ladder(_answer_ledger(), [], _JD)
    assert [(g.scope, g.statement) for g in named] == [(True, QUOTE)]
    block = render_gap_ladder_block(gaps, named, "de")
    assert f'the candidate\'s own words (recorded): "{QUOTE}"' in block
    # The sentence the delivery-1b letter carried, on the miss data (no denial):
    assert await _verdicts(_STEP) == ["grounded"]
    assert await _verdicts(_STEP[:-1] + ", genau deshalb reizt mich die Stelle.") == ["grounded"]
    # The whole captured sentence: the step is backed, the glued clause judged alone.
    assert await _verdicts(_W1B_SENTENCE) == ["unverifiable"]
    # Without the stored answer the same sentence is the wave-1b red.
    assert await _verdicts(_STEP, ledger=[_scope_row()]) == ["unbacked"]


# 4. MD-Q10 conditions --------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("statement,text", [
    # condition 1: an answer stating NO shortfall backs no step sentence
    ("Ich habe 120 Mitarbeitende geführt.",
     "Die Führung von 120 Mitarbeitenden wäre für mich der nächste Schritt."),
    ("Ich habe 120 Mitarbeitende geführt.", "120 Mitarbeitende habe ich noch nicht geführt."),
    (MET, "Dauerhaft 120 Mitarbeitende zu verantworten wäre für mich der nächste Schritt."),
    # restates the met-bar answer closely: only the "frame in the person's own
    # sentence" check stops it (mutation M1)
    (MET, "Dauerhaft am Standort Koblenz rund 120 Mitarbeitende über drei Bereichsleiter zu "
          "verantworten wäre für mich der nächste Schritt."),
    # restatement is measured against the person's sentence, never the bar
    # concept that serves as the id (mutation M3)
    ("Das wäre für mich der nächste Schritt.", "Die Führungsspanne von ~120 MA wäre der nächste Schritt."),
    (MET, "Dauerhaft verantwortet habe ich rund 120 Mitarbeitende noch nicht."),
    (QUOTE, "Die dauerhafte Spanne von 120 hatte ich noch nicht, sie wäre der nächste Schritt."),
    # never past or attained
    (QUOTE, "Ich verantwortete die dauerhafte Spanne von 120."),
    (QUOTE, "Die dauerhafte Spanne von 120 wäre für mich der nächste Schritt gewesen, ich habe sie erreicht."),
], ids=["led-120-step", "led-120-not-yet", "met-step", "met-restated-step", "concept-as-id",
        "met-not-yet", "back-pointing",
        "verantwortete", "attained"])
async def test_condition_1_the_answer_backs_only_its_own_step_frame(statement, text):
    assert await _verdicts(text, ledger=_answer_ledger(statement)) == ["unbacked"], text


@pytest.mark.asyncio
async def test_condition_1_verantwortete_120_passes_only_when_the_profile_backs_it():
    text = "Als Produktionsleiter verantwortete ich 120 Mitarbeitende."
    assert await _verdicts(text, ledger=_answer_ledger(MET)) == ["unbacked"]
    assert await _verdicts(text, ledger=_answer_ledger(MET), profile=_miss_profile(120)) == ["grounded"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Ein Investitionsbudget von 120 Mio. € wäre für mich der nächste Schritt.",
    "Die dauerhafte Spanne von 120 Kunden wäre für mich der nächste Schritt.",
    "Eine Werksleitung über 120 Maschinen wäre für mich der nächste Schritt.",
], ids=["other-requirement-budget", "wrong-unit", "other-object"])
async def test_condition_2_the_answer_grounds_nothing_beyond_its_bar(text):
    assert await _verdicts(text) == ["unbacked"], text


@pytest.mark.asyncio
async def test_condition_2_the_cv_path_never_reads_the_answer():
    assert await _verdicts(_STEP, kind="cv") != ["grounded"]
    from applire.services.oracle.matchers.vault import build_vault_index

    idx = build_vault_index(_miss_profile())
    assert not any("nächste Schritt" in u.text for u in [*idx.units, *idx.denial_units])


@pytest.mark.asyncio
async def test_the_outcome_writer_keeps_an_answer_only_on_a_scope_cluster(db):  # noqa: F811
    """Defence in depth under the session's own gate (mutation M7): the writer
    itself refuses an answer for a non-scope cluster."""
    from applire.models.gap import GapAnalysis
    from applire.services.gap_coverage import record_turn_outcome
    from sqlalchemy import select

    scope, llm = _scope_cluster(), _llm_cluster("cluster-mes", ["MES"])
    job = await _seed(db, [scope, llm], [_scope_row()])
    for cid in (scope["id"], llm["id"]):
        await record_turn_outcome(db, job_id=job.id, fallback_gap_analysis_id=None, cluster_id=cid,
                                  member_facts={}, session_id="s-1", charge=False,
                                  scope_answer=ANSWER)
    row = (await db.execute(select(GapAnalysis).where(GapAnalysis.job_analysis_id == job.id))).scalar_one()
    by_id = {c["id"]: c for c in row.gap_clusters}
    assert by_id[scope["id"]].get("scope_answers") == [ANSWER]
    assert "scope_answers" not in by_id[llm["id"]]


#: Luna E2E run 3 (2026-11-15, sorbet-q6): the shortfall was stated on another
#: turn (the driver's bank answered the span topic early), recorded as a denial,
#: and cited by the final gap call — but bound nothing.
_RUN3_DENIAL = {
    "concept": "dauerhafte Führung von 120 Mitarbeitenden",
    "statement": (
        "Nein, mit IFS oder BRC habe ich keine Erfahrung — das ist eine ehrliche Lücke. Ich habe "
        "auch nie direkt für Lebensmittelkunden produziert. Hygiene- und Dokumentationsdisziplin "
        "kenne ich aber aus der Fertigung für Kosmetik-Verpackungen im Sauberraumbereich bei "
        "Weberit seit 2021 und aus zehn Jahren ISO-9001-Audit-Praxis. Der Standard ist neu, die "
        "Audit- und Dokumentationsdisziplin nicht. " + ANSWER
    ),
    "status": "active", "source": "interview", "date": "2026-11-15",
}


def test_run3_a_limit_naming_the_figure_with_its_unit_binds_although_the_bare_figure_repeats():
    """The figure scan must see EVERY occurrence: the old single cursor skipped
    "120 Mitarbeitenden" in the id once the statement's bare "120" was found."""
    from applire.services.scope_requirements import names_bar_figure_with_unit, verified_stated_limit

    bar = {"kind": "team_size", "value": 120.0, "value_max": None, "concept": _SCOPE}
    text = f"{_RUN3_DENIAL['concept']} {_RUN3_DENIAL['statement']}"
    assert names_bar_figure_with_unit(text, bar)
    profile = {"metadata": {"denied_concepts": [_RUN3_DENIAL]}}
    bound = verified_stated_limit(_RUN3_DENIAL["concept"], profile, bar=bar, asked_for=())
    assert bound and bound["id"] == _RUN3_DENIAL["concept"]
    for other in ("2120 Mitarbeitende", "1200 Mitarbeitende", "120 Tickets, dann 120 wäre"):
        assert not names_bar_figure_with_unit(other, bar), other
