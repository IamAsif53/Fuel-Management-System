"""Asynchronous database engine, session management, and migration execution."""
from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import AsyncGenerator
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql import text

from app.config import get_settings

log = structlog.get_logger()

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def resolve_database_url(url: str | None = None) -> str:
    """Resolve database URL, handling Docker network vs host localhost fallback."""
    if not url:
        url = os.environ.get("DATABASE_URL")
    if not url:
        url = get_settings().database_url

    # Normalize to postgresql+asyncpg://
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # If the URL targets 'timescaledb:5432' (docker internal DNS) and we are on host,
    # fallback to localhost:15432 (or PG_HOST_PORT) if timescaledb is not resolvable.
    if "@timescaledb:5432/" in url:
        try:
            socket.gethostbyname("timescaledb")
        except socket.gaierror:
            host_port = os.environ.get("PG_HOST_PORT", "15432")
            url = url.replace("@timescaledb:5432/", f"@localhost:{host_port}/")

    return url


def get_engine(url: str | None = None) -> AsyncEngine:
    """Get or create singleton AsyncEngine."""
    global _engine
    if _engine is None:
        db_url = resolve_database_url(url)
        _engine = create_async_engine(
            db_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    return _engine


def get_sessionmaker(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    """Get or create singleton async_sessionmaker."""
    global _sessionmaker
    if _sessionmaker is None:
        eng = engine or get_engine()
        _sessionmaker = async_sessionmaker(
            bind=eng,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for obtaining an AsyncSession."""
    sm = get_sessionmaker()
    async with sm() as session:
        yield session


async def check_db_health(engine: AsyncEngine | None = None) -> bool:
    """Check whether database is reachable and responsive."""
    eng = engine or get_engine()
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.warning("db.health.failed", error=str(exc))
        return False


def _run_alembic_upgrade_sync(alembic_cfg: Config, revision: str = "head") -> None:
    """Synchronous worker function to invoke alembic command."""
    command.upgrade(alembic_cfg, revision)


async def run_migrations(
    url: str | None = None,
    max_retries: int = 15,
    retry_interval: float = 2.0,
) -> None:
    """Run alembic upgrade head on engine startup, retrying until DB becomes available."""
    db_url = resolve_database_url(url)
    engine_dir = Path(__file__).resolve().parents[2]
    ini_path = engine_dir / "alembic.ini"

    if not ini_path.exists():
        raise FileNotFoundError(f"Alembic configuration not found at {ini_path}")

    alembic_cfg = Config(str(ini_path))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    alembic_cfg.set_main_option("script_location", str(engine_dir / "migrations"))

    # Mask password for logging
    masked_url = db_url.split("@")[-1] if "@" in db_url else db_url
    log.info("db.migration.start", database=masked_url)

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            # Run migration in a separate thread so asyncio.run in env.py works cleanly
            await asyncio.to_thread(_run_alembic_upgrade_sync, alembic_cfg, "head")
            log.info("db.migration.success", revision="head", attempts=attempt)
            return
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                log.warning(
                    "db.migration.retry",
                    attempt=attempt,
                    max_retries=max_retries,
                    error=str(exc),
                    delay_s=retry_interval,
                )
                await asyncio.sleep(retry_interval)
            else:
                log.error("db.migration.failed", attempts=max_retries, error=str(exc))

    if last_error is not None:
        raise last_error
