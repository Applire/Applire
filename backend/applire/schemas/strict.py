# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""US250 (E044, ADR-054) — unknown-field detection for agent-authored content.

``TailoredCVData`` and its nested models are deliberately lenient (the LLM
writer's output is coerced, extra keys ignored). On the agent door that
leniency is a trap: an agent typo like ``work_experience`` instead of
``work_history`` would silently render an empty CV. This walker reports every
key in the submitted data that no model field will absorb, as dotted paths the
caller can act on (US251 surfaces them via ``invalid_input``).

Kept separate from the schemas so the pipeline's lenient validation is
untouched (the letter contract handles this natively via ``extra="forbid"``).
"""
from typing import Union, get_args, get_origin

from pydantic import BaseModel


def _model_in_annotation(annotation) -> type[BaseModel] | None:
    """Return the BaseModel subclass inside an annotation, unwrapping
    Optional[...] and list[...] one level at a time."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = get_origin(annotation)
    if origin in (list, Union):
        for arg in get_args(annotation):
            found = _model_in_annotation(arg)
            if found is not None:
                return found
    return None


def find_unknown_fields(
    model_cls: type[BaseModel], data: object, path: str = ""
) -> list[str]:
    """Dotted paths of keys in *data* that *model_cls* (recursively) does not define.

    Non-dict data is left to pydantic's own validation — this walker only
    catches what pydantic would silently IGNORE, not what it would reject.
    """
    if not isinstance(data, dict):
        return []

    unknown: list[str] = []
    fields = model_cls.model_fields
    known = set(fields)
    for f_name, f_info in fields.items():
        if f_info.alias:
            known.add(f_info.alias)

    for key, value in data.items():
        key_path = f"{path}.{key}" if path else str(key)
        if key not in known:
            unknown.append(key_path)
            continue
        f_info = fields.get(key)
        if f_info is None:  # matched via alias
            f_info = next(fi for fi in fields.values() if fi.alias == key)
        nested = _model_in_annotation(f_info.annotation)
        if nested is None:
            continue
        if isinstance(value, list):
            for i, item in enumerate(value):
                unknown.extend(find_unknown_fields(nested, item, f"{key_path}[{i}]"))
        else:
            unknown.extend(find_unknown_fields(nested, value, key_path))
    return unknown
