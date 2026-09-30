import os
from pathlib import Path

from src.config import settings
from src.database import normalize_database_url
from src.services.extractor import ig_session, ig_session_store


def test_neon_url_is_converted_for_asyncpg():
    url, args = normalize_database_url(
        "postgresql://user:pw@ep-x.eu.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    )
    assert url == "postgresql+asyncpg://user:pw@ep-x.eu.aws.neon.tech/neondb"
    assert args == {"ssl": True}


def test_sqlite_url_is_left_alone():
    url, args = normalize_database_url("sqlite+aiosqlite:///./data/skycoach.db")
    assert url == "sqlite+aiosqlite:///./data/skycoach.db"
    assert args == {"timeout": 30}


async def test_instagram_session_survives_disk_wipe(db_session):
    session_file = Path(settings.IG_SESSION_FILE)
    session_file.write_text(
        "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tcookie-line\tv\n"
    )
    ig_session._write_status(True, "tester", "Сессия активна.")
    await ig_session_store.persist(db_session)

    os.remove(settings.IG_SESSION_FILE)
    os.remove(ig_session._status_path())

    assert await ig_session_store.restore(db_session) is True
    assert "cookie-line" in session_file.read_text()
    status = ig_session.get_status()
    assert status["valid"] is True
    assert status["username"] == "tester"


async def test_restore_keeps_existing_session_file(db_session):
    session_file = Path(settings.IG_SESSION_FILE)
    session_file.write_text("old\n")
    await ig_session_store.persist(db_session)
    session_file.write_text("newer\n")

    assert await ig_session_store.restore(db_session) is False
    assert session_file.read_text() == "newer\n"


async def test_restore_without_saved_session(db_session):
    assert await ig_session_store.restore(db_session) is False
    assert not os.path.exists(settings.IG_SESSION_FILE)
