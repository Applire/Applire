# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5 (U-11, U-9) — request / response shapes of the entry-merge
and keep-separate doors (``POST /api/profile/entries/merge`` and
``/entries/keep-separate``)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MergeSection = Literal["work_experience", "volunteer_activities", "projects", "education"]
DuplicateOutcome = Literal["keep_current", "take_new", "keep_both", "merge"]


class DuplicateChoice(BaseModel):
    """The candidate's answer for one offered duplicate pair. The two texts are
    echoed so the commit can refuse a pair the entries no longer hold."""

    model_config = ConfigDict(extra="forbid")

    pair_id: str = Field(min_length=1)
    target_text: str
    source_text: str
    outcome: DuplicateOutcome
    merged_text: str | None = None


class MergeChoices(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dates: Literal["target", "source"] | None = None
    duplicates: list[DuplicateChoice] = Field(default_factory=list)


class EntryMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: MergeSection
    target_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    mode: Literal["preview", "commit"]
    choices: MergeChoices | None = None


class AliasRow(BaseModel):
    title: str | None = None
    company: str | None = None


class DateSpan(BaseModel):
    start_date: str | None = None
    end_date: str | None = None


class DateConflict(BaseModel):
    target: DateSpan
    source: DateSpan


class DuplicatePairOut(BaseModel):
    pair_id: str
    target_text: str
    source_text: str
    proposal: str | None = None


class EntryMergePreview(BaseModel):
    merged_entry: dict[str, Any]
    aliases: list[AliasRow] = Field(default_factory=list)
    date_conflict: DateConflict | None = None
    duplicate_pairs: list[DuplicatePairOut] = Field(default_factory=list)
    unique_bullets: list[str] = Field(default_factory=list)


class KeepSeparateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: MergeSection
    entry_ids: list[str] = Field(min_length=2, max_length=2)
