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


def test_a_field_the_prompt_calls_required_is_required_in_the_schema():
    """The other direction of the same "second specification" class as the
    test above: `prompts/reconcile.py`'s per-op REQUIRED: line (M-3a) names
    `value` on set_field/set_personal_info, both sides of flag_conflict, and
    `options` on request_confirmation — pydantic's own inference disagrees
    because each carries an `Any = None` / `default_factory=list` default for
    constructor convenience, not because the prompt considers it optional.

    Found adversarially 2026-09-10: without this, a structured-output call
    (`LLM_STRUCTURED_OUTPUT=auto`, the default since P-4) is schema-permitted
    to emit a `set_field` with no `value`, a `flag_conflict` with neither side
    to compare, or a `request_confirmation` with no `options` — the field
    that carries the entire point of the op, contradicting the prompt's own
    REQUIRED: line for the exact shapes M-3a exists to reinforce.

    MUTATION KILL: drop the `_add_prompt_required_fields` call in
    `reconcile_response_schema` and every assertion below fails.
    """
    defs = reconcile_response_schema()["$defs"]
    assert "value" in defs["SetField"]["required"]
    assert "value" in defs["SetPersonalInfo"]["required"]
    assert "existing" in defs["FlagConflict"]["required"]
    assert "incoming" in defs["FlagConflict"]["required"]
    assert "options" in defs["RequestConfirmation"]["required"]


def test_a_field_the_prompt_never_asks_for_is_not_in_the_schema():
    """A schema is a second specification: showing `status` would invite the
    model to assert the candidate CONFIRMED a skill (ADR-061 clause 3).

    (`last_used` used to be hidden here too; RULING P2-1, 2026-09-11 removed the
    field from `UpsertSkill` itself, so there is nothing left to hide.)

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


def test_the_schema_is_on_by_default_and_off_is_an_exact_opt_out():
    """Founder ruling P-4: the default is `auto`, taken against this package's own
    recommendation with the +2,269-input-token price stated. `off` must restore
    today's `json_object` behaviour byte-for-byte, so an operator who does not
    want to pay for it has an exact way out."""
    from applire.config import Settings, settings
    from applire.services.profile.reconcile.engine import _structured_output_schema

    assert Settings.model_fields["llm_structured_output"].default == "auto"

    original = settings.llm_structured_output
    try:
        settings.llm_structured_output = "auto"
        assert _structured_output_schema()["name"] == "applire_reconcile_batch"
        settings.llm_structured_output = "off"
        assert _structured_output_schema() is None
        # An unrecognised value must not silently mean "on" — anything that is
        # not exactly "auto" leaves the vault write path as it was.
        settings.llm_structured_output = "yes"
        assert _structured_output_schema() is None
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


# ── adversarial pass 2026-09-10 — the latch's own wording match ─────────────


@pytest.mark.parametrize("provider_cls_path", [
    "applire.providers.llm.openrouter.OpenRouterProvider",
    "applire.providers.llm.requesty.RequestyProvider",
])
def test_a_field_name_listed_in_an_unrelated_400_does_not_latch(provider_cls_path):
    """A context-length error that lists `response_format` among the request's
    OTHER field names (a gateway echoing what it received) is not a schema
    rejection — the field is merely present, not refused.

    MUTATION KILL: revert `is_schema_rejection_message` to a bare substring
    test (drop the `_SCHEMA_REJECTION_VERDICT` proximity requirement) and this
    fails — the constructed context-length message below latches the schema
    off for the rest of the process on a misdiagnosis.
    """
    module_path, cls_name = provider_cls_path.rsplit(".", 1)
    import importlib

    cls = getattr(importlib.import_module(module_path), cls_name)
    provider = cls.__new__(cls)
    provider._model = "some/model"
    provider._json_schema_rejected = False
    msg = (
        "Error code: 400 - This model's maximum context length is 8192 tokens. "
        "Your request (model, messages, temperature, response_format, "
        "max_tokens) resulted in 9000 tokens. Please reduce the length of the "
        "messages."
    )
    assert provider._note_schema_rejection(Exception(msg)) is False
    assert provider._json_schema_rejected is False


@pytest.mark.parametrize("provider_cls_path", [
    "applire.providers.llm.openrouter.OpenRouterProvider",
    "applire.providers.llm.requesty.RequestyProvider",
])
def test_the_documented_rejection_shape_still_latches_on_both_providers(provider_cls_path):
    """The proximity requirement must not blunt the ONE shape already measured
    (the F-B/M-3 wording `test_a_schema_rejection_is_latched_...` pins for
    OpenRouter) — pinned here for BOTH providers, since the check now lives in
    one shared function both call."""
    module_path, cls_name = provider_cls_path.rsplit(".", 1)
    import importlib

    cls = getattr(importlib.import_module(module_path), cls_name)
    provider = cls.__new__(cls)
    provider._model = "some/model"
    provider._json_schema_rejected = False
    msg = "Error code: 400 - response_format json_schema is not supported"
    assert provider._note_schema_rejection(Exception(msg)) is True
    assert provider._json_schema_rejected is True
