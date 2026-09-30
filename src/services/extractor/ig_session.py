"""
Instagram session management: storage of the cookies jar uploaded from the UI,
validity checks against Instagram and pacing of requests from the worker.

The session saved from the UI lives in the shared data volume (IG_SESSION_FILE),
so both web and worker containers see it; the read-only YTDLP_COOKIES_FILE mount
is only a fallback.
"""

import json
import logging
import os
import random
import shutil
import tempfile
import time
from datetime import UTC, datetime
from http.cookiejar import MozillaCookieJar

import yt_dlp
from yt_dlp.networking import Request

from src.config import settings

logger = logging.getLogger(__name__)

# The web endpoint: i.instagram.com/.../current_user answers 403 even to live sessions
_CURRENT_USER_URL = "https://www.instagram.com/api/v1/accounts/edit/web_form_data/"
_IG_WEB_APP_ID = "936619743392459"
_NEXT_SLOT_KEY = "skycoach:instagram:next_slot"
# Next free Instagram slot when running without Redis (single-process memory queue)
_memory_next_slot = 0.0
# Session cookie lifetime Instagram itself sets on login
_SESSION_TTL_SEC = 365 * 24 * 3600


class InvalidCookiesError(ValueError):
    """Raised when uploaded data contains no usable Instagram session."""


def _status_path() -> str:
    return os.path.splitext(settings.IG_SESSION_FILE)[0] + "_status.json"


def active_cookies_file() -> str | None:
    """Session saved from the UI wins over the mounted cookies file."""
    for path in (settings.IG_SESSION_FILE, settings.YTDLP_COOKIES_FILE):
        if path and os.path.isfile(path):
            return path
    return None


def build_cookies_text(cookies_txt: str | None = None, sessionid: str | None = None) -> str:
    """
    Produces a Netscape cookies.txt holding only instagram.com cookies, from either
    a full browser export or a bare sessionid value.
    """
    lines = ["# Netscape HTTP Cookie File"]
    if cookies_txt:
        found = False
        for line in cookies_txt.splitlines():
            fields = line.split("\t")
            if line.startswith("#") or len(fields) < 7 or "instagram.com" not in fields[0]:
                continue
            lines.append(line.strip("\r"))
            found = found or fields[5] == "sessionid"
        if not found:
            raise InvalidCookiesError("В файле нет cookie sessionid для instagram.com.")
    elif sessionid:
        value = sessionid.strip().strip('"')
        if not value or any(c in value for c in "\t\r\n ;"):
            raise InvalidCookiesError("Некорректное значение sessionid.")
        expires = int(time.time()) + _SESSION_TTL_SEC
        lines.append(f".instagram.com\tTRUE\t/\tTRUE\t{expires}\tsessionid\t{value}")
        # sessionid starts with the numeric user id ("123%3A...")
        user_id = value.replace("%3A", ":").split(":", 1)[0]
        if user_id.isdigit():
            lines.append(f".instagram.com\tTRUE\t/\tTRUE\t{expires}\tds_user_id\t{user_id}")
    else:
        raise InvalidCookiesError("Передайте файл cookies.txt или значение sessionid.")
    return "\n".join(lines) + "\n"


def check_cookies(cookies_path: str) -> tuple[bool, str | None, str]:
    """
    Asks Instagram who is logged in. Returns (valid, username, message).
    Works on a copy: yt-dlp writes the jar back and must not touch the original.
    """
    tmp_dir = tempfile.mkdtemp(prefix="skycoach_igcheck_")
    try:
        jar_copy = os.path.join(tmp_dir, "cookies.txt")
        shutil.copyfile(cookies_path, jar_copy)
        opts = {"cookiefile": jar_copy, "quiet": True, "no_warnings": True, "socket_timeout": 20}
        with yt_dlp.YoutubeDL(opts) as ydl:
            request = Request(
                _CURRENT_USER_URL,
                headers={
                    "X-IG-App-ID": _IG_WEB_APP_ID,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://www.instagram.com/",
                },
            )
            with ydl.urlopen(request) as response:
                payload = json.loads(response.read())
        username = (payload.get("form_data") or {}).get("username")
        if not username:
            return False, None, "Instagram не подтвердил вход по этой сессии."
        return True, username, "Сессия активна."
    except yt_dlp.networking.exceptions.HTTPError as e:
        logger.info("Instagram session check rejected: %s", e)
        return (
            False,
            None,
            f"Instagram отклонил сессию (HTTP {e.status}). Войдите заново и обновите.",
        )
    except json.JSONDecodeError:
        return False, None, "Instagram перенаправил на страницу входа — сессия недействительна."
    except Exception as e:  # noqa: BLE001
        logger.warning("Instagram session check failed: %s", e)
        return False, None, f"Не удалось проверить сессию: {str(e)[:120]}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _write_status(valid: bool, username: str | None, message: str) -> dict:
    status = {
        "valid": valid,
        "username": username,
        "message": message,
        "checked_at": datetime.now(UTC).isoformat(),
    }
    os.makedirs(os.path.dirname(_status_path()) or ".", exist_ok=True)
    with open(_status_path(), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False)
    return status


