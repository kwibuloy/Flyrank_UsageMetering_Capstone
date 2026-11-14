"""Engine and session factory. One session per request (see app/api/deps.py)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings

_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
