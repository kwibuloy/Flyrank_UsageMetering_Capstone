"""Liveness check for GET /health."""

from sqlalchemy.orm import Session

from app.repositories import health as health_repo


def database_ok(session: Session) -> bool:
    try:
        health_repo.ping(session)
    except Exception:
        return False
    return True
