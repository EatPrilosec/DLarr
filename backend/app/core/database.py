from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from sqlalchemy import event
from backend.app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)

# Enable WAL mode and foreign key constraints for SQLite
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    async with engine.begin() as conn:
        # Import models to ensure they are registered with Base.metadata
        import backend.app.models.show  # noqa: F401
        import backend.app.models.setting  # noqa: F401
        import backend.app.models.job  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)

        # Automatic SQLite schema migration for columns/indexes added over time
        def migrate_schema(sync_conn):
            cursor = sync_conn.connection.cursor()
            try:
                cursor.execute("PRAGMA table_info(jobs)")
                cols = [row[1] for row in cursor.fetchall()]
                if "payload" not in cols:
                    cursor.execute("ALTER TABLE jobs ADD COLUMN payload TEXT")

                # Deduplicate and ensure unique index on (episode_id, source_name)
                cursor.execute("""
                    DELETE FROM episode_source_metadata
                    WHERE id NOT IN (
                        SELECT MAX(id)
                        FROM episode_source_metadata
                        GROUP BY episode_id, source_name
                    )
                """)
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uix_episode_source ON episode_source_metadata (episode_id, source_name)")
            except Exception:
                pass
            finally:
                cursor.close()

        await conn.run_sync(migrate_schema)
