from sqlalchemy import text
from sqlalchemy.orm import Session


def ping(session: Session) -> None:
    """Round-trip to the database; raises if it is unreachable."""
    session.execute(text("SELECT 1"))
