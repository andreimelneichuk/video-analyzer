# Этап 5. REST API и веб-интерфейс инфлюенс-менеджера

## 1. Цели этапа
1. Реализовать высокопроизводительный REST API на FastAPI с валидацией входных данных через Pydantic v2.
2. Поддержать строгие требования ТЗ:
   - Прием списка ссылок (до 20 штук за один запрос);
   - Асинхронный возврат `task_id`;
   - Выдача кэшированных данных без повторного запуска воркера.
3. Разработать интуитивный, эргономичный веб-интерфейс для инфлюенс-менеджеров с автообновлением статусов в реальном времени (polling раз в 2 секунды).
4. Реализовать кнопку быстрой загрузки тестовых примеров (хорошие видео, ролики со штрафами, краевые случаи) для мгновенной проверки экзаменатором.
5. Реализовать экспорт отчета по проверенным интеграциям в формат CSV.

---

## 2. Спецификация REST API

### 2.1. `POST /api/tasks` — Пакетное создание задач
- **Входной JSON:**
  ```json
  {
    "urls": [
      "https://www.instagram.com/reel/DceO7gsR0w-/",
      "https://www.instagram.com/reel/Dcdl9dgSEHd/"
    ]
  }
  ```
- **Правила валидации:**
  - Массив `urls` не должен быть пустым;
  - Длина массива $\le 20$ (при превышении — ошибка `422 Unprocessable Entity`);
  - Каждая строка проверяется на соответствие URL.
- **Ответ `200 OK`:**
  ```json
  {
    "created_count": 1,
    "cached_count": 1,
    "tasks": [
      {
        "id": "c1f79f53-29a3-4883-a26b-9c745749be8d",
        "url": "https://www.instagram.com/reel/DceO7gsR0w-/",
        "status": "COMPLETED",
        "is_cached": true
      },
      {
        "id": "8a31e84d-91b4-4e92-95f2-95ec1d310619",
        "url": "https://www.instagram.com/reel/Dcdl9dgSEHd/",
        "status": "PENDING",
        "is_cached": false
      }
    ]
  }
  ```

### 2.2. `GET /api/tasks` — Получение списка задач
- **Query-параметры:**
  - `ids`: список идентификаторов через запятую (для быстрого live polling активных задач пользователя);
  - `limit`: пагинация (по умолчанию 50);
  - `status`: фильтр по статусу (`COMPLETED`, `FAILED` и др.).
- **Ответ `200 OK`:** Возвращает массив задач с вложенными `metrics` и `analysis`.

### 2.3. `GET /api/tasks/{task_id}` — Детализация задачи
Возвращает полную карточку задачи, включая таймкоды, дефекты и обоснование AI.

### 2.4. `GET /api/export/csv` — Экспорт данных
Генерирует и скачивает `skycoach_integrations_report.csv` со столбцами: URL, Автор, Просмотры, Лайки, Комментарии, Класс, Заметность, Штраф, Статус выплаты, Обоснование.

---

## 3. Реализация эндпоинтов (`src/api/endpoints/tasks.py`)

