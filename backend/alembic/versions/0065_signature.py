# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""user_settings signature — the stored image and its two render toggles (#359).

Two additive, non-nullable, defaulted boolean columns. The defaults ARE the
founder's decision (default F-0, 2026-09-11) and the issue's own proposal: the
Anschreiben is expected to carry a signature in DACH practice, the Lebenslauf's
is traditional but increasingly optional — so `signature_in_letter` defaults to
TRUE and `signature_in_cv` to FALSE, both user-overridable.

Server defaults rather than a nullable column with an application-side default:
every existing row must read as "the convention", not as "never chosen". There
is no third state here to preserve — unlike `ui_language` (0055/ADR-038, where
NULL genuinely means "the user never chose" and the served 'en' was not a
choice), a signature toggle has no behaviour that differs between "defaulted"
and "explicitly set to the default value".

`signature_path` is the third column and the one that is NOT where a reader
would first look for it. The profile photo's path lives in
`master_profiles.profile_json.personal_info.photo_url`; the signature's
deliberately does not (ADR-088, founder ruling F-3 of 2026-09-11). The whole
`personal_info` object is rendered into the reconciler's input view, so a path
stored there is a file path shown to an LLM — and that render is pinned
byte-for-byte by the model-qualification goldens, whose README states that
editing them invalidates every published matrix row (#688). A signature is also
document chrome attached at render time rather than a claim about the candidate,
so it belongs beside the toggles that decide whether it renders.

The price of leaving the vault is that the file leaves the vault's file
lifecycle, and it is paid in code rather than in a comment: the retention
worker's orphan scan reads this column into its referenced set (a signature file
absent from that set is not merely unprotected — it is deleted once past the
grace period), and the Art. 17 erasure path deletes the file. Both are covered
by named tests.

Chain: 0063 (ops layer) -> 0064 (`llm_usage.reasoning_tokens`) -> 0065.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0065"
down_revision: Union[str, None] = "0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column("signature_path", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "user_settings",
        sa.Column(
            "signature_in_letter",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "user_settings",
        sa.Column(
            "signature_in_cv",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "signature_in_cv")
    op.drop_column("user_settings", "signature_in_letter")
    op.drop_column("user_settings", "signature_path")
