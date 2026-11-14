"""Background worker: applies queued Stripe events off the request path.

Retries with exponential backoff; after MAX_ATTEMPTS the event is marked dead and an
ALERT is logged. Spec: docs/background-jobs.md. Run: python -m app.jobs.worker
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import SessionLocal
from app.repositories import stripe_events as events_repo
from app.services import billing, stripe_sync

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


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logger.info("worker started poll=%ss", settings.worker_poll_seconds)
    while True:
        try:
            if run_once() is None:
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
