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

"""ADR-061 clause 2's stance-adjudication call must be recognised by
MockLLMProvider (2026-07-27 review finding, mirrors
test_mock_reviewer_chain_recognition.py's regression class).

An unrecognised prompt falls through to the generic ``{"mock": ...}``
fallback. ``enforce_stance``'s ``_adjudicate_testimony`` correctly treats
that as malformed and resolves to ``unconfirmed`` — SAFE, but WRONG as
mock-stack behaviour: every uncertain-band token would silently land
unconfirmed on every mock IQ/OQ/PQ run, so those suites would test a
different product than production (the confirmed-via-adjudication path,
the whole point of ADR-061 clause 2, would have zero coverage outside this
file's and test_reconcile_stance.py's hand-scripted stubs). ADR-047's
precedent is explicit: the mock mirrors the response shape of the real
provider.
"""
from __future__ import annotations

import pytest

from applire.prompts.stance_adjudication import (
    STANCE_ADJUDICATION_SYSTEM_PROMPT,
    build_stance_adjudication_prompt,
)
from applire.providers.llm.mock import MockLLMProvider


@pytest.mark.asyncio
async def test_mock_recognises_the_adjudication_chain() -> None:
    """Must return the real {"answer", "quote"} shape, never the generic
    fallback — mirrors test_mock_reviewer_chain_recognition.py's invariant."""
    prompt = build_stance_adjudication_prompt(
        "Selenium", "technical", "I mostly did QA testing this quarter.",
    )
    result = await MockLLMProvider().aparse_json(
        prompt, system=STANCE_ADJUDICATION_SYSTEM_PROMPT,
    )
    assert "mock" not in result, (
        "MockLLMProvider does not recognise the ADR-061 stance-adjudication "
        "prompt — it fell through to the generic fallback, so every "
        "uncertain-band token silently lands unconfirmed on the mock stack "
        "(safe, but tests a different product than production). Teach "
        "providers/llm/mock.py the 'verifying one narrow claim' fingerprint."
    )
    assert set(result) == {"answer", "quote"}
    assert result["answer"] in ("yes", "no", "unclear")


@pytest.mark.asyncio
async def test_mock_reaches_confirmed_with_a_verified_citation() -> None:
    """The confirmed path must be REACHABLE on the mock stack, not just the
    unconfirmed one — a mock that can only ever answer "no"/"unclear"
    reproduces the exact bug this test guards against. The returned quote
    must be a genuine, literal substring of the turn it was given, so it
    also passes the caller's OWN deterministic citation check
    (``stance.py::_citation_verified``) — not merely well-shaped."""
    turn = (
        "Beim SAP-Rollout bei Rasselstein war ich Key-User für PP; bei "
        "Weberit arbeite ich täglich mit PP und MM."
    )
    prompt = build_stance_adjudication_prompt("SAP PP", "skill", turn)
    result = await MockLLMProvider().aparse_json(
        prompt, system=STANCE_ADJUDICATION_SYSTEM_PROMPT,
    )
    assert result["answer"] == "yes"
    assert result["quote"], "a 'yes' answer must carry a non-empty quote"
    assert result["quote"] in turn, (
        "the mock's own quote must be a LITERAL substring of the turn it was "
        "given — anything else would fail the real citation check too, and "
        "silently reproduce the exact fabricated-quote failure mode #316's "
        "adversarial test guards against, just on the mock side."
    )


@pytest.mark.asyncio
async def test_mock_reaches_unconfirmed_when_the_turn_does_not_support_it() -> None:
    """The negative path stays reachable too — deterministic, not a coin
    flip: no word of TOKEN appears anywhere in TURN."""
    prompt = build_stance_adjudication_prompt(
        "Kubernetes", "technical", "We reduced churn by 3% through quarterly reviews.",
    )
    result = await MockLLMProvider().aparse_json(
        prompt, system=STANCE_ADJUDICATION_SYSTEM_PROMPT,
    )
    assert result == {"answer": "no", "quote": ""}


@pytest.mark.asyncio
async def test_mock_adjudication_is_deterministic() -> None:
    """No randomness (explicit requirement): identical input, identical
    output, across repeated calls and fresh provider instances."""
    prompt = build_stance_adjudication_prompt(
        "OEE (Overall Equipment Effectiveness)",
        "domain",
        "die OEE im Spritzguss ist in 18 Monaten von 61 % auf 73 % gestiegen.",
    )
    results = [
        await MockLLMProvider().aparse_json(prompt, system=STANCE_ADJUDICATION_SYSTEM_PROMPT)
        for _ in range(5)
    ]
    assert all(r == results[0] for r in results)
    assert results[0]["answer"] == "yes"


@pytest.mark.asyncio
async def test_mock_adjudication_wired_through_enforce_stance_end_to_end() -> None:
    """Not just the raw provider call — the real seam, real provider class,
    landing `confirmed` with a citation-verified quote exactly the way a
    correct real-LLM answer would."""
    from applire.services.profile.reconcile.ops import UpsertSkill
    from applire.services.profile.reconcile.stance import enforce_stance

    turn = {
        "gap": "Digital Transformation",
        "question": "Können Sie uns Beispiele nennen?",
        "answer": (
            "Beim SAP-Rollout bei Rasselstein war ich Key-User für PP; bei "
            "Weberit arbeite ich täglich mit PP und MM."
        ),
    }
    ops = [UpsertSkill(name="SAP PP", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=MockLLMProvider(),
    )
    assert len(out) == 1
    assert out[0].status == "confirmed"
