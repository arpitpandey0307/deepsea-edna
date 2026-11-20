"""
Database engine, sessions and migrations
"""
import logging
from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def normalize_database_url(url: str) -> str:
    """Use the psycopg 3 driver for plain postgres:// URLs"""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = normalize_database_url(settings.DATABASE_URL)
_is_sqlite = DATABASE_URL.startswith("sqlite")

if _is_sqlite:
    db_file = DATABASE_URL.split("///", 1)[-1]
    if db_file and db_file != ":memory:":
        Path(db_file).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
    return config


def run_migrations():
    """Bring the schema up to date (safe to run on every start)"""
    logger.info("Applying database migrations...")
    command.upgrade(alembic_config(), "head")
    logger.info("Database schema is up to date")
