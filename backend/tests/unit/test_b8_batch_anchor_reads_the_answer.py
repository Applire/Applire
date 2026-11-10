# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Build 8 delivery run (2026-11-10) — the batch anchor is the ANSWER's employer.

The harm shape, case B question 4 on the built-in interview: Applire's question
named both employers ("At Cargonaut you operate … and at Finleap Build you were
on call …"), the person answered "… at Cargonaut I built the Prometheus and
Grafana setup myself", the reconciler put every bullet under Cargonaut — and
the attribution guard held all seven items behind a card reading "the answer
named Finleap Build GmbH for this part", with "Move to Finleap Build GmbH" as
the first option. Whoever takes it puts a false employer on the CV.

Producer: `attribution._batch_anchor` (channel 3, ADR-063 am. 2026-09-18 rule
b) read the whole turn — gap label, Applire's question and the answer. The
person's short form "Cargonaut" does not match the vault's "Cargonaut
Logistics", so the question's "Finleap Build" was the only employer the corpus
named, and the question's words became "the answer named". A question is
Applire's text (ADR-061 am. 2026-10-18): the batch anchor reads the answer.

**Every fixture here is SYNTHETIC** — the delivery run's invented persona.
"""
from __future__ import annotations

import pytest

from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.services.profile.reconcile.attribution import enforce_attribution
from applire.services.profile.reconcile.ops import AddBullets, RequestConfirmation, SetField

_CARGO, _FIN = "w-cargo", "w-fin"

_QUESTION_BOTH = (
    "The role asks for observability work with Prometheus, Grafana and SLOs. At Cargonaut "
    "you operate Kubernetes workloads day to day, and at Finleap Build you were on call for "
    "production services and wrote the incident runbook. What have you used to monitor those "
    "services and know when something was going wrong?"
)
_QUESTION_FINLEAP = (
    "At Finleap Build you were on call for production services. Did you also build the "
    "monitoring there?"
)
_ANSWER_CARGONAUT = (
    "For the settlement-adjacent parts of our stack at Cargonaut I built the Prometheus and "
    "Grafana setup myself: RED-metrics dashboards for all four services, SLOs with error "
    "budgets for the tracking API with a 99.9% availability target, and alert routing that "
    "cut pager noise roughly in half."
)
_ANSWER_NO_EMPLOYER = _ANSWER_CARGONAUT.replace(" at Cargonaut", "")
_ANSWER_YES_THERE = "Yes, there I built the Prometheus and Grafana setup myself."
_ANSWER_FINLEAP = "Yes, at Finleap Build I built the Prometheus and Grafana setup myself."


def _profile() -> MasterProfileData:
    return MasterProfileData(
        work_experience=[
            WorkEntry(
                id=_CARGO,
                company="Cargonaut Logistics GmbH",
                role="Senior Backend Engineer",
                start_date="2021-03",
                is_current=True,
            ),
            WorkEntry(
                id=_FIN,
                company="Finleap Build GmbH",
                role="Backend Engineer",
                start_date="2017-05",
                end_date="2021-02",
            ),
        ]
    )


def _bullets() -> list[AddBullets]:
    return [
        AddBullets(
            target=_CARGO,
            responsibilities=[
                "Built the Prometheus and Grafana monitoring setup for the settlement-adjacent "
                "parts of the stack, including RED-metrics dashboards for all four services",
                "Defined SLOs with error budgets for the tracking API with a 99.9% "
                "availability target",
            ],
            achievements=["Cut pager noise roughly in half through improved alert routing"],
            technologies=["Prometheus", "Grafana"],
        )
    ]


def _cards(question: str, answer: str, ops: list | None = None) -> list[RequestConfirmation]:
    out = enforce_attribution(
        ops if ops is not None else _bullets(),
        profile=_profile(),
        new_info={"gap": "Observability", "question": question, "answer": answer},
        source="interview",
    )
    return [op for op in out if isinstance(op, RequestConfirmation)]


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        pytest.param(_QUESTION_BOTH, _ANSWER_CARGONAUT, id="delivery-turn"),
        pytest.param(_QUESTION_BOTH, _ANSWER_NO_EMPLOYER, id="answer-names-no-employer"),
        pytest.param(_QUESTION_FINLEAP, _ANSWER_CARGONAUT, id="answer-names-the-target-short"),
        # Knowingly given up: "yes, there" under a question that names the other
        # employer. The answer alone names nobody, and a card that says "the
        # answer named" must not be raised on Applire's own sentence.
        pytest.param(_QUESTION_FINLEAP, _ANSWER_YES_THERE, id="answer-only-affirms"),
    ],
)
def test_an_employer_only_the_question_named_holds_no_bullet(question: str, answer: str):
    """MUTATION KILL: hand `_batch_anchor` the whole-turn corpus again and all
    four cases raise the "reads like it belongs to Finleap Build GmbH" card."""
    ops = _bullets()
    out = enforce_attribution(
        ops,
        profile=_profile(),
        new_info={"gap": "Observability", "question": question, "answer": answer},
        source="interview",
    )
    assert not [op for op in out if isinstance(op, RequestConfirmation)]
    assert out == ops, "the batch must reach the vault unchanged"


def test_an_employer_the_answer_itself_names_still_holds_the_batch():
    """The channel's own case survives: the ANSWER names the other employer."""
    cards = _cards(_QUESTION_FINLEAP, _ANSWER_FINLEAP)
    assert len(cards) == 1
    assert cards[0].context["anchor_employer"] == "Finleap Build GmbH"
    assert cards[0].context["target_employer"] == "Cargonaut Logistics GmbH"


def test_a_set_field_is_not_rerouted_on_the_questions_employer():
    """The batch anchor also feeds the `set_field` guard — same source rule."""
    ops = [
        SetField(
            target=_CARGO,
            field="industry_context",
            value="Built and ran the monitoring stack for four backend services.",
        )
    ]
    assert not _cards(_QUESTION_BOTH, _ANSWER_CARGONAUT, ops)
