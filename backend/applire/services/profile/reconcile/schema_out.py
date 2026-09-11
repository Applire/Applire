# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Founder ruling M-3 — hand the model the op union as a SCHEMA, not only as prose.

``engine.py`` asks for JSON with ``response_format={"type": "json_object"}``:
free-form JSON, no shape. The 15-branch discriminated union exists only as prose
in ``prompts/reconcile.py`` and as a pydantic validator that runs *after* the
call, so a model that invents a field name, mangles the ``op`` discriminator or
drops a required field is not corrected — its op is dropped whole, silently
(``o3/prompt-health.md`` §2, "JSON mode is not schema enforcement"; measured
shapes in ``o3/failure-taxonomy-2026-09-09.md`` §3.5).

This module renders the SAME union pydantic already validates against into a
JSON Schema the gateway can pass to the model. One source of truth: the schema
is derived from ``ReconcileOp`` at call time, so an op added to ``ops.py``
cannot drift out of it.

**Why ``strict`` is False, and what it would cost to change.** OpenAI-style
*strict* structured output requires every object to close
(``additionalProperties: false``) and to list every property in ``required``.
Two model-emittable ops cannot satisfy that as they are defined today:

* ``SetField.value: Any`` — a scalar whose type depends on the field being
  filled (a date string, a headcount int, an ``is_current`` bool);
* ``RequestConfirmation.context: dict[str, Any]`` — a deliberately free-form
  bag ("any helpful keys", ``prompts/reconcile.py``), which a closed object
  schema cannot express at all.

Making the union strict therefore means re-deciding those two shapes, which is
ADR-063 territory and a founder call, not a provider detail. Non-strict schema
guidance needs neither, is accepted by the gateways that support structured
output, and is rejected cleanly by the ones that do not — so it is what ships,
with the tension recorded here rather than discovered later.

**Graceful fallback is the capability check.** Rather than a second network
probe per model, the caller sends the schema once and latches a rejection
(``providers/llm/openrouter.py`` / ``requesty.py``), exactly as those providers
already latch a mandatory-reasoning rejection. A model that cannot take a schema
costs one 400 per process, not one per call, and falls back to today's
``json_object`` behaviour with no change in output shape.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, get_args

_SCHEMA_NAME = "applire_reconcile_batch"

#: What an untyped (`Any`) property becomes. `SetField.value` really is a
#: date string, a headcount int, an `is_current` bool or null depending on the
#: field it fills — enumerating the JSON scalars is more informative to the
#: model than the empty schema pydantic emits, and admits exactly what the
#: applier's own coercions accept.
_ANY_SCALAR: dict[str, Any] = {
    "anyOf": [
        {"type": "string"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "null"},
    ]
}
#: Keys pydantic emits that carry no constraint and only cost the model tokens.
_NOISE_KEYS = ("title", "default")

#: Fields the SCHEMA must not show, because the PROMPT deliberately does not ask
#: for them. A schema is a second specification of the same output: any field it
#: names that the prompt does not is an invitation the prompt spent words
#: avoiding, and the model will take it.
#:
#: * ``status`` — ADR-061 clause 3 reserves it for ``enforce_stance``; a model
#:   that sets it is asserting the candidate CONFIRMED a skill it just invented.
#:   (``last_used`` stood here until RULING P2-1, 2026-09-11 removed the field
#:   from ``UpsertSkill`` altogether — the entry went with it.)
#: * the ``RequestConfirmation`` i18n/key trio — adapter-only (ADR-063 amended
#:   2026-09-05, ruling V-4); ``engine._strip_adapter_only`` removes them from
#:   model output, so showing them would advertise a field that is thrown away.
_PROMPT_HIDDEN_FIELDS = frozenset(
    {"status", "question_i18n", "options_i18n", "option_keys"}
)

#: The other direction of the same "second specification" class (adversarial
#: pass 2026-09-10) — a field the PROMPT's own per-op ``REQUIRED:`` line (M-3a)
#: names, that pydantic's OWN required-field inference disagrees with, because
#: the field carries an ``Any = None`` / ``default_factory=list`` default for
#: constructor convenience (existing unit tests build these ops without every
#: field filled), not because the prompt considers it optional. Left alone,
#: the rendered schema tells the model the OPPOSITE of what the prompt's
#: REQUIRED: line says, for the field that carries the entire point of the op:
#: a ``set_field``/``set_personal_info`` with no ``value`` sets nothing, a
#: ``flag_conflict`` with neither ``existing`` nor ``incoming`` shows the user
#: a conflict with nothing to compare, a ``request_confirmation`` with no
#: ``options`` is a choice question with no choices.
#:
#: Keyed by the schema's ``$defs`` name (== the op's pydantic class name).
#: **Known, accepted limit** (same one the module docstring already states for
#: ``strict=False``): JSON Schema ``required`` only forces the KEY to be
#: present, never a non-null/non-empty VALUE — a model can still satisfy this
#: with ``"value": null`` or ``"options": []``. Closing that needs the
#: strict-mode redesign ADR-063 already reserves; this closes the cheaper,
#: still-real half — the schema no longer flatly CONTRADICTS the prompt.
_PROMPT_REQUIRED_EXTRA: dict[str, tuple[str, ...]] = {
    "SetField": ("value",),
    "SetPersonalInfo": ("value",),
    "FlagConflict": ("existing", "incoming"),
    "RequestConfirmation": ("options",),
}


