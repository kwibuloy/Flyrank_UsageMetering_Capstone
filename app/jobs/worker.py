"""Background worker: Stripe events, usage alerts and Stripe usage reports (three queues).

Both queues retry with exponential backoff; after MAX_ATTEMPTS a job is marked dead and an
ALERT is logged. Spec: docs/background-jobs.md. Run: python -m app.jobs.worker
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import SessionLocal
from app.repositories import alerts as alerts_repo
from app.repositories import stripe_events as events_repo
from app.repositories import usage_reports as reports_repo
from app.services import alert_delivery, alerts, billing, stripe_sync, usage_billing

logger = logging.getLogger("app.jobs.worker")

MAX_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 300


def backoff_seconds(attempts: int) -> int:
    return min(2**attempts, MAX_BACKOFF_SECONDS)


def run_once(
    session_factory: Callable[[], Session] = SessionLocal,
    fetch_subscription: stripe_sync.FetchSubscription | None = None,
    now: datetime | None = None,
) -> str | None:
    """Process at most one due event. Returns "done", "retry", "dead" or None (queue empty)."""
    fetch = fetch_subscription or billing.fetch_subscription
    now = now or datetime.now(UTC)

    with session_factory() as session:
        event = events_repo.claim_next(session, now)
        if event is None:
            return None
        event_id, event_type, attempts = event.id, event.type, event.attempts
        try:
            # State change and "done" commit together: the event is applied exactly once.
            outcome = stripe_sync.apply(session, event.payload, fetch)
            events_repo.mark_done(session, event_id, outcome)
            session.commit()
        except Exception as exc:
            session.rollback()
            error = f"{type(exc).__name__}: {exc}"[:500]
        else:
            logger.info("stripe_event_done id=%s type=%s outcome=%s", event_id, event_type, outcome)
            return "done"

    attempts += 1
    dead = attempts >= MAX_ATTEMPTS
    with session_factory() as session:
        events_repo.record_failure(
            session,
            event_id,
            attempts=attempts,
            error=error,
            next_attempt_at=now + timedelta(seconds=backoff_seconds(attempts)),
            dead=dead,
        )
        session.commit()

    if dead:
        logger.error(
            "ALERT stripe_event_dead id=%s type=%s attempts=%s error=%s",
            event_id,
            event_type,
            attempts,
            error,
        )
        return "dead"
    logger.warning(
        "stripe_event_retry id=%s type=%s attempts=%s error=%s",
        event_id,
        event_type,
        attempts,
        error,
    )
    return "retry"


def run_alerts_once(
    session_factory: Callable[[], Session] = SessionLocal,
    deliver: alert_delivery.Deliver | None = None,
    now: datetime | None = None,
) -> str | None:
    """Deliver at most one due usage alert. Returns "sent", "retry", "dead" or None.

    Delivery happens while the row is locked and "sent" commits right after, so a crash
    between the two can re-send: delivery is at-least-once and carries `alert_id`.
    """
    send = deliver or alert_delivery.deliver
    now = now or datetime.now(UTC)

    with session_factory() as session:
        claimed = alerts_repo.claim_next(session, now)
        if claimed is None:
            return None
        alert, tenant_name = claimed
        alert_id, attempts = alert.id, alert.attempts
        try:
            delivery = send(alerts.payload(alert, tenant_name))
            alerts_repo.mark_sent(session, alert_id, delivery)
            session.commit()
        except Exception as exc:
            session.rollback()
            error = f"{type(exc).__name__}: {exc}"[:500]
        else:
            logger.info("usage_alert_sent id=%s via=%s", alert_id, delivery)
            return "sent"

    attempts += 1
    dead = attempts >= MAX_ATTEMPTS
    with session_factory() as session:
        alerts_repo.record_failure(
            session,
            alert_id,
            attempts=attempts,
            error=error,
            next_attempt_at=now + timedelta(seconds=backoff_seconds(attempts)),
            dead=dead,
        )
        session.commit()

    if dead:
        logger.error("ALERT usage_alert_dead id=%s attempts=%s error=%s", alert_id, attempts, error)
        return "dead"
    logger.warning("usage_alert_retry id=%s attempts=%s error=%s", alert_id, attempts, error)
    return "retry"


def run_usage_reports_once(
    session_factory: Callable[[], Session] = SessionLocal,
    send: Callable[[Any], None] | None = None,
    now: datetime | None = None,
) -> str | None:
    """Send at most one usage report to Stripe. Returns "sent", "retry", "dead" or None.

    Safe to retry: Stripe de-duplicates on the report's identifier.
    """
    send_report = send or usage_billing.send
    now = now or datetime.now(UTC)

    with session_factory() as session:
        report = reports_repo.claim_next(session, now)
        if report is None:
            return None
        report_id, attempts = report.id, report.attempts
        try:
            send_report(report)
            reports_repo.mark_sent(session, report_id)
            session.commit()
        except Exception as exc:
            session.rollback()
            error = f"{type(exc).__name__}: {exc}"[:500]
        else:
            logger.info("usage_report_sent id=%s", report_id)
            return "sent"

    attempts += 1
    dead = attempts >= MAX_ATTEMPTS
    with session_factory() as session:
        reports_repo.record_failure(
            session,
            report_id,
            attempts=attempts,
            error=error,
            next_attempt_at=now + timedelta(seconds=backoff_seconds(attempts)),
            dead=dead,
        )
        session.commit()

    if dead:
        logger.error(
            "ALERT usage_report_dead id=%s attempts=%s error=%s", report_id, attempts, error
        )
        return "dead"
    logger.warning("usage_report_retry id=%s attempts=%s error=%s", report_id, attempts, error)
    return "retry"


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logger.info("worker started poll=%ss", settings.worker_poll_seconds)
    while True:
        try:
            results = (run_once(), run_alerts_once(), run_usage_reports_once())
            if all(result is None for result in results):
                time.sleep(settings.worker_poll_seconds)
        except KeyboardInterrupt:
            logger.info("worker stopped")
            return
        except Exception:
            # e.g. database not migrated yet on first boot; keep the loop alive.
            logger.exception("worker_loop_error")
            time.sleep(2)


if __name__ == "__main__":
    main()
