"""usage-based billing: report each request's exact cost to a Stripe Billing Meter

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("usage_billing", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_table(
        "usage_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("stripe_customer_id", sa.Text(), nullable=False),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("value_picousd", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_usage_reports"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_usage_reports_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_usage_reports_tenant_key"),
        sa.UniqueConstraint("identifier", name="uq_usage_reports_identifier"),
        sa.CheckConstraint("value_picousd > 0", name="ck_usage_reports_value_positive"),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'dead')", name="ck_usage_reports_status_known"
        ),
    )
    op.create_index(
        "ix_usage_reports_status_next_attempt_at", "usage_reports", ["status", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_usage_reports_status_next_attempt_at", table_name="usage_reports")
    op.drop_table("usage_reports")
    op.drop_column("subscriptions", "usage_billing")
