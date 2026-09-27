# Этап 1. Инициализация проекта, окружение и доменные модели

## 1. Цели этапа
1. Развернуть чистое изолированное рабочее окружение на базе Python 3.13 и менеджера пакетов `uv`.
2. Сформировать модульную структуру проекта в соответствии с принципами чистой архитектуры.
3. Спроектировать схему реляционной базы данных и доменные Pydantic/SQLAlchemy-модели, полностью покрывающие бизнес-требования ТЗ и регламента Skycoach.
4. Настроить валидацию конфигурации через переменные окружения (`pydantic-settings`).

---

## 2. Структура директорий проекта

```
skycoach-reels-analyzer/
├── .env.example              # Шаблон переменных окружения
├── .gitignore                # Исключения git (venv, db, temp mp4, .env)
├── pyproject.toml            # Спецификация зависимостей uv
├── README.md                 # Руководство по запуску и архитектуре
├── Dockerfile                # Мультистейдж сборка сервиса
├── docker-compose.yml        # Оркестрация web, worker, redis
├── docs/                     # Архитектурная документация
│   └── stages/               # Поэтапные гайды разработки
├── src/
│   ├── __init__.py
│   ├── config.py             # Настройки приложения (pydantic-settings)
│   ├── database.py           # Сессия БД (SQLAlchemy / SQLModel, SQLite WAL)
│   ├── models/
│   │   ├── __init__.py
│   │   ├── enums.py          # Статусы задач, классы интеграции, типы дефектов
│   │   ├── task.py           # SQLAlchemy модель Task
│   │   ├── metrics.py        # SQLAlchemy / Pydantic модель ReelMetrics
│   │   └── analysis.py       # SQLAlchemy / Pydantic модель IntegrationAnalysis
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── task.py           # Схемы API (TaskCreate, TaskResponse, TaskList)
│   │   └── analysis.py       # Схемы валидации Structured Output от VLM
│   ├── services/
│   │   ├── __init__.py
│   │   ├── extractor/        # Модуль скачивания и парсинга метрик (yt-dlp)
│   │   ├── ai/               # Модуль мультимодального анализа (VLM)
│   │   └── rules/            # Детерминированный Rule Engine штрафов
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── queue.py          # Подключение к Redis и очереди RQ
│   │   └── tasks.py          # Фоновые воркер-функции (process_reel)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── router.py         # Подключение эндпоинтов
│   │   └── endpoints/
│   │       ├── tasks.py      # /api/tasks (POST, GET)
│   │       └── health.py     # /api/health
│   └── ui/
│       ├── static/           # CSS, JS, SVG логотип Skycoach
│       └── templates/        # Jinja2 шаблоны страниц
└── tests/
    ├── conftest.py           # Фикстуры pytest, тестовая база в памяти
    ├── test_extractor.py     # Тесты нормализации и парсинга yt-dlp
    ├── test_rules.py         # Тесты Rule Engine (штрафы 20%, 30%, 0%)
    ├── test_api.py           # Тесты REST API (лимит 20 ссылок, кэш, ошибки)
    └── test_worker.py        # Тесты пайплайна воркера
```

---

## 3. Спецификация зависимостей (`pyproject.toml`)

Используем менеджер пакетов `uv` как самый быстрый инструмент сборки и разрешения зависимостей в Python:

```toml
[project]
name = "skycoach-reels-analyzer"
version = "0.1.0"
description = "Automated Instagram Reels Ad Analyzer for Skycoach Influence Managers"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic>=2.8.0",
    "pydantic-settings>=2.4.0",
    "sqlalchemy>=2.0.30",
    "aiosqlite>=0.20.0",
    "rq>=1.16.0",
    "redis>=5.0.0",
    "yt-dlp>=2024.8.6",
    "google-genai>=0.1.1",       # Google Gemini SDK (или openai для OpenRouter/Qwen)
    "openai>=1.40.0",            # Для универсального подключения к OpenRouter (Qwen Omni)
    "jinja2>=3.1.4",
    "python-multipart>=0.0.9",
    "httpx>=0.27.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "pytest-mock>=3.14.0",
    "ruff>=0.6.0",
]
```

---

## 4. Доменные модели и схемы базы данных

### 4.1. Перечисления (Enums) — `src/models/enums.py`

```python
from enum import Enum, IntEnum


class TaskStatus(str, Enum):
    PENDING = "PENDING"  # Поставлена в очередь
    DOWNLOADING = "DOWNLOADING"  # Скачивание видео и извлечение метрик
    ANALYZING = "ANALYZING"  # Работа мультимодальной нейросети
    COMPLETED = "COMPLETED"  # Успешно завершена
    FAILED = "FAILED"  # Ошибка (приватный, удален, таймаут)


class IntegrationClass(IntEnum):
    NONE = 0  # Про Skycoach ничего нет
    MENTION = 1  # Упоминание (логотип/название), но продукт не рекламируется
    DIRECT_AD = 2  # Прямая реклама продукта Skycoach (бустинг, валюта, CTA, промокод)


class BannerDefect(str, Enum):
    CUT_OFF_EDGE = "cut_off_edge"  # Обрезан по краю (логотип срезан) -> 20%
    TOO_SMALL = "too_small"  # Слишком мелкий баннер -> 30% (или 20%)
    OVERLAPPED_BY_UI = "overlapped_by_ui"  # Перекрыт интерфейсом/камерой -> 20%
    NOT_VISIBLE = "not_visible"  # Не виден / полностью перекрыт -> 0%
```

