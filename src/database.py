from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from src.config import settings

# libpq-only query params that asyncpg rejects; TLS is passed via connect_args instead
_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding"}


def normalize_database_url(url: str) -> tuple[str, dict]:
    """
    Accepts a provider connection string as-is (e.g. Neon's
    postgresql://...?sslmode=require) and returns (async URL, connect_args).
    """
    parts = urlsplit(url)
    if parts.scheme.startswith("sqlite"):
        return url, {"timeout": 30}
    if parts.scheme not in ("postgres", "postgresql", "postgresql+asyncpg"):
        return url, {}
    query = dict(parse_qsl(parts.query))
    sslmode = query.get("sslmode")
    kept = {k: v for k, v in query.items() if k not in _LIBPQ_ONLY_PARAMS}
    async_url = urlunsplit(parts._replace(scheme="postgresql+asyncpg", query=urlencode(kept)))
    connect_args = {"ssl": True} if sslmode and sslmode != "disable" else {}
    return async_url, connect_args


database_url, connect_args = normalize_database_url(settings.DATABASE_URL)
is_sqlite = database_url.startswith("sqlite")

# Configure async engine
# For SQLite, we pass timeout and enable WAL mode on connect.
# For PostgreSQL the web app keeps a small pool (a fresh TLS connection per
# request costs seconds on a remote database); pre-ping and recycle cover the
# database suspending idle connections.
engine = create_async_engine(
    database_url,
    echo=settings.DEBUG,
    connect_args=connect_args,
    future=True,
    **({} if is_sqlite else {"pool_size": 3, "pool_pre_ping": True, "pool_recycle": 240}),
)

# The worker runs every task in its own event loop and asyncpg connections
# can't be shared across loops, so it gets an engine without pooling.
worker_engine = (
    engine
    if is_sqlite
    else create_async_engine(
        database_url, echo=settings.DEBUG, connect_args=connect_args, poolclass=NullPool
    )
)

if is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        """Enable SQLite WAL mode and busy timeout for safe concurrent access."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


WorkerSessionLocal = async_sessionmaker(
    bind=worker_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy domain models."""


async def get_db():
    """FastAPI dependency yielding an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Initializes tables in database."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
