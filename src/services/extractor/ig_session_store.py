"""
Mirrors the Instagram session saved from the UI into the database, so it
survives hosts with an ephemeral disk (the session file is restored on startup).
"""

import logging
import os
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models import AppState
from src.services.extractor import ig_session

logger = logging.getLogger(__name__)

_COOKIES_KEY = "ig_session_cookies"
_STATUS_KEY = "ig_session_status"


def _files() -> dict[str, str]:
    return {_COOKIES_KEY: settings.IG_SESSION_FILE, _STATUS_KEY: ig_session._status_path()}


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _write(path: str, value: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(value)
    os.chmod(path, 0o600)


async def persist(session: AsyncSession) -> None:
    """Copies the session files saved from the UI into the database."""
    for key, path in _files().items():
        value = _read(path)
        if value is None:
            continue
        await session.merge(AppState(key=key, value=value))
    await session.commit()


async def restore(session: AsyncSession) -> bool:
    """Recreates missing session files from the database. Returns True if restored."""
    if os.path.isfile(settings.IG_SESSION_FILE):
        return False
    restored = False
    # Cookies first: a status older than the cookies file is treated as stale
    for key, path in _files().items():
        row = await session.get(AppState, key)
        if row is None:
            continue
        _write(path, row.value)
        restored = restored or key == _COOKIES_KEY
    if restored:
        # get_status() drops a status older than the cookies file; the restored
        # file is not a new session, so backdate it to the last check
        status = ig_session._read_status()
        if status:
            checked = datetime.fromisoformat(status["checked_at"]).timestamp()
            os.utime(settings.IG_SESSION_FILE, (checked, checked))
        logger.info("Instagram session restored from the database")
    return restored
