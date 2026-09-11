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

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base

# ADR-002 pattern: JSONB on PostgreSQL, plain JSON on SQLite (unit tests).
_JSON = JSONB().with_variant(JSON(), "sqlite")


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    default_color_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cv_color_profiles.id"), nullable=True
    )
    # ADR-038 (amended 2026-08-01, #400): nullable — NULL means the user never
    # explicitly chose a language (always *served* as 'en'). Only a write that
    # carries ui_language may set it; the 'en' default standing in for a choice
    # is what sent English interview questions into a German agent-channel run.
    ui_language: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # ADR-040 (amended 2026-07-01): when true, suppress the clean-case pre-download
    # "AI content" notice across BOTH the CV and cover-letter surfaces. A real
    # red-flag notice is never suppressed by this.
    hide_predownload_notice: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    # E042/US236 (ADR-051 §1): per-user default target CV page count.
    # NULL = "use region standard" (REGION_NORMS[region].cv_standard_pages via
    # resolve_target_pages()). No upper cap — users may deliberately exceed
    # the norm; validated >= 1 at the API layer.
    target_cv_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ADR-081 clause 5 (US301): three-valued document-review preference.
    # 'auto' follows the DOCUMENT (guided while it has unwalked group-1
    # findings, overview otherwise); 'overview'/'guided' are fixed user
    # overrides. Deliberately NOT exposed over MCP (clause 8, SF-DOOR.4).
    review_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="auto", default="auto"
    )
    # #679 (US309): the ids of first-use explainers this user dismissed via
    # "Nicht mehr anzeigen". ONE additive set for every explainer that comes
    # after the pre-download notice, instead of a new boolean column each time.
    # `hide_predownload_notice` above keeps its own column (ADR-040 §4) — it
    # predates the mechanism and is not migrated into it.
    # Ids are validated against routers.settings.EXPLAINER_IDS at write time;
    # order is write order, so a reader can tell which explainer was dismissed
    # first. Like `review_mode`, deliberately NOT exposed over MCP
    # (ADR-081 clause 8, SF-DOOR.4): an agent has no explainer to dismiss.
    dismissed_explainers: Mapped[list] = mapped_column(
        _JSON, nullable=False, server_default="[]", default=list
    )
    # #359: whether a stored signature image is rendered on each document kind.
    # The founder's defaults are the DACH convention as the issue states it —
    # the Anschreiben is expected to carry a signature, the Lebenslauf's is
    # traditional but increasingly optional — so the letter is ON and the CV is
    # OFF, both user-overridable (founder default F-0, 2026-09-11).
    # Read at RENDER time, not pinned onto the document row: a user may toggle
    # and re-download without paying for a regeneration. That trade is recorded
    # in SF-PDF.6's Cause cell rather than left implicit.
    # Two columns rather than one enum/JSON set, because there are exactly two
    # document kinds and the settings contract is read by the frontend on every
    # page — a set would cost a lookup at every read site for no extensibility
    # the product has asked for.
    # #359: the stored signature IMAGE's path in the storage provider.
    # Deliberately NOT in `master_profiles.profile_json.personal_info`, where
    # the profile photo's path lives (founder ruling F-3, 2026-09-11). Three
    # reasons, in the order they were found:
    #   1. The reconcile prompt renders the whole `personal_info` object into
    #      the model's input view — `photo_url` is in there today. A file path
    #      is input the model has no business reading; adding a second one made
    #      the surface worse, not equal.
    #   2. That render is pinned byte-for-byte by the model-qualification
    #      goldens (`tests/files/model_matrix/golden/`), whose README states
    #      that any edit invalidates every published matrix row (#688).
    #   3. A signature is document CHROME the user attaches at render time, not
    #      a claim about the candidate. The photo is CV content and Art. 9 data;
    #      this is neither. It therefore belongs with the settings that decide
    #      whether it renders, which is exactly where it now is.
    # The cost — it is outside the vault's own file lifecycle — is paid
    # explicitly: `retention/worker.py`'s orphan scan reads this column, and the
    # Art. 17 erasure path deletes the file. Both are named tests, not comments.
    signature_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    signature_in_letter: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    signature_in_cv: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
