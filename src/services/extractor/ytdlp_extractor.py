import logging
import os
import tempfile

import certifi
import yt_dlp

from src.services.extractor.exceptions import (
    ExtractionTimeoutError,
    ExtractorError,
    PrivateAccountError,
    ReelTooLongError,
    VideoNotFoundError,
)

logger = logging.getLogger(__name__)


class YtDlpExtractor:
    """
    Service responsible for fetching video metadata and downloading media streams via yt-dlp.
    """

    def __init__(self, max_duration_sec: int = 180):
        self.max_duration_sec = max_duration_sec

    def _get_ydl_options(self, target_dir: str | None = None) -> dict:
        opts = {
            "format": "b[height<=720]/b",
            "no_color": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 20,
            "nocheckcertificate": True,
            "ca_certs": certifi.where(),
        }
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
        temp_dir = tempfile.mkdtemp(prefix="skycoach_reel_")
        opts = self._get_ydl_options(target_dir=temp_dir)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    raise VideoNotFoundError()

                # Check duration limit
                duration = info.get("duration")
                if duration and duration > self.max_duration_sec:
                    raise ReelTooLongError(duration, max_allowed=self.max_duration_sec)

                # Parse metrics with strict preservation of None (NULL)
                raw_metrics = {
                    "views": info.get("view_count"),
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
            if any(k in err_msg for k in ["private", "login", "requires authentication", "restricted"]):
                raise PrivateAccountError() from e
            elif any(k in err_msg for k in ["not found", "404", "deleted", "unavailable", "does not exist"]):
                raise VideoNotFoundError() from e
            elif any(k in err_msg for k in ["timed out", "timeout", "handshake"]):
                raise ExtractionTimeoutError() from e
            else:
                raise ExtractorError(f"Ошибка загрузки ролика: {str(e)[:150]}") from e

    @staticmethod
    def _format_date(raw_date: str | None) -> str | None:
        """Converts YYYYMMDD to YYYY-MM-DD."""
        if not raw_date or len(raw_date) != 8:
            return None
        return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
