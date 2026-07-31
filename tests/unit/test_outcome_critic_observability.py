# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Tobias Rosenbaum
"""ADR-060 Pass B — System-FMEA row ``SF-CRITIC.1``: the pass's three states must
be **distinguishable** in the log.

The row's requirement is that "did not run (reason)", "ran, found nothing" and
"ran but the judgement failed" can never be read as the same thing — a disabled
pass and a working one must not look alike to whoever is diagnosing a run.

**Levels are deliberately asserted at INFO, not WARNING.** An earlier pass
promoted these six state lines to WARNING on the belief that they were not being
emitted at all; that belief was a measurement error (the observed run was driven
over the MCP stdio door, a separate process whose stdout never reaches
``docker compose logs backend``, so the records went to a stream nobody was
reading). The promotion was reverted for an independent reason as well: a
*routine success path* logging at WARNING means a healthy letter generation
emits a warning on every single run, which is exactly how operators are trained
to ignore warnings. The two genuine failure paths stay at WARNING, which is
correct.

So what this module pins is **distinguishability at the default level**, not
loudness. `config.py` defaults `log_level` to INFO and `main.py` attaches a
StreamHandler to the ``applire`` logger, so these records are visible as
shipped. An operator who sets ``LOG_LEVEL=WARNING`` loses most observability by
definition — that is their trade, not a defect in this component.
"""

import logging

import pytest

from applire.services.outcome_critic import run_pass_b

pytestmark = pytest.mark.asyncio

_LETTER = {"body": {"paragraphs": ["Ich bringe ISO-9001-Erfahrung mit."]}}
_CV = {"work_history": [{"company": "X", "bullets": ["ISO 9001 Audits begleitet"]}]}
_LEDGER = [
    {
        "concept": "ISO 9001",
        "surface_forms": ["ISO 9001"],
        "status": "direct",
        "claimable": True,
        "fit_weight": 1.0,
        "sources": ["required"],
        "evidence": "Bereichsverantwortlicher für Qualitätsmanagement",
    }
]


def _records(caplog):
    """The critic state records, at whatever level they were emitted."""
    return [r for r in caplog.records if "outcome critic (" in r.getMessage()]


class _EmptyFindingsProvider:
    """Minimal judgement stub — full-kwargs signature per the provider-ABC
    stub rule in CLAUDE.md."""

    async def aparse_json(self, prompt, **kwargs):
        return {"findings": []}


async def _run(**overrides):
    kwargs = dict(
        cv_tailored=_CV,
        letter_data=_LETTER,
        keyword_ledger=_LEDGER,
        job_role_title="Leiter Operations",
        jd_excerpt="ISO 9001 erforderlich.",
        provider=_EmptyFindingsProvider(),
        enabled=True,
    )
    kwargs.update(overrides)
    return await run_pass_b(**kwargs)


@pytest.mark.parametrize(
    "overrides, expected_reason, must_contain",
    [
        ({"enabled": False}, "disabled", "CRITIC_ENABLED=false"),
        ({"letter_data": None}, "missing_letter", "no settled letter draft"),
        ({"cv_tailored": None}, "missing_cv", "no assembled CV"),
    ],
)
async def test_each_short_circuit_state_logs_its_own_reason(
    caplog, overrides, expected_reason, must_contain
):
    """Every "did not run" state names WHY in the log, not just in the report.

    SF-CRITIC.8's requirement that missing inputs short-circuit *loudly* rather
    than degrade into a judgement on partial data. (The pre-2026-07-31
    ``missing_ledger`` short-circuit is retired — see the dedicated test
    below: the ledger only feeds the anchor list now, the judgement's real
    input is the documents.)
    """
    with caplog.at_level(logging.INFO, logger="applire"):
        report = await _run(**overrides)

    assert report.ran is False
    assert report.reason == expected_reason

    msgs = [r.getMessage() for r in _records(caplog)]
    assert len(msgs) == 1, f"expected exactly one state record, got {msgs}"
    assert "DID NOT RUN" in msgs[0]
    assert must_contain in msgs[0], (
        f"the log line does not say why it did not run: {msgs[0]!r}"
    )


async def test_a_missing_ledger_no_longer_blocks_the_pass(caplog):
    """ADR-060 third amendment: the ledger feeds ANCHORS, not the judgement's
    input boundary — a legacy/pre-E037 analysis without one must still get a
    coherence read (SF-CRITIC.9: the enumeration is no longer the boundary).
    The absence is still a logged fact, never silent."""
    with caplog.at_level(logging.INFO, logger="applire"):
        report = await _run(keyword_ledger=None)

    assert report.ran is True
    assert report.reason is None
    msgs = [r.getMessage() for r in _records(caplog)]
    assert any("no Keyword Ledger" in m for m in msgs), (
        f"the ledger's absence is not logged: {msgs}"
    )
    assert any("RAN" in m for m in msgs)


async def test_ran_with_nothing_to_judge_is_not_confusable_with_did_not_run(caplog):
    """The distinction SF-CRITIC.1 exists for, and the one a collapsed log
    destroys: a clean pass that found nothing must NOT read like a pass that
    never executed. (Since the third amendment there is no 0-candidate
    short-circuit — the model reads the documents and may find nothing; the
    RAN record carries the advisory count.)
    """
    with caplog.at_level(logging.INFO, logger="applire"):
        report = await _run()

    assert report.ran is True
    assert report.reason is None
    assert report.advisories == []

    msgs = [m for m in (r.getMessage() for r in _records(caplog)) if "RAN" in m]
    assert msgs, "no RAN state record emitted"
    assert all("DID NOT RUN" not in m for m in msgs), (
        "a pass that RAN is being logged with the same phrase as one that did not — "
        "this is precisely the SF-CRITIC.1 collapse"
    )


async def test_the_three_state_families_produce_mutually_distinct_messages(caplog):
    """Belt and braces over the individual assertions above: collect one message
    per state family and prove no two are equal. A future edit that unifies the
    wording ("critic finished") would satisfy every test above and still destroy
    the property the row asks for.
    """
    seen: list[str] = []
    for overrides in ({"enabled": False}, {"letter_data": None}, {}):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="applire"):
            await _run(**overrides)
        recs = _records(caplog)
        assert recs, f"no state record emitted for {overrides!r}"
        seen.append(recs[-1].getMessage())

    assert len(set(seen)) == len(seen), (
        f"two critic states log an identical message: {seen}"
    )


async def test_state_records_come_from_the_applire_logger_hierarchy(caplog):
    """``main.py`` attaches the StreamHandler to the ``applire`` logger, so a
    record logged on a name outside that hierarchy would be silently dropped in
    production while still being visible to ``caplog`` in a test.
    """
    with caplog.at_level(logging.INFO, logger="applire"):
        await _run(enabled=False)

    for r in _records(caplog):
        assert r.name == "applire" or r.name.startswith("applire."), (
            f"state record logged on {r.name!r}, outside the applire logger "
            "hierarchy that main.py attaches a handler to — it would not appear "
            "in production output"
        )
