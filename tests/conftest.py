import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.database import Base


@pytest_asyncio.fixture
async def db_session():
    """Provides an isolated in-memory SQLite database session for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture(autouse=True)
def _isolated_instagram_session(tmp_path, monkeypatch):
    """Tests never pace requests or read the real Instagram session."""
    monkeypatch.setattr("src.config.settings.IG_MIN_INTERVAL_SEC", 0)
    monkeypatch.setattr("src.config.settings.IG_SESSION_FILE", str(tmp_path / "ig_session.txt"))
    monkeypatch.setattr("src.config.settings.YTDLP_COOKIES_FILE", "")
