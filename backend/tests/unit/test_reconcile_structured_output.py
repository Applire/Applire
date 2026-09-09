# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Founder ruling M-3 — the op union as a schema, with the rejection latched."""

from __future__ import annotations

import json

import pytest

from applire.services.profile.reconcile.ops import _MODEL_EMITTABLE
from applire.services.profile.reconcile.schema_out import (
    reconcile_json_schema_param,
    reconcile_response_schema,
)


def _emittable_op_names() -> set[str]:
    """The op names `engine._parse_ops` accepts, read off `ops.py` itself."""
    return {model.model_fields["op"].default for model in _MODEL_EMITTABLE}


def test_every_model_emittable_op_is_a_branch_of_the_schema():
    """One source of truth: an op added to `ops.py` cannot drift out of here."""
    schema = reconcile_response_schema()
    branch_names = {
        ref["$ref"].rsplit("/", 1)[-1]
        for ref in schema["properties"]["ops"]["items"]["anyOf"]
    }
    defs = schema["$defs"]
    schema_ops = {
        defs[name]["properties"]["op"]["const"]
        for name in branch_names
        if "op" in defs[name]["properties"]
    }
    assert schema_ops == _emittable_op_names()


def test_the_union_tag_survives_as_a_const_on_every_branch():
    """`oneOf` + pydantic's `discriminator` is not a shape gateways read; the
    per-branch `op` const is what makes the union legible to the model."""
    schema = reconcile_response_schema()
    for name, definition in schema["$defs"].items():
        if name == "RequestConfirmation":
            continue
        assert "const" in definition["properties"]["op"], name
    assert "discriminator" not in schema
    assert "oneOf" not in json.dumps(schema)


def test_the_envelope_is_the_three_keys_the_engine_reads():
    schema = reconcile_response_schema()
    assert list(schema["properties"]) == ["ops", "ambiguities", "denials"]
    assert schema["required"] == ["ops", "ambiguities", "denials"]
    assert schema["properties"]["denials"]["items"] == {"type": "string"}


def test_an_untyped_field_gets_the_json_scalars_rather_than_an_empty_schema():
    """`SetField.value: Any` renders as no constraint at all in pydantic, which
    tells the model nothing about what it may put there."""
    value = reconcile_response_schema()["$defs"]["SetField"]["properties"]["value"]
    assert {"type": "string"} in value["anyOf"]
    assert {"type": "null"} in value["anyOf"]


def test_the_schema_is_not_strict_and_says_why_in_one_place():
    """Strict mode would require re-deciding `SetField.value` and
    `RequestConfirmation.context` (ADR-063) — recorded in the module, asserted
    here so a later change to `strict` cannot be silent."""
    param = reconcile_json_schema_param()
    assert param["strict"] is False
    assert param["name"] == "applire_reconcile_batch"


def _schema_keys(node, out=None):
    out = set() if out is None else out
    if isinstance(node, list):
        for item in node:
            _schema_keys(item, out)
    elif isinstance(node, dict):
        for key, value in node.items():
            out.add(key)
            if key not in ("properties", "$defs"):
                _schema_keys(value, out)
    return out


def test_presentation_keys_are_stripped():
    """Every `title`/`default` pydantic emits is prompt budget for nothing.

    Checked as schema KEYS, not as a substring: `title` is also a real field
    name on `upsert_story` and `upsert_publication`."""
    keys = _schema_keys(reconcile_response_schema())
    assert "title" not in keys
    assert "default" not in keys


def test_a_field_the_prompt_never_asks_for_is_not_in_the_schema():
    """A schema is a second specification: showing `status` would invite the
    model to assert the candidate CONFIRMED a skill (ADR-061 clause 3), and
    showing `last_used` would ask for a field nothing reads
    (`o3/prompt-health.md` §2).

    MUTATION KILL: drop `_hide` from `reconcile_response_schema` and this fails.
    """
    defs = reconcile_response_schema()["$defs"]
    assert "status" not in defs["UpsertSkill"]["properties"]
    assert "last_used" not in defs["UpsertSkill"]["properties"]
    assert "status" not in defs["UpsertCertification"]["properties"]
    assert "option_keys" not in defs["RequestConfirmation"]["properties"]
    # …and the fields the prompt DOES describe are all still there.
    assert "years_experience" in defs["UpsertSkill"]["properties"]
    assert "evidence" in defs["UpsertSkill"]["properties"]


def test_the_engine_sends_no_schema_unless_the_operator_turns_it_on():
    """Default `off` keeps today's `json_object` behaviour byte-for-byte."""
    from applire.config import settings
    from applire.services.profile.reconcile.engine import _structured_output_schema

    original = settings.llm_structured_output
    try:
        settings.llm_structured_output = "off"
        assert _structured_output_schema() is None
        settings.llm_structured_output = "auto"
        assert _structured_output_schema()["name"] == "applire_reconcile_batch"
    finally:
        settings.llm_structured_output = original


@pytest.mark.asyncio
async def test_a_schema_rejection_is_latched_and_the_call_still_returns():
    """An endpoint without structured output costs ONE 400 per process, and the
    turn it happened on still completes on plain JSON mode.

    MUTATION KILL: remove the `_note_schema_rejection` branch in `_create` and
    the first call raises instead of returning.
    """
    import openai as openai_sdk

    from applire.providers.llm.openrouter import OpenRouterProvider

    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider._model = "some/model"
    provider._reasoning_effort = ""
    provider._reasoning_rejected = False
    provider._json_schema_rejected = False

    seen: list[str] = []

    class _Client:
        class chat:  # noqa: N801
            class completions:
                @staticmethod
                async def create(**kwargs):
                    fmt = (kwargs.get("response_format") or {}).get("type")
                    seen.append(fmt)
                    if fmt == "json_schema":
                        exc = openai_sdk.BadRequestError.__new__(openai_sdk.BadRequestError)
                        Exception.__init__(
                            exc, "Error code: 400 - response_format json_schema is not supported"
                        )
                        raise exc
                    return "ok"

    provider._client = _Client()

    schema_fmt = {"type": "json_schema", "json_schema": reconcile_json_schema_param()}
    assert await provider._create(
        max_tokens=1024, extra_body=None, response_format=schema_fmt
    ) == "ok"
    assert provider._json_schema_rejected is True
    assert seen == ["json_schema", "json_object"]


def test_an_unrelated_400_does_not_disable_structured_output():
    """A context-length 400 that merely co-occurs must not latch the schema off."""
    from applire.providers.llm.openrouter import OpenRouterProvider

    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider._model = "some/model"
    provider._json_schema_rejected = False
    assert provider._note_schema_rejection(Exception("400 - context length exceeded")) is False
    assert provider._json_schema_rejected is False
