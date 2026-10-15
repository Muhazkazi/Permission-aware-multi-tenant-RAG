"""Initialize Alembic migration infrastructure without application tables.

Revision ID: 20261010_0001
Revises:
Create Date: 2026-10-10
"""

from typing import Sequence

# revision identifiers, used by Alembic.
revision: str = "20261010_0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Establish the baseline revision without creating application tables."""


def downgrade() -> None:
    """Revert the empty baseline revision."""
