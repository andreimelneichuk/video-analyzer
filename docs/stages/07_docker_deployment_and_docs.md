# Этап 7. Docker-контейнеризация, публичный деплой и документация

## 1. Цели этапа
1. Собрать легковесный production-ready `Dockerfile` с установленным `ffmpeg` и системными CA-сертификатами.
2. Подготовить локальный `docker-compose.yml` для одновременного запуска веб-сервера, воркера и Redis одной командой.
3. Развернуть сервис на публичный хостинг с HTTPS-доступом (Railway / Render / Fly.io) — обязательное требование ТЗ (*«Localhost не принимаем»*).
4. Оформить исчерпывающий `README.md`, полностью отвечающий критериям тестового задания Skycoach:
   - Инструкция по локальному запуску;
   - Список внешних сервисов с обоснованием их выбора;
   - Анализ ограничений системы;
   - План развития (Roadmap / Следующие шаги).

---

## 2. Мультистейдж Dockerfile (`Dockerfile`)

```dockerfile
# --- Stage 1: Сборка зависимостей с помощью uv ---
FROM python:3.13-slim AS builder

WORKDIR /app

# Установка curl для загрузки uv
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin/:$PATH"

# Копируем описание зависимостей
COPY pyproject.toml .
# Создаем виртуальное окружение и устанавливаем production зависимости
RUN uv venv /app/.venv && uv pip install --no-cache -r pyproject.toml

# --- Stage 2: Финальный легковесный образ ---
FROM python:3.13-slim

WORKDIR /app

# Установка ffmpeg (для обработки видео/аудио) и корневых сертификатов
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Копируем виртуальное окружение из builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Копируем исходный код приложения
COPY . /app

# Создаем директорию для базы данных и временных файлов
RUN mkdir -p /app/data /app/temp

EXPOSE 8080

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

---

## 3. Docker Compose (`docker-compose.yml`)

Оркестрация трех сервисов: Web API, фонового воркера и очереди Redis.

```yaml
services:
  redis:
    image: redis:7-alpine
    container_name: skycoach-redis
    restart: unless-stopped
    ports:
      - "6380:6379" # Используем порт 6380, чтобы не конфликтовать со стандартным 6379
    volumes:
      - redis_data:/data

  web:
    build: .
    container_name: skycoach-web
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      - PORT=8080
      - HOST=0.0.0.0
      - DATABASE_URL=sqlite+aiosqlite:////app/data/skycoach.db
      - REDIS_URL=redis://redis:6379/0
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
      - AI_PROVIDER=${AI_PROVIDER:-gemini}
    volumes:
      - app_data:/app/data
    depends_on:
      - redis

  worker:
    build: .
    container_name: skycoach-worker
    restart: unless-stopped
    command: ["rq", "worker", "skycoach_reels", "--url", "redis://redis:6379/0"]
    environment:
      - DATABASE_URL=sqlite+aiosqlite:////app/data/skycoach.db
      - REDIS_URL=redis://redis:6379/0
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
      - AI_PROVIDER=${AI_PROVIDER:-gemini}
    volumes:
      - app_data:/app/data
    depends_on:
      - redis

volumes:
  redis_data:
  app_data:
```

---

## 4. Публичный деплой (Railway / Render)

### Вариант 1. Railway (Рекомендуемый):
1. Создать проект в [Railway.app](https://railway.app).
2. Подключить `Redis` плагин в один клик.
3. Добавить сервис из GitHub репозитория:
   - Сервис 1: `web` (порт 8080, публичный HTTPS домен `*.up.railway.app`).
   - Сервис 2: `worker` (команда запуска: `rq worker skycoach_reels --url $REDIS_URL`).
4. Подключить Volume к сервисам `/app/data` для постоянного хранения файла `skycoach.db` (или переключить `DATABASE_URL` на бесплатный managed PostgreSQL).
5. Прописать переменные окружения `GEMINI_API_KEY` или `OPENROUTER_API_KEY`.

---

## 5. Структура и содержание итогового `README.md`

Согласно ТЗ, файл `README.md` должен содержать четыре обязательных раздела:

```markdown
# Skycoach Reels Ad Analyzer

Веб-сервис для автоматического анализа рекламных интеграций маркетплейса Skycoach в Instagram Reels.

## 1. Как запустить локально
- Через Docker Compose: `docker-compose up --build`
- Напрямую через uv:
  - `uv sync`
  - `uv run uvicorn src.main:app --reload --port 8080`
  - `uv run rq worker skycoach_reels`

## 2. Использованные внешние сервисы и обоснование выбора
- **yt-dlp**: гибкое извлечение метаданных и потока видео без необходимости платной подписки на начальном этапе.
- **Google Gemini 2.5 Flash / Qwen 2.5 Omni**: нативная поддержка видео и звука, превосходный OCR мелких игровых баннеров и стоимость менее $0.001 за ролик (в 10 раз дешевле GPT-4o).
- **Redis + RQ**: легковесная и надежная очередь фоновых задач с нативным контролем таймаутов.

## 3. Ограничения системы
- Скорость Instagram: при массовых запросах возможны блокировки по IP со стороны Meta, что требует ротации прокси в production.
- Длительность роликов: установлено жесткое ограничение на 180 сек для защиты воркеров от перегрузки.

## 4. Что бы мы сделали следующим шагом (Roadmap)
- Подключение пула резидентных прокси и fallback-скрейпера (RapidAPI / Apify);
- Обучение специализированной легковесной модели YOLOv11 для локальной детекции логотипов без отправки видео во внешние API;
- Двусторонняя интеграция с CRM / Таблицами Skycoach для автоматического выставления счетов на выплаты.
```

---

## 6. Чек-лист готовности Этапа 7
- [ ] `Dockerfile` успешно собирается локально без ошибок.
- [ ] `docker-compose up` поднимает `web`, `worker` и `redis`.
- [ ] Сервис развернут на публичный URL с активным HTTPS.
- [ ] Переменные окружения безопасно настроены в облачной панели хостинга.
- [ ] Итоговый `README.md` проверен на соответствие всем пунктам ТЗ.
- [ ] Ссылка на развернутый сервис и репозиторий готовы к отправке.
