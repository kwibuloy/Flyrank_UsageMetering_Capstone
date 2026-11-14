"""SQLAlchemy models. The schema itself is owned by Alembic migrations (migrations/).

Spec: docs/data-model.md. Money and counters are BIGINT.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

METRIC_CHECK = "metric IN ('api_calls', 'ai_tokens')"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint(
            "api_calls_limit >= 0 AND ai_tokens_limit >= 0", name="limits_non_negative"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    api_calls_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ai_tokens_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stripe_price_id: Mapped[str | None] = mapped_column(Text)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    api_key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    plan_id: Mapped[str] = mapped_column(
        Text, ForeignKey("plans.id"), nullable=False, server_default="free"
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (CheckConstraint("length(key) BETWEEN 1 AND 255", name="key_length"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    # JSON (not JSONB): JSONB reorders keys, and a replay must be byte-identical.
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UsageEvent(Base):
    """Append-only. One row per metric per accepted request."""

    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", "metric", name="uq_usage_events_tenant_key_metric"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "idempotency_key"],
            ["idempotency_keys.tenant_id", "idempotency_keys.key"],
            ondelete="CASCADE",
        ),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(METRIC_CHECK, name="metric_known"),
        Index("ix_usage_events_tenant_period_metric", "tenant_id", "period_start", "metric"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UsageCounter(Base):
    """Running total per tenant/period/metric; row-locked during the quota check."""

    __tablename__ = "usage_counters"
    __table_args__ = (
        CheckConstraint("used >= 0", name="used_non_negative"),
        CheckConstraint(METRIC_CHECK, name="metric_known"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    metric: Mapped[str] = mapped_column(Text, primary_key=True)
    used: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))


class Subscription(Base):
    """Mirror of the tenant's Stripe subscription. Changed only by verified webhook events."""

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    stripe_subscription_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Stripe event.created of the newest event applied; older events are skipped.
    last_event_created: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StripeEvent(Base):
    """Received webhook events. The Stripe event id is the primary key: that is the dedupe."""

    __tablename__ = "stripe_events"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'done', 'dead', 'ignored')", name="status_known"),
        Index("ix_stripe_events_status_next_attempt_at", "status", "next_attempt_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageAlert(Base):
    """One row per (tenant, period, metric, threshold, limit): the dedupe and the job queue.

    Inserted in the metering transaction that crosses the threshold; delivered by the
    worker with retries. Spec: docs/background-jobs.md#usage-alerts.
    """

    __tablename__ = "usage_alerts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "period_start",
            "metric",
            "threshold_percent",
            "quota_limit",
            name="uq_usage_alerts_once",
        ),
        CheckConstraint(METRIC_CHECK, name="metric_known"),
        CheckConstraint("threshold_percent IN (80, 100)", name="threshold_known"),
        CheckConstraint("status IN ('pending', 'sent', 'dead')", name="status_known"),
        CheckConstraint("quota_limit > 0 AND used_at_trigger >= 0", name="amounts_valid"),
        Index("ix_usage_alerts_status_next_attempt_at", "status", "next_attempt_at"),
        Index("ix_usage_alerts_tenant_period", "tenant_id", "period_start"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    threshold_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_id: Mapped[str] = mapped_column(Text, nullable=False)
    quota_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_at_trigger: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    delivery: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
