import json
import logging
import os
import shutil
import tempfile
import time

import certifi
import yt_dlp
from yt_dlp.networking import Request

from src.config import settings
from src.services.extractor import ig_session
from src.services.extractor.exceptions import (
    AuthRequiredError,
    ExtractionTimeoutError,
    ExtractorError,
    PrivateAccountError,
    ReelTooLongError,
    VideoNotFoundError,
)

logger = logging.getLogger(__name__)

# Transient TLS drops seen on Instagram; yt-dlp's own retries don't cover them
_TRANSIENT_ERRORS = ("unexpected_eof_while_reading", "eof occurred in violation of protocol")
_MAX_ATTEMPTS = 3

_IG_SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_IG_MEDIA_INFO_URL = "https://i.instagram.com/api/v1/media/{pk}/info/"
_IG_WEB_APP_ID = "936619743392459"


class YtDlpExtractor:
    """
    Service responsible for fetching video metadata and downloading media streams via yt-dlp.
    """

    def __init__(self, max_duration_sec: int = 180):
        self.max_duration_sec = max_duration_sec

    def _get_ydl_options(self, target_dir: str | None = None, url: str = "") -> dict:
        opts = {
            # YouTube often serves no progressive (audio+video) stream, only DASH
            # tracks, so fall back to merging best video + audio via ffmpeg.
            "format": "b[height<=720]/bv*[height<=720]+ba/bv*+ba/b",
            "merge_output_format": "mp4",
            "no_color": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 20,
            # Instagram intermittently drops TLS connections (SSL EOF); retry them
            "retries": 5,
            "extractor_retries": 3,
            "fragment_retries": 5,
            "nocheckcertificate": True,
            "ca_certs": certifi.where(),
        }
        # Only Instagram needs a logged-in session. With cookies YouTube switches to
        # clients that expose no downloadable formats, so it must stay anonymous.
        cookies_file = ig_session.active_cookies_file()
        if "instagram.com" in url and cookies_file:
            # yt-dlp writes the jar back on exit; when Instagram drops the session it
            # would overwrite the source file without sessionid. Hand it a copy.
            cookies_dir = target_dir or tempfile.mkdtemp(prefix="skycoach_cookies_")
            cookies_copy = os.path.join(cookies_dir, "cookies.txt")
            shutil.copyfile(cookies_file, cookies_copy)
            opts["cookiefile"] = cookies_copy
        # Datacenter IPs get YouTube's bot check; an optional proxy works around it.
        # Anonymous traffic only, so even an untrusted free proxy sees no cookies.
        if settings.YOUTUBE_PROXY and ("youtube.com" in url or "youtu.be" in url):
            opts["proxy"] = settings.YOUTUBE_PROXY
        if target_dir:
            opts["outtmpl"] = os.path.join(target_dir, "%(id)s.%(ext)s")
        return opts

    def extract_info_and_download(self, url: str) -> tuple[dict, str, bool]:
        """
        Extracts metadata and downloads the video file to a temporary location.
        Returns:
            tuple of (raw_metrics_dict, video_file_path, has_audio)
        Raises:
            PrivateAccountError, VideoNotFoundError, ReelTooLongError,
            ExtractionTimeoutError, ExtractorError
        """
        is_instagram = "instagram.com" in url
        if is_instagram:
            # Don't burn requests on a session Instagram already rejected
            if ig_session.is_known_invalid():
                raise AuthRequiredError()
            ig_session.wait_for_instagram_slot()

        temp_dir = tempfile.mkdtemp(prefix="skycoach_reel_")
        opts = self._get_ydl_options(target_dir=temp_dir, url=url)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = self._extract_with_retries(ydl, url)
                if not info:
                    raise VideoNotFoundError()

                # Check duration limit
                duration = info.get("duration")
                if duration and duration > self.max_duration_sec:
                    raise ReelTooLongError(duration, max_allowed=self.max_duration_sec)

                # Parse metrics with strict preservation of None (NULL)
                views = info.get("view_count")
                if views is None and info.get("extractor_key") == "Instagram":
                    views = self._fetch_instagram_play_count(ydl, info.get("id"))
                raw_metrics = {
                    "views": views,
                    "likes": info.get("like_count"),
                    "comments": info.get("comment_count"),
                    "author": info.get("uploader") or info.get("channel"),
                    "upload_date": self._format_date(info.get("upload_date")),
                }

                # Resolve downloaded video file path
                filename = ydl.prepare_filename(info)
                if not os.path.exists(filename):
                    # In case of extension remuxing (e.g. webm -> mp4)
                    candidates = [
                        os.path.join(temp_dir, f)
                        for f in os.listdir(temp_dir)
                        if not f.endswith(".part") and not f.endswith(".ytdl")
                    ]
                    if candidates:
                        filename = candidates[0]
                    else:
                        raise ExtractorError("Файл ролика не был сохранен на диск.")

                # Check whether video contains an active audio track
                acodec = info.get("acodec")
                has_audio = bool(acodec and acodec != "none")

                return raw_metrics, filename, has_audio

        except yt_dlp.utils.DownloadError as e:
            err_msg = str(e).lower()
            # With a valid session Instagram answers 400 for removed/hidden posts
            if "video info extraction failed: http error 400" in err_msg:
                raise VideoNotFoundError() from e
            elif any(
                k in err_msg
                for k in ["empty media response", "not a bot", "sign in to confirm", "logged-in"]
            ) or (is_instagram and "failed to parse json" in err_msg):
                # A dead session makes Instagram answer with a login page instead of JSON
                if is_instagram:
                    ig_session.mark_invalid(
                        "Instagram отклонил загрузку — сессия истекла или заблокирована."
                    )
                raise AuthRequiredError(instagram=is_instagram) from e
            elif any(
                k in err_msg for k in ["private", "login", "requires authentication", "restricted"]
            ):
                raise PrivateAccountError() from e
            elif any(
                k in err_msg
                for k in ["not found", "404", "deleted", "unavailable", "does not exist"]
            ):
                raise VideoNotFoundError() from e
            elif any(k in err_msg for k in ["timed out", "timeout", "handshake"]):
                raise ExtractionTimeoutError() from e
            else:
                raise ExtractorError(f"Ошибка загрузки ролика: {str(e)[:150]}") from e

    @staticmethod
    def _extract_with_retries(ydl: yt_dlp.YoutubeDL, url: str) -> dict | None:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return ydl.extract_info(url, download=True)
            except yt_dlp.utils.DownloadError as e:
                err_msg = str(e).lower()
                if attempt == _MAX_ATTEMPTS or not any(k in err_msg for k in _TRANSIENT_ERRORS):
                    raise
                logger.info("Transient network error for %s (attempt %d), retrying", url, attempt)
                time.sleep(2 * attempt)
        return None

    @staticmethod
    def _fetch_instagram_play_count(ydl: yt_dlp.YoutubeDL, shortcode: str | None) -> int | None:
        """
        yt-dlp doesn't expose Instagram reel plays, but the media info API that it
        uses does (play_count). Requires the logged-in session from cookies;
        returns None on any failure so views stay N/A rather than failing the task.
        """
        if not shortcode:
            return None
        try:
            pk = 0
            for char in shortcode:
                pk = pk * 64 + _IG_SHORTCODE_ALPHABET.index(char)
            request = Request(
                _IG_MEDIA_INFO_URL.format(pk=pk),
                headers={
                    "X-IG-App-ID": _IG_WEB_APP_ID,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://www.instagram.com/",
                },
            )
            with ydl.urlopen(request) as response:
                payload = json.loads(response.read())
            item = (payload.get("items") or [{}])[0]
            plays = item.get("play_count") or item.get("ig_play_count")
            return int(plays) if plays is not None else None
        except Exception as e:  # noqa: BLE001
            logger.info("Could not fetch Instagram play count for %s: %s", shortcode, e)
            return None

    @staticmethod
    def _format_date(raw_date: str | None) -> str | None:
        """Converts YYYYMMDD to YYYY-MM-DD."""
        if not raw_date or len(raw_date) != 8:
            return None
        return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
