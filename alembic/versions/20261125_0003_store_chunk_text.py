"""Store extracted text for new chunks; preserve legacy registry rows."""

import sqlalchemy as sa

from alembic import op

revision = "20261125_0003"
down_revision = "20261013_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("chunk_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_chunks", "chunk_text")