def _read_status() -> dict | None:
    try:
        with open(_status_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _cookies_expiry(path: str) -> str | None:
    try:
        jar = MozillaCookieJar()
        jar.load(path, ignore_discard=True, ignore_expires=True)
    except (OSError, ValueError):
        return None
    for cookie in jar:
        if cookie.name == "sessionid" and cookie.expires:
            return datetime.fromtimestamp(cookie.expires, UTC).isoformat()
    return None


def get_status() -> dict:
    """
    Current session state for the UI. A status older than the cookies file is
    dropped: the file was replaced by hand and hasn't been checked yet.
    """
    path = active_cookies_file()
    base = {
        "configured": path is not None,
        "source": None,
        "expires_at": None,
        "valid": None,
        "username": None,
        "message": "Сессия не задана — загрузите cookies.txt или sessionid.",
        "checked_at": None,
    }
    if not path:
        return base
    base["source"] = "ui" if path == settings.IG_SESSION_FILE else "file"
    base["expires_at"] = _cookies_expiry(path)
    base["message"] = "Сессия ещё не проверялась."
    status = _read_status()
    if status:
        checked = datetime.fromisoformat(status["checked_at"]).timestamp()
        if checked >= os.path.getmtime(path):
            base.update(status)
    return base


def is_known_invalid() -> bool:
    """True when the last check/attempt proved the current session dead."""
    return get_status()["valid"] is False


def save_session(cookies_text: str) -> dict:
    """Validates the jar first and only then replaces the stored session."""
    tmp_dir = tempfile.mkdtemp(prefix="skycoach_ignew_")
    try:
        candidate = os.path.join(tmp_dir, "cookies.txt")
        with open(candidate, "w", encoding="utf-8") as f:
            f.write(cookies_text)
        valid, username, message = check_cookies(candidate)
        if not valid:
            return {**get_status(), "saved": False, "error": message}
        os.makedirs(os.path.dirname(settings.IG_SESSION_FILE) or ".", exist_ok=True)
        shutil.copyfile(candidate, settings.IG_SESSION_FILE)
        os.chmod(settings.IG_SESSION_FILE, 0o600)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    _write_status(True, username, message)
    logger.info("Instagram session updated from UI (user=%s)", username)
    return {**get_status(), "saved": True, "error": None}


def recheck() -> dict:
    path = active_cookies_file()
    if not path:
        return get_status()
    _write_status(*check_cookies(path))
    return get_status()


def mark_invalid(message: str) -> None:
    """Called by the worker when Instagram refused a download with this session."""
    if active_cookies_file():
        _write_status(False, None, message)


def wait_for_instagram_slot() -> None:
    """
    Spaces Instagram requests out by IG_MIN_INTERVAL_SEC plus random jitter so a
    batch doesn't look like a burst from a bot. The next free slot is kept in
    Redis, so the pause holds across jobs and worker restarts.
    """
    interval = settings.IG_MIN_INTERVAL_SEC
    if interval <= 0:
        return
    global _memory_next_slot
    now = time.time()
    gap = interval + random.uniform(0, settings.IG_JITTER_SEC)
    if settings.QUEUE_BACKEND == "memory":
        # One task at a time in this process, so a module variable is enough
        delay = max(0.0, _memory_next_slot - now)
        _memory_next_slot = max(now, _memory_next_slot) + gap
    else:
        try:
            from src.workers.queue import get_redis_connection

            conn = get_redis_connection()
            next_slot = float(conn.get(_NEXT_SLOT_KEY) or 0)
            delay = max(0.0, next_slot - now)
            conn.set(_NEXT_SLOT_KEY, max(now, next_slot) + gap, ex=int(gap) + 3600)
        except Exception as e:  # noqa: BLE001
            logger.warning("Instagram pacing unavailable (Redis): %s", e)
            return
    if delay > 0:
        logger.info("Pausing %.1fs before next Instagram request", delay)
        time.sleep(delay)
