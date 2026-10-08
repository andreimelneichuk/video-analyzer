# Документация архитектуры и этапов разработки: Skycoach Reels Ad Analyzer

Данная директория содержит исчерпывающее пошаговое описание архитектурных решений, интерфейсов, моделей данных, логики обработки и инструкций по реализации для каждого этапа проекта **Skycoach Reels Ad Analyzer**.

---

## Навигация по этапам разработки

| № | Файл | Ключевой фокус | Результат этапа |
|---|---|---|---|
| **01** | [`01_project_init_and_models.md`](./01_project_init_and_models.md) | Архитектура проекта, стек, управление зависимостями (`uv`), Pydantic/SQLAlchemy модели данных и схемы БД | Готовая структура репозитория, доменные модели `Task`, `ReelMetrics`, `IntegrationAnalysis`, enum-типы |
| **02** | [`02_media_extraction_and_edge_cases.md`](./02_media_extraction_and_edge_cases.md) | Модуль экстрактора (`yt-dlp`), нормализация ссылок, парсинг метрик с `null`, типизированные ошибки краевых случаев | Сервис скачивания видео (720p/480p) и извлечения метрик, обработка приватных/удаленных/битых ссылок |
| **03** | [`03_ai_engine_and_rule_layer.md`](./03_ai_engine_and_rule_layer.md) | Мультимодальный AI-пайплайн (VLM), промпт-инжиниринг, Structured Output и детерминированный Rule Engine удержаний | Классификатор (0, 1, 2), шкала заметности (1–5) с обоснованием, детекция дефектов баннера |
| **04** | [`04_queue_worker_and_cache.md`](./04_queue_worker_and_cache.md) | Асинхронные очереди (Redis + RQ), воркеры, таймауты, идемпотентность и кэширование по `canonical_url` | Фоновый воркер, изоляция задач, мгновенная отдача результатов из кэша без повторного анализа |
| **05** | [`05_api_and_frontend.md`](./05_api_and_frontend.md) | REST API (FastAPI) и веб-интерфейс для инфлюенс-менеджера (Tailwind + Polling) | Эндпоинты `/api/tasks`, `/api/export`, UI с пакетным вводом до 20 ссылок, автообновлением и готовыми примерами |
| **06** | [`06_acceptance_testing_and_qa.md`](./06_acceptance_testing_and_qa.md) | Комплекс автотестов (`pytest`), верификация приёмочных критериев и проверка на ручной разметке баннеров | 100% покрытие приёмочных кейсов (приватный, удаленный, без звука, длинный, дубликат, невалидный) |
| **07** | [`07_docker_deployment_and_docs.md`](./07_docker_deployment_and_docs.md) | Мультистейдж `Dockerfile`, `docker-compose`, развертывание на публичный хостинг (Railway/Render) и `README.md` | Готовый публичный сервис с HTTPS, документация по запуску и архитектурный отчет для ревью |

---

## Общая схема потоков данных (Data Flow)

```mermaid
sequenceDiagram
    autonumber
    actor Manager as Инфлюенс-менеджер
    participant UI as Веб-интерфейс
    participant API as FastAPI Router
    participant DB as База данных (SQLite/PostgreSQL)
    participant Redis as Redis Queue (RQ)
    participant Worker as Background Worker
    participant Extractor as Instagram Extractor (yt-dlp)
    participant VLM as Multimodal AI (VLM)
    participant Rules as Deterministic Rule Engine

    Manager->>UI: Ввод до 20 ссылок Reels
    UI->>API: POST /api/tasks (список URL)
    API->>API: Нормализация URLs
    loop Для каждого URL
        API->>DB: Проверка наличия canonical_url со статусом COMPLETED
        alt Найден в кэше
            DB-->>API: Готовый результат
            API-->>UI: task_id (статус COMPLETED, cached: true)
        else Новый URL
            API->>DB: Создание записи Task (status: PENDING)
            API->>Redis: Постановка задачи process_reel_task(task_id)
            API-->>UI: task_id (статус PENDING, cached: false)
        end
    end

    loop Фоновая обработка воркером
        Redis->>Worker: Получение задачи
        Worker->>DB: Обновление статуса: DOWNLOADING
        Worker->>Extractor: Запрос метрик и скачивание видео (<=720p)
        alt Ошибка доступа / удален / приватный
            Extractor-->>Worker: Typed Exception (Private / Deleted / Invalid)
            Worker->>DB: Статус FAILED + понятное сообщение
        else Успешное скачивание
            Extractor-->>Worker: Метрики (null-safe) + путь к temp MP4
            Worker->>DB: Обновление статуса: ANALYZING
            Worker->>VLM: Мультимодальный анализ видеоряда + звука
            VLM-->>Worker: Структурированные признаки (класс, секунды, % кадра, дефекты)
            Worker->>Rules: Применение правил проверки (вычисление удержания)
            Rules-->>Worker: Итоговый вердикт (штраф, статус выплаты, обоснование)
            Worker->>DB: Статус COMPLETED + запись всех результатов
            Worker->>Worker: Удаление временного MP4 файла
        end
    end

    loop Polling статусов каждые 2 секунды
        UI->>API: GET /api/tasks?ids=...
        API->>DB: Чтение статусов
        DB-->>API: Данные задач
        API-->>UI: Обновление строк таблицы в реальном времени
    end
```