def _add_prompt_required_fields(defs: dict[str, Any]) -> None:
    """Add each ``_PROMPT_REQUIRED_EXTRA`` field to its op's ``required`` list.

    In place, after ``defs`` is fully assembled — this looks up definitions by
    name rather than walking the tree (unlike ``_hide``/``_clean``) because the
    fields to add are keyed by WHICH op, not by field name alone (``value`` is
    required on ``SetField`` and ``SetPersonalInfo`` but stays untouched on
    ``FlagConflict``, where it does not exist at all).
    """
    for name, extra_fields in _PROMPT_REQUIRED_EXTRA.items():
        definition = defs.get(name)
        if not isinstance(definition, dict):
            continue
        properties = definition.get("properties")
        if not isinstance(properties, dict):
            continue
        required = definition.setdefault("required", [])
        for field in extra_fields:
            if field in properties and field not in required:
                required.append(field)


def _clean(node: Any) -> Any:
    """Drop presentation-only keys and give an untyped property a type."""
    if isinstance(node, list):
        return [_clean(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {k: _clean(v) for k, v in node.items() if k not in _NOISE_KEYS}
    # An `Any`-typed field renders as a schema with no constraint at all, which
    # tells the model nothing. Only substitute when the node genuinely carries
    # no shape — never over a `$ref`, an enum or a composed schema.
    if not out or set(out) <= {"description"}:
        return {**out, **_ANY_SCALAR}
    return out


def _hide(node: Any) -> Any:
    """Remove every field the prompt does not ask for, and its `required` entry."""
    if isinstance(node, list):
        return [_hide(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {k: _hide(v) for k, v in node.items()}
    props = out.get("properties")
    if isinstance(props, dict):
        out["properties"] = {
            k: v for k, v in props.items() if k not in _PROMPT_HIDDEN_FIELDS
        }
        required = out.get("required")
        if isinstance(required, list):
            out["required"] = [r for r in required if r not in _PROMPT_HIDDEN_FIELDS]
    return out


@lru_cache(maxsize=1)
def reconcile_response_schema() -> dict[str, Any]:
    """The reconciler's output envelope as a JSON Schema, cached per process.

    Shape: ``{"ops": [<any of the 15 ops>], "ambiguities": [<confirmation>],
    "denials": ["..."], "empty_reason": <enum|null>}`` — the same four keys
    ``engine._parse_ops`` / ``_parse_ambiguities`` / ``_parse_denials`` /
    ``_parse_empty_reason`` read, in the same order the prompt states them.

    ``empty_reason`` is the one OPTIONAL key (ADR-046 amended 2026-09-11,
    ruling M5.1.4): the prompt asks for it only when ``ops`` is empty, so
    putting it in ``required`` would contradict the prose on every ordinary
    turn — the exact "second specification" failure ``_hide`` and
    ``_PROMPT_REQUIRED_EXTRA`` exist for, in its third shape. Its ``enum`` is
    read from the ONE ``EmptyReason`` Literal, so the schema cannot drift from
    the parser's accepted set.
    """
    from pydantic import TypeAdapter

    from applire.services.profile.reconcile.ops import (
        EmptyReason,
        ReconcileOp,
        RequestConfirmation,
    )

    op_schema = _hide(_clean(TypeAdapter(ReconcileOp).json_schema()))
    defs = op_schema.pop("$defs", {})
    # `oneOf` + `discriminator` is pydantic's rendering of a tagged union.
    # Gateways understand `anyOf`; the tag survives as each branch's `op`
    # `const`, which is what makes the union readable to the model at all.
    branches = op_schema.pop("oneOf", None) or op_schema.pop("anyOf", None) or []
    op_schema.pop("discriminator", None)

    # Read through a `TypeAdapter`, the same idiom as the op union above — not
    # `RequestConfirmation.model_json_schema()`. `test_669_confirmation_option_keys`
    # guards the tree against any attribute call on this class, because eight of
    # the nine sites that touch it BUILD a confirmation and must route through
    # `confirmations.py` instead. Reading a schema is not building an op, but
    # using one idiom for both reads keeps that guard as strict as it is rather
    # than adding an exception entry to it.
    confirmation = _hide(_clean(TypeAdapter(RequestConfirmation).json_schema()))
    defs.update(confirmation.pop("$defs", {}))
    confirmation_ref = {"$ref": "#/$defs/RequestConfirmation"}
    defs.setdefault("RequestConfirmation", confirmation)

    # The other half of the "second specification" discipline `_hide` already
    # applies — see `_PROMPT_REQUIRED_EXTRA`'s docstring.
    _add_prompt_required_fields(defs)

    return {
        "$defs": defs,
        "type": "object",
        "properties": {
            "ops": {"type": "array", "items": {"anyOf": branches}},
            "ambiguities": {"type": "array", "items": confirmation_ref},
            "denials": {"type": "array", "items": {"type": "string"}},
            "empty_reason": {
                "type": ["string", "null"],
                "enum": [*get_args(EmptyReason), None],
            },
        },
        "required": ["ops", "ambiguities", "denials"],
    }


def reconcile_json_schema_param() -> dict[str, Any]:
    """The ``json_schema`` block a provider passes as ``response_format``."""
    return {
        "name": _SCHEMA_NAME,
        # See the module docstring: `strict` would require re-deciding
        # `SetField.value` and `RequestConfirmation.context` (ADR-063), so the
        # schema is guidance, and the pydantic validator after the call stays
        # the enforcement boundary it has always been.
        "strict": False,
        "schema": reconcile_response_schema(),
    }
