"""usage alerts: 80% / 100% quota notifications, once per threshold per period

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_alerts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("threshold_percent", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Text(), nullable=False),
        sa.Column("quota_limit", sa.BigInteger(), nullable=False),
        sa.Column("used_at_trigger", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivery", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_usage_alerts"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_usage_alerts_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        # The "exactly once per threshold" guarantee. quota_limit is part of the key so an
        # upgrade to Pro (new limit) re-arms the thresholds for the rest of the month.
        sa.UniqueConstraint(
            "tenant_id",
            "period_start",
            "metric",
            "threshold_percent",
            "quota_limit",
            name="uq_usage_alerts_once",
        ),
        sa.CheckConstraint(
            "metric IN ('api_calls', 'ai_tokens')", name="ck_usage_alerts_metric_known"
        ),
        sa.CheckConstraint(
            "threshold_percent IN (80, 100)", name="ck_usage_alerts_threshold_known"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'dead')", name="ck_usage_alerts_status_known"
        ),
        sa.CheckConstraint(
            "quota_limit > 0 AND used_at_trigger >= 0", name="ck_usage_alerts_amounts_valid"
        ),
    )
    op.create_index(
        "ix_usage_alerts_status_next_attempt_at", "usage_alerts", ["status", "next_attempt_at"]
    )
    op.create_index("ix_usage_alerts_tenant_period", "usage_alerts", ["tenant_id", "period_start"])


def downgrade() -> None:
    op.drop_index("ix_usage_alerts_tenant_period", table_name="usage_alerts")
    op.drop_index("ix_usage_alerts_status_next_attempt_at", table_name="usage_alerts")
    op.drop_table("usage_alerts")
