"""
Database migration module for FastAPI lifespan.

This module provides database migration functionality that can be executed
during FastAPI application startup to ensure the database schema is up-to-date.
"""

import logging
from pathlib import Path

from sqlalchemy.exc import OperationalError

from alembic import command  # type: ignore[attr-defined]
from alembic.config import Config
from workflow.extensions.middleware.getters import get_cache_service

# Migration constants
INIT_VERSION = "b13356244aea"

# MySQL error codes
MYSQL_ERROR_SELECT_DENIED = 1142
MYSQL_ERROR_ACCESS_DENIED = 1227
MYSQL_ERROR_EXECUTE_DENIED = 1370
MYSQL_ERROR_TABLE_EXISTS = 1050

MIGRATION_LOCK_KEY = "workflow_database_migration_lock"
MIGRATION_LOCK_TTL_SECONDS = 900.0
MIGRATION_LOCK_WAIT_SECONDS = 930.0
MIGRATION_LOCK_POLL_SECONDS = 0.25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s:%(funcName)s:%(lineno)d | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def run_database_migration() -> None:
    """
    Execute database migration (using Redis distributed lock).

    This function runs database migrations to ensure the database schema is up-to-date.
    Uses Redis distributed lock to prevent multiple instances from running migrations simultaneously.
    Database URL is configured from environment variables in alembic/env.py.
    """
    workflow_dir = Path(__file__).parent.parent.parent.parent
    alembic_dir = workflow_dir / "alembic"
    alembic_ini = alembic_dir / "alembic.ini"
    if not alembic_ini.exists():
        logging.error(f"alembic.ini not found: {alembic_ini}")
        raise FileNotFoundError(f"alembic.ini not found: {alembic_ini}")

    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(alembic_dir))

    cache_service = get_cache_service()
    migration_lock = cache_service.distributed_lock(
        MIGRATION_LOCK_KEY,
        timeout=MIGRATION_LOCK_TTL_SECONDS,
        blocking_timeout=MIGRATION_LOCK_WAIT_SECONDS,
        sleep=MIGRATION_LOCK_POLL_SECONDS,
    )
    if not migration_lock.acquire():
        raise TimeoutError("timed out waiting for Workflow database migration lock")
    try:
        try:
            command.upgrade(config, "head")
        except OperationalError as error:
            db_error_code = getattr(error.orig, "args", [None])[0]
            if db_error_code in (
                MYSQL_ERROR_SELECT_DENIED,
                MYSQL_ERROR_ACCESS_DENIED,
                MYSQL_ERROR_EXECUTE_DENIED,
            ):
                logging.error(
                    "Database migration permissions are insufficient; refusing to start"
                )
                raise
            if db_error_code == MYSQL_ERROR_TABLE_EXISTS:
                logging.warning("Detected legacy database, stamping to init version...")
                command.stamp(config, INIT_VERSION)
                command.upgrade(config, "head")
                return
            logging.error("Database migration failed", exc_info=True)
            raise
    finally:
        # All replicas run upgrade after acquiring the lock. Losers therefore wait
        # for the winner instead of touching a partially migrated fresh schema.
        # redis-py releases with an atomic token compare-and-delete. If this
        # owner lost the lock, release raises rather than deleting a newer lock.
        migration_lock.release()