```python
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import uuid

from src.database import get_db
from src.schemas.task import TaskBatchCreateRequest, TaskBatchResponse, TaskItemResponse
from src.services.extractor.normalizer import normalize_video_url
from src.services.cache_service import TaskCacheService
from src.workers.queue import enqueue_reel_analysis
from src.models.task import Task
from src.models.enums import TaskStatus

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("", response_model=TaskBatchResponse, status_code=status.HTTP_200_OK)
async def create_tasks_batch(payload: TaskBatchCreateRequest, db: AsyncSession = Depends(get_db)):
    # 1. Валидация ограничения пакета (до 20 ссылок)
    urls = [u.strip() for u in payload.urls if u.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="Список ссылок пуст.")
    if len(urls) > 20:
        raise HTTPException(
            status_code=422, detail="Максимальное количество ссылок за один раз — 20."
        )

    response_items = []
    created_count = 0
    cached_count = 0

    for raw_url in urls:
        canonical_url = normalize_video_url(raw_url)
        if not canonical_url:
            # Создаем задачу сразу в статусе FAILED (невалидный URL)
            task = Task(
                id=str(uuid.uuid4()),
                original_url=raw_url,
                canonical_url=raw_url,
                status=TaskStatus.FAILED,
                error_message="Некорректный формат ссылки (поддерживаются Instagram Reels и YouTube Shorts)",
            )
            db.add(task)
            await db.commit()
            response_items.append(TaskItemResponse.from_orm(task, is_cached=False))
            continue

        # 2. Проверка кэша (ранее выполненные ролики)
        cached_task = await TaskCacheService.find_existing_completed_task(db, canonical_url)
        if cached_task:
            cached_count += 1
            response_items.append(TaskItemResponse.from_orm(cached_task, is_cached=True))
            continue

        # 3. Проверка активных задач (дедупликация на лету)
        active_task = await TaskCacheService.find_active_in_flight_task(db, canonical_url)
        if active_task:
            response_items.append(TaskItemResponse.from_orm(active_task, is_cached=False))
            continue

        # 4. Новая задача: сохраняем в БД и отправляем в очередь воркеров
        new_task = Task(
            id=str(uuid.uuid4()),
            original_url=raw_url,
            canonical_url=canonical_url,
            status=TaskStatus.PENDING,
        )
        db.add(new_task)
        await db.commit()

        # Постановка в Redis RQ
        enqueue_reel_analysis(new_task.id)
        created_count += 1
        response_items.append(TaskItemResponse.from_orm(new_task, is_cached=False))

    return TaskBatchResponse(
        created_count=created_count, cached_count=cached_count, tasks=response_items
    )
```

---

## 4. Архитектура и функции веб-интерфейса

Интерфейс спроектирован для максимальной скорости и удобства инфлюенс-менеджера:
1. **Панель ввода:**
   - Многострочное текстовое поле с динамическим счетчиком `(строк: N / 20)`.
   - Кнопка **«Примеры из ТЗ»** — мгновенно вставляет 5 разноплановых роликов (отличная интеграция, обрезанный баннер, приватный аккаунт, видео без звука, невалидный URL).
   - Кнопка **«Запустить анализ»** с анимацией загрузки.
2. **Индикация процесса (Live Polling):**
   - Пока в таблице есть задачи в статусах `PENDING`, `DOWNLOADING` или `ANALYZING`, каждые 2 секунды выполняется запрос `GET /api/tasks?ids=...`.
   - Статусы отображаются анимированными бейджами:
     - `PENDING` $\to$ серый пульсирующий бейдж «В очереди»;
     - `DOWNLOADING` $\to$ синий спиннер «Загрузка видео и метрик»;
     - `ANALYZING` $\to$ фиолетовый спиннер «AI-анализ видео и звука»;
     - `COMPLETED` $\to$ зеленый бейдж «Готово»;
     - `FAILED` $\to$ красный бейдж с раскрывающимся текстом ошибки.
3. **Таблица результатов:**
   - **Колонка «Метрики»:** Отображает просмотры, лайки, комментарии. Если метрика скрыта — отображается серый бейдж `N/A`, а не `0`!
   - **Колонка «Класс интеграции»:**
     - `Класс 0: Нет рекламы` (нейтральный серый);
     - `Класс 1: Упоминание` (желтый предупреждающий);
     - `Класс 2: Реклама продукта` (ярко-зеленый).
   - **Колонка «Заметность»:** Звездный рейтинг (1–5 ⭐), длительность показа баннера и занимаемая площадь кадра (%).
   - **Колонка «Вердикт и выплата»:**
     - `100% Оплата (Дефектов нет)` — зеленый бейдж;
     - `-20% Баннер обрезан` — желтый бейдж;
     - `-30% Баннер слишком мелкий` — оранжевый бейдж;
     - `0% Не оплачивается` — красный бейдж.
   - **Колонка «Обоснование»:** Подробный текст с объяснением решения, наличием промокода и рекомендациями.

---

## 5. Чек-лист готовности Этапа 5
- [ ] Эндпоинт `POST /api/tasks` отклоняет пакеты $>20$ ссылок кодом 422.
- [ ] Ранее проанализированные ссылки отдаются со статусом `COMPLETED` и признаком `is_cached: true`.
- [ ] Веб-интерфейс обновляет таблицу раз в 2 секунды без перезагрузки всей страницы.
- [ ] Отсутствующие метрики явно отображаются как `N/A`.
- [ ] Добавлена кнопка быстрой подстановки тестовых ссылок.
- [ ] Работает экспорт отчета в CSV.
