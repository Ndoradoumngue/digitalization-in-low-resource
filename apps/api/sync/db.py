"""Database access for the sync processes (worker, join) - the API's own
engine setup and schema migration, without the ingestion pipeline."""

from __future__ import annotations


async def open_engine(init_schema: bool = True):
    """Create the shared engine (every connection stamped with NODE_ID, like
    the API's) and run the idempotent schema setup."""
    import ingest_router
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(
        ingest_router.DATABASE_URL,
        pool_size=4,
        max_overflow=4,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    event.listen(engine.sync_engine, "connect", ingest_router._set_node_id_on_connect)
    ingest_router.engine = engine
    ingest_router.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    if init_schema:
        await ingest_router._init_db()
    return engine
