"""Persist dataset records and give each run item the record it evaluates.

Before this, a dataset version stored a digest, a storage URI and a validation
report — but not the records. Nothing ever wrote to the storage URI, so the
parsed records were discarded after validation. A run item then carried only its
index, which is why the recorded executor built tool arguments from that index:
there was nothing else available.

The consequence was that an evaluation platform could not show an agent the item
it was being evaluated on. A model-driven agent would have been asked to
diagnose an incident it could not see.

Both columns default to empty, so existing rows remain valid. Runs created
before this migration keep an empty payload, which is honest — that data was
never captured and cannot be reconstructed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_item_payload"
down_revision: str | None = "0019_tribunal_rounds"  # lgtm[py/unused-global-variable]
branch_labels: str | None = None
depends_on: str | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "dataset_versions",
        sa.Column(
            "records",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "run_items",
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("run_items", "payload")
    op.drop_column("dataset_versions", "records")
