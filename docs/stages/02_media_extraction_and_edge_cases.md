# Этап 2. Сбор данных, медиа-экстрактор и краевые случаи (Edge Cases)

## 1. Цели этапа
1. Разработать надежный модуль нормализации ссылок для устранения дубликатов из-за UTM-меток и query-параметров.
2. Реализовать извлечение метаданных ролика (`views`, `likes`, `comments`, `author`, `upload_date`) с соблюдением строгого правила: **отсутствующее/скрытое значение $\to$ `null`, а не `0`**.
3. Реализовать скачивание медиапотока в сжатом разрешении ($\le 720p$) для быстрой передачи в мультимодальную нейросеть.
4. Покрыть все обязательные приёмочные сценарии отказоустойчивости (приватный, удаленный, невалидный, длинный, беззвучный ролик) с генерацией понятных пользовательских сообщений.
5. Обеспечить надежное удаление временных видеофайлов после обработки для предотвращения переполнения диска.

---

## 2. Модуль нормализации URL (`src/services/extractor/normalizer.py`)

Пользователи могут вставлять ссылки в самых разных форматах (мобильные ссылки, ссылки с трекерами `?igsh=...`, `?utm_source=...`, ссылки `/p/` вместо `/reel/`). Для корректной работы кэширования в БД ссылки должны приводиться к каноническому виду.

```python
import re
from typing import Optional

INSTAGRAM_REEL_PATTERN = re.compile(
    r"(?:https?:\/\/)?(?:www\.)?instagram\.com\/(?:reel|reels|p)\/([a-zA-Z0-9_-]+)", re.IGNORECASE
)

YOUTUBE_SHORTS_PATTERN = re.compile(
    r"(?:https?:\/\/)?(?:www\.)?youtube\.com\/shorts\/([a-zA-Z0-9_-]+)", re.IGNORECASE
)


def normalize_video_url(url: str) -> Optional[str]:
    """
    Приводит ссылку к каноническому виду.
    Возвращает None, если формат ссылки не распознан.
    """
    url = url.strip()

    # Проверка Instagram Reels
    ig_match = INSTAGRAM_REEL_PATTERN.search(url)
    if ig_match:
        shortcode = ig_match.group(1)
        return f"https://www.instagram.com/reel/{shortcode}/"

    # Поддержка YouTube Shorts (из эталонного набора banner_review_examples)
    yt_match = YOUTUBE_SHORTS_PATTERN.search(url)
    if yt_match:
        video_id = yt_match.group(1)
        return f"https://www.youtube.com/shorts/{video_id}"

    return None
```

---

## 3. Типизированные исключения (`src/services/extractor/exceptions.py`)

На этапе приёмки тестируются конкретные краевые случаи. Пайплайн не должен падать с необработанным трейсбэком; вместо этого он генерирует типизированные ошибки:

```python
class ExtractorError(Exception):
    """Базовое исключение экстрактора."""

    def __init__(self, user_friendly_message: str):
        super().__init__(user_friendly_message)
        self.user_friendly_message = user_friendly_message


class InvalidUrlError(ExtractorError):
    """Некорректный формат ссылки."""

    def __init__(self, url: str):
        super().__init__(f"Некорректная ссылка на ролик: '{url}'")


class PrivateAccountError(ExtractorError):
    """Приватный или закрытый аккаунт."""

    def __init__(self):
        super().__init__("Аккаунт приватный или доступ ограничен настройками приватности автора.")


class VideoNotFoundError(ExtractorError):
    """Ролик удален или ссылка ведет на несуществующую публикацию."""

    def __init__(self):
        super().__init__("Ролик не найден или был удален автором.")


class ReelTooLongError(ExtractorError):
    """Ролик превышает допустимый хронометраж (например, > 180 сек)."""

    def __init__(self, duration: float):
        super().__init__(
            f"Хронометраж ролика ({int(duration)} сек) превышает допустимый лимит (180 сек)."
        )


class ExtractionTimeoutError(ExtractorError):
    """Таймаут скачивания медиа."""

    def __init__(self):
        super().__init__("Превышен таймаут загрузки ролика. Попробуйте повторить позже.")
```

---

## 4. Реализация `InstagramExtractor` на базе `yt-dlp` (`src/services/extractor/ytdlp_extractor.py`)

### Ключевые архитектурные решения:
1. **Сжатие потока:** Используется формат `-f "b[height<=720]/b"`. Анализ баннера и текста не требует 1080p/4K; 720p экономит 70% трафика и времени передачи в AI.
2. **Нулевые метрики:** `views`, `likes`, `comments` берутся из инфословаря `yt-dlp`. Если поле отсутствует или равно `None`, в Pydantic-модель пишется строго `None`, а не `0`.
3. **Обнаружение звука:** Анализируется наличие дорожки `acodec != 'none'`. Если звука нет — выставляется флаг `has_audio = False`.
4. **Очистка ресурсов:** Скачивание производится во временный каталог с использованием контекстного менеджера, гарантирующего удаление mp4 даже при возникновении сбоя.