### 4.2. Модели сущностей — `src/models/`

#### Модель Задачи (`Task`)
- **`id`**: `str` (UUID) — уникальный идентификатор задачи.
- **`original_url`**: `str` — исходный введенный пользователем URL.
- **`canonical_url`**: `str` — нормализованный URL без query-параметров (для поиска по кэшу).
- **`status`**: `TaskStatus` — текущее состояние задачи.
- **`error_message`**: `Optional[str]` — человекочитаемая причина неудачи (если статус FAILED).
- **`created_at`**, **`updated_at`**: временные метки.

#### Модель Метрик ролика (`ReelMetrics`)
> [!IMPORTANT]
> Согласно ТЗ: Если какая-либо метрика недоступна (например, скрыты лайки), поле **строго должно быть `None` / `NULL`**, а **не `0`**. Значение 0 означает реальный ноль лайков/просмотров.

- **`views`**: `Optional[int]` — количество просмотров (None, если скрыты).
- **`likes`**: `Optional[int]` — количество лайков (None, если автор скрыл лайки).
- **`comments`**: `Optional[int]` — количество комментариев (None, если комментарии отключены).
- **`author`**: `Optional[str]` — имя аккаунта блогера / канала.
- **`upload_date`**: `Optional[str]` — дата публикации ролика в формате `YYYY-MM-DD`.

#### Модель Результата анализа (`IntegrationAnalysis`)
- **`integration_class`**: `IntegrationClass` — 0, 1 или 2.
- **`prominence_score`**: `int` (1–5) — шкала заметности:
  - `1`: Логотип мелькнул случайно или вскользь.
  - `2`: Логотип виден кратковременно, акцента нет.
  - `3`: Стабильный баннер без явного текстового/голосового призыва.
  - `4`: Отличная видимость баннера + текстовый или голосовой CTA.
  - `5`: Максимальная заметность, крупный баннер, настойчивый CTA, промокод.
- **`banner_duration_seconds`**: `float` — хронометраж показа баннера/логотипа в секундах.
- **`screen_percentage`**: `float` — ориентировочная доля площади кадра, занимаемая баннером (%).
- **`has_voice_cta`**: `bool` — наличие голосового призыва к действию.
- **`has_text_cta`**: `bool` — наличие текстового призыва к действию.
- **`promo_code`**: `Optional[str]` — распознанный промокод (например, `VALFUN`).
- **`defects`**: `list[BannerDefect]` — список обнаруженных дефектов размещения.
- **`deduction_percent`**: `int` — расчетный процент штрафа (0, 20, 30, 100%).
- **`payout_recommendation`**: `str` — статус выплаты (*"Full payout"*, *"20% deduction"*, *"30% deduction"*, *"Excluded / No payout"*).
- **`reasoning`**: `str` — исчерпывающее структурированное текстовое обоснование для менеджера.

---

## 5. Конфигурация приложения (`src/config.py`)

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    APP_NAME: str = "Skycoach Reels Analyzer"
    DEBUG: bool = False
    PORT: int = 8080
    HOST: str = "0.0.0.0"

    # База данных: SQLite в WAL-режиме по умолчанию, либо PostgreSQL
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./data/skycoach.db", description="URL подключения к БД"
    )

    # Redis для фоновых очередей
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0", description="URL подключения к Redis"
    )

    # Ключи API мультимодальных нейросетей
    GEMINI_API_KEY: str = Field(default="", description="Google AI Studio Key")
    OPENROUTER_API_KEY: str = Field(default="", description="OpenRouter Key для Qwen-Omni")
    AI_PROVIDER: str = Field(default="gemini", description="'gemini' или 'openrouter'")

    # Таймауты и ограничения
    TASK_TIMEOUT_SECONDS: int = 180
    MAX_BATCH_URLS: int = 20

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
```

---

## 6. Инициализация базы данных и WAL-режим (`src/database.py`)

Для исключения блокировок базы данных при параллельной записи из воркеров и чтении из веб-сервера, SQLite настраивается с включенным режимом **WAL (Write-Ahead Logging)** и таймаутом ожидания блокировки 30 секунд:

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from src.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"timeout": 30} if "sqlite" in settings.DATABASE_URL else {},
)

AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
```

---

## 7. Чек-лист готовности Этапа 1
- [ ] Окружение инициализировано через `uv init` / `uv sync`.
- [ ] Все зависимости зафиксированы в `pyproject.toml`.
- [ ] Модели `Task`, `ReelMetrics`, `IntegrationAnalysis` созданы и типизированы.
- [ ] Поле `null` для метрик строго отделено от числового `0`.
- [ ] Файл конфигурации `.env.example` задокументирован.
- [ ] Скрипт миграции / автоматического создания таблиц проверен локально.
