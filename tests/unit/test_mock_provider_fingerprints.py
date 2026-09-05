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

"""Infra collector #658 — every system prompt must be visible to the mock provider.

A prompt the mock does not recognise degrades to the generic
``{"mock": True, "raw_prompt_length": N}`` fallback. The caller then sees a shape
it treats as "nothing came back", **silently**, so that seam is exercised by no
IQ/OQ/PQ run at all and CI cannot tell whether it works. That is the #264 lesson,
and it had happened at least twice: `skill_estimation` never had a fingerprint,
and the v1 ``"cover-letter quality auditor"`` fingerprint died unnoticed when the
prompt's opening line was reworded.

The check is **behavioural, not a source grep**: each constant is fed to the real
``MockLLMProvider.aparse_json`` and the fallback is what fails the test — so a
reworded prompt breaks it exactly as a missing branch does, which a scan of
``mock.py``'s string literals could never see.

**The probe prompts are the interesting part.** Several branches are
*prompt-keyed*: they parse the user prompt and return the generic fallback when
its shape is unrecognisable, which is their own documented fail-safe. Probing
those with an empty prompt reports them as unmatched — a false positive the first
version of this check produced for `stance_adjudication`, whose fingerprint has
been present and correct all along. They therefore get a REAL user prompt built
by their own prompt builder, rather than an entry on a carve-out list: the
predicate stays uniform, and every constant is genuinely asserted.
"""
import importlib
import pkgutil

import pytest

import applire.prompts as prompts_package
from applire.prompts.skill_estimation import build_skill_estimation_prompt
from applire.prompts.stance_adjudication import build_stance_adjudication_prompt
from applire.providers.llm.mock import MockLLMProvider

_GENERIC_PROBE = "Probe user prompt for the mock fingerprint enumeration."

#: Prompt-keyed branches: a real user prompt from the module's own builder, so
#: the branch can do its job instead of hitting its shape fail-safe.
_PROBE_PROMPTS: dict[str, str] = {
    "STANCE_ADJUDICATION_SYSTEM_PROMPT": build_stance_adjudication_prompt(
        "Kubernetes", "skill", "Ich habe drei Jahre mit Kubernetes gearbeitet."
    ),
    "SKILL_ESTIMATION_SYSTEM_PROMPT": build_skill_estimation_prompt(
        [{"company": "Beispiel GmbH", "description": "Kubernetes und Python."}],
        ["Kubernetes", "Rust"],
    ),
}

#: The count at the time this check was written. A guard against the enumeration
#: silently finding nothing — a check that collects zero constants is green and
#: worthless (#438).
_MINIMUM_EXPECTED_CONSTANTS = 35


def _system_prompt_constants() -> list[tuple[str, str, str]]:
    """Every ``*SYSTEM*`` string constant under ``applire/prompts/`` — the positive set."""
    found: list[tuple[str, str, str]] = []
    for module_info in pkgutil.iter_modules(prompts_package.__path__):
        module = importlib.import_module(f"applire.prompts.{module_info.name}")
        for name, value in vars(module).items():
            if "SYSTEM" in name and isinstance(value, str):
                found.append((module_info.name, name, value))
    return sorted(found)


def _is_generic_fallback(response: object) -> bool:
    return isinstance(response, dict) and set(response) == {"mock", "raw_prompt_length"}


def test_the_enumeration_finds_the_prompt_constants():
    constants = _system_prompt_constants()
    assert len(constants) >= _MINIMUM_EXPECTED_CONSTANTS, (
        f"only {len(constants)} *SYSTEM* constants found under applire/prompts/ — "
        "the enumeration broke, and a check that enumerates nothing passes for "
        "the wrong reason"
    )


@pytest.mark.asyncio
async def test_every_system_prompt_constant_is_recognised_by_the_mock_provider():
    provider = MockLLMProvider()
    unmatched: list[str] = []

    for module_name, constant_name, system in _system_prompt_constants():
        prompt = _PROBE_PROMPTS.get(constant_name, _GENERIC_PROBE)
        response = await provider.aparse_json(prompt, system=system)
        if _is_generic_fallback(response):
            unmatched.append(f"applire/prompts/{module_name}.py::{constant_name}")

    assert unmatched == [], (
        "These system prompts match no fingerprint in providers/llm/mock.py, so "
        "their call sites degrade to the generic {'mock': ...} fallback and are "
        f"exercised by NO mock-stack IQ/OQ/PQ run: {unmatched}. Add a branch "
        "returning the real response shape (see the oracle/stance branches), or "
        "— if the prompt was merely reworded — repair its fingerprint."
    )


@pytest.mark.asyncio
async def test_the_check_detects_a_prompt_with_no_fingerprint():
    """Negative control: the predicate must be able to fire.

    Without this, a `_is_generic_fallback` that never returns True would make
    the check above permanently green.
    """
    provider = MockLLMProvider()
    response = await provider.aparse_json(
        _GENERIC_PROBE, system="You are a system prompt nobody has ever fingerprinted."
    )
    assert _is_generic_fallback(response)


@pytest.mark.asyncio
async def test_skill_estimation_returns_the_real_response_shape():
    """#658 — the branch this collector line asked for, and what it must return."""
    provider = MockLLMProvider()
    from applire.prompts.skill_estimation import SKILL_ESTIMATION_SYSTEM_PROMPT

    estimates = await provider.aparse_json(
        _PROBE_PROMPTS["SKILL_ESTIMATION_SYSTEM_PROMPT"],
        system=SKILL_ESTIMATION_SYSTEM_PROMPT,
    )

    assert set(estimates) == {"Kubernetes", "Rust"}
    # Grounded: "Kubernetes" appears in the supplied experience history, "Rust"
    # does not — so the mock exercises BOTH branches of the caller's handling
    # instead of always answering the same way.
    assert isinstance(estimates["Kubernetes"], int) and estimates["Kubernetes"] > 0
    assert estimates["Rust"] is None


@pytest.mark.asyncio
async def test_skill_estimation_never_invents_a_skill_the_caller_did_not_ask_for():
    provider = MockLLMProvider()
    from applire.prompts.skill_estimation import SKILL_ESTIMATION_SYSTEM_PROMPT

    prompt = build_skill_estimation_prompt(
        [{"company": "Beispiel GmbH", "description": "Python, Terraform, Ansible."}],
        ["Python"],
    )
    estimates = await provider.aparse_json(prompt, system=SKILL_ESTIMATION_SYSTEM_PROMPT)
    assert set(estimates) == {"Python"}
