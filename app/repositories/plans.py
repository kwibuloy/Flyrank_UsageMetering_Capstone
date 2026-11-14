from sqlalchemy.orm import Session

from app.db.models import Plan


def get(session: Session, plan_id: str) -> Plan | None:
    """Plans are global (not tenant-owned), so no tenant_id scope applies."""
    return session.get(Plan, plan_id)
