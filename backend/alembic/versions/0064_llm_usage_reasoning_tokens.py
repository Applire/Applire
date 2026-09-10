# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""llm_usage.reasoning_tokens — what the thinking cost (founder ruling M-4).

One additive, non-nullable, defaulted integer column. Existing rows take 0,
which reads correctly: they were written before Applire could tell reasoning
tokens apart from answer tokens, so the split for them is genuinely unknown.

`reasoning_tokens` is a SUBSET of `completion_tokens`, never an addition —
OpenAI-compatible gateways already include it in the completion total and
report the split at `usage.completion_tokens_details.reasoning_tokens`. The
column exists because that split is the number an operator acts on: it is what
they pay for output the product strips before parsing
(`providers/llm/reasoning.py`). Providers with no split (Anthropic bills
thinking inside `output_tokens`; Ollama has none) write 0 rather than a guess.

Still no text column on this table: the PII boundary against the debug log is
structural (SF-OPS.9) and `tests/unit/test_llm_usage_seam.py`'s column
allowlist names this column explicitly so a text column added here fails a
named test instead of shipping.

Chain: 0061 → 0062 (`instance_state`, US310) → 0063 (ops layer, US312/US313)
→ 0064.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0064"
down_revision: Union[str, None] = "0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_usage",
        sa.Column(
            "reasoning_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("llm_usage", "reasoning_tokens")
