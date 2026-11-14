"""core schema: plans, tenants, idempotency keys, usage events, usage counters

Revision ID: 0001
Revises:
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

METRIC_CHECK = "metric IN ('api_calls', 'ai_tokens')"


def upgrade() -> None:
    plans = op.create_table(
        "plans",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("api_calls_limit", sa.BigInteger(), nullable=False),
        sa.Column("ai_tokens_limit", sa.BigInteger(), nullable=False),
        sa.Column("stripe_price_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_plans"),
        sa.CheckConstraint(
            "api_calls_limit >= 0 AND ai_tokens_limit >= 0",
            name="ck_plans_limits_non_negative",
        ),
    )
    # Reference data: plan limits are part of the schema contract (docs/quotas.md).
    op.bulk_insert(
        plans,
        [
            {"id": "free", "name": "Free", "api_calls_limit": 1_000, "ai_tokens_limit": 100_000},
            {"id": "pro", "name": "Pro", "api_calls_limit": 50_000, "ai_tokens_limit": 5_000_000},
        ],
    )

    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("api_key_hash", sa.Text(), nullable=False),
        sa.Column("plan_id", sa.Text(), server_default="free", nullable=False),
        sa.Column("stripe_customer_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name="fk_tenants_plan_id_plans"),
        sa.UniqueConstraint("name", name="uq_tenants_name"),
        sa.UniqueConstraint("api_key_hash", name="uq_tenants_api_key_hash"),
        sa.UniqueConstraint("stripe_customer_id", name="uq_tenants_stripe_customer_id"),
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("tenant_id", "key", name="pk_idempotency_keys"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_idempotency_keys_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("length(key) BETWEEN 1 AND 255", name="ck_idempotency_keys_key_length"),
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_usage_events"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_usage_events_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "idempotency_key"],
            ["idempotency_keys.tenant_id", "idempotency_keys.key"],
            name="fk_usage_events_tenant_id_idempotency_keys",
            ondelete="CASCADE",
        ),
        # Last line of defence against double counting (docs/idempotency.md).
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", "metric", name="uq_usage_events_tenant_key_metric"
        ),
        sa.CheckConstraint("quantity > 0", name="ck_usage_events_quantity_positive"),
        sa.CheckConstraint(METRIC_CHECK, name="ck_usage_events_metric_known"),
    )
    op.create_index(
        "ix_usage_events_tenant_period_metric",
        "usage_events",
        ["tenant_id", "period_start", "metric"],
    )

    op.create_table(
        "usage_counters",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("used", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "period_start", "metric", name="pk_usage_counters"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_usage_counters_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("used >= 0", name="ck_usage_counters_used_non_negative"),
        sa.CheckConstraint(METRIC_CHECK, name="ck_usage_counters_metric_known"),
    )


def downgrade() -> None:
    op.drop_table("usage_counters")
    op.drop_index("ix_usage_events_tenant_period_metric", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_table("idempotency_keys")
    op.drop_table("tenants")
    op.drop_table("plans")