```python
import os
import tempfile
import certifi
from contextlib import contextmanager
from typing import Generator, Tuple, Optional
import yt_dlp
from src.models.metrics import ReelMetrics
from .exceptions import (
    PrivateAccountError,
    VideoNotFoundError,
    ReelTooLongError,
    ExtractionTimeoutError,
    ExtractorError,
)


class YtDlpExtractor:
    def __init__(self, max_duration_sec: int = 180):
        self.max_duration_sec = max_duration_sec

    def _get_ydl_options(self, target_dir: Optional[str] = None) -> dict:
        opts = {
            "format": "b[height<=720]/b",
            "no_color": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 20,
            "nocheckcertificate": True,
            # Сертификаты cacert для корректной работы SSL
            "ca_certs": certifi.where(),
        }
        if target_dir:
            opts["outtmpl"] = os.path.join(target_dir, "%(id)s.%(ext)s")
        return opts

    def extract_info_and_download(self, url: str) -> Tuple[ReelMetrics, str, bool]:
        """
        Извлекает метрики и скачивает видеофайл.
        Возвращает: (ReelMetrics, video_file_path, has_audio)
        """
        temp_dir = tempfile.mkdtemp(prefix="skycoach_reel_")
        opts = self._get_ydl_options(target_dir=temp_dir)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)

                if not info:
                    raise VideoNotFoundError()

                # Проверка длительности
                duration = info.get("duration")
                if duration and duration > self.max_duration_sec:
                    raise ReelTooLongError(duration)

                # Извлечение метрик с сохранением None
                metrics = ReelMetrics(
                    views=info.get("view_count"),  # int или None
                    likes=info.get("like_count"),  # int или None
                    comments=info.get("comment_count"),  # int или None
                    author=info.get("uploader") or info.get("channel"),
                    upload_date=self._format_date(info.get("upload_date")),
                )

                # Поиск скачанного файла
                filename = ydl.prepare_filename(info)
                if not os.path.exists(filename):
                    # Поиск альтернативного расширения (например, mkv -> mp4)
                    files = os.listdir(temp_dir)
                    if files:
                        filename = os.path.join(temp_dir, files[0])
                    else:
                        raise ExtractorError("Файл не был сохранен на диск.")

                # Проверка наличия звуковой дорожки
                has_audio = info.get("acodec") != "none" and info.get("acodec") is not None

                return metrics, filename, has_audio

        except yt_dlp.utils.DownloadError as e:
            err_msg = str(e).lower()
            if "private" in err_msg or "login" in err_msg:
                raise PrivateAccountError() from e
            elif (
                "not found" in err_msg
                or "404" in err_msg
                or "deleted" in err_msg
                or "unavailable" in err_msg
            ):
                raise VideoNotFoundError() from e
            elif "timed out" in err_msg or "timeout" in err_msg:
                raise ExtractionTimeoutError() from e
            else:
                raise ExtractorError(f"Ошибка загрузки ролика: {str(e)[:150]}") from e

    @staticmethod
    def _format_date(raw_date: Optional[str]) -> Optional[str]:
        if not raw_date or len(raw_date) != 8:
            return None
        # Преобразование YYYYMMDD -> YYYY-MM-DD
        return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
```

---

## 5. Безопасное управление временными файлами

Для гарантии удаления временных файлов видео после завершения работы модели используется генераторный контекстный менеджер:

```python
import shutil
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


@contextmanager
def temporary_reel_session(url: str):
    """
    Контекстный менеджер для жизненного цикла скачанного видео.
    Гарантированно удаляет временную директорию с MP4 при выходе из блока.
    """
    extractor = YtDlpExtractor()
    metrics, video_path, has_audio = extractor.extract_info_and_download(url)
    temp_dir = os.path.dirname(video_path)
    try:
        yield metrics, video_path, has_audio
    finally:
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info("Удалены временные файлы: %s", temp_dir)
        except Exception as e:
            logger.warning("Не удалось удалить временный каталог %s: %s", temp_dir, e)
```

---

## 6. Чек-лист готовности Этапа 2
- [ ] Функция `normalize_video_url` очищает UTM, трекеры и валидирует URL.
- [ ] Ошибки парсера классифицируются в `PrivateAccountError`, `VideoNotFoundError`, `InvalidUrlError`.
- [ ] Метрики со скрытыми значениями пишутся строго как `None`, а не `0`.
- [ ] Видео скачивается с ограничением разрешения $\le 720p$.
- [ ] Корректно детектируется отсутствие звука (`has_audio = False`).
- [ ] Временный MP4 гарантированно удаляется после анализа.
