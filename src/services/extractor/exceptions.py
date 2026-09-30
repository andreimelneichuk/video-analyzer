class ExtractorError(Exception):
    """Base exception for all video extraction errors."""

    def __init__(self, user_friendly_message: str):
        super().__init__(user_friendly_message)
        self.user_friendly_message = user_friendly_message


class InvalidUrlError(ExtractorError):
    """Raised when URL format is not supported or malformed."""

    def __init__(self, url: str):
        super().__init__(f"Некорректный формат ссылки на ролик: '{url}'")


class PrivateAccountError(ExtractorError):
    """Raised when the video belongs to a private/restricted account."""

    def __init__(self):
        super().__init__("Аккаунт приватный или доступ ограничен настройками приватности автора.")


class AuthRequiredError(ExtractorError):
    """Raised when the platform refuses anonymous access (login wall / bot check)."""

    def __init__(self):
        super().__init__(
            "Платформа заблокировала анонимную загрузку (требуется вход / проверка на бота). "
            "Обновите сессию Instagram в блоке «Сессия Instagram» и повторите."
        )


class VideoNotFoundError(ExtractorError):
    """Raised when the video has been deleted or cannot be found (404)."""

    def __init__(self):
        super().__init__("Ролик не найден или был удален автором.")


class ReelTooLongError(ExtractorError):
    """Raised when reel exceeds maximum allowed duration."""

    def __init__(self, duration: float, max_allowed: int = 180):
        super().__init__(
            f"Хронометраж ролика ({int(duration)} сек) превышает допустимый лимит ({max_allowed} сек)."
        )


class ExtractionTimeoutError(ExtractorError):
    """Raised when network operation or download times out."""

    def __init__(self):
        super().__init__("Превышен таймаут загрузки ролика. Попробуйте повторить позже.")
