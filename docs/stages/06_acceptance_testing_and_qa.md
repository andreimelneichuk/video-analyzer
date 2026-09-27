# Этап 6. Тестирование приёмочных критериев и QA-сьют

## 1. Цели этапа
1. Написать полный набор автоматических тестов (`pytest`) для валидации API, экстрактора, детерминированного Rule Engine и воркера.
2. Провести строгую верификацию по всем сценариям из раздела ТЗ **«Что мы проверяем на приёмке»**:
   - Приватный ролик;
   - Удалённый ролик;
   - Ролик без звука;
   - Длинный ролик;
   - Одна и та же ссылка дважды (кэш / дедупликация);
   - Невалидная ссылка.
3. Проверить точность расчетов штрафов на эталонных кейсах из `banner_review_examples.pdf` (удержания 20%, 30%, исключение 0%).

---

## 2. Матрица приёмочных сценариев (Acceptance Matrix)

| № | Сценарий приёмки | Входные данные | Ожидаемое поведение системы | Статус в БД |
|---|---|---|---|---|
| **1** | **Приватный аккаунт** | Ссылка на ролик из закрытого профиля | Экстрактор перехватывает ошибку доступа $\to$ воркер не падает $\to$ выводится: *«Аккаунт приватный или доступ ограничен»* | `FAILED` |
| **2** | **Удаленный ролик** | Ссылка на 404 / удаленный пост | Экстрактор определяет отсутствие контента $\to$ выводится: *«Ролик не найден или был удален автором»* | `FAILED` |
| **3** | **Ролик без звука** | Видео без аудиодорожки | Экстрактор ставит `has_audio=False` $\to$ AI анализирует только видеоряд $\to$ `has_voice_cta=False` $\to$ ошибок нет | `COMPLETED` |
| **4** | **Длинный ролик** | Видео длительностью > 180 сек | Превышение лимита длительности $\to$ понятная ошибка о лимите хронометража без зависания очереди | `FAILED` |
| **5** | **Дубликат ссылки** | Один и тот же URL подан дважды | Первый запрос выполняет анализ, второй запрос мгновенно возвращает кэшированный результат (`is_cached: true`) | `COMPLETED` |
| **6** | **Невалидная ссылка** | Произвольный текст (`not_a_link`, ссылка на профиль) | Валидатор нормализатора фиксирует ошибку формата $\to$ немедленный возврат понятного сообщения | `FAILED` |
| **7** | **Скрытые лайки** | Видео со скрытыми счетчиками | В `metrics.likes` сохраняется `None` $\to$ в UI отображается серый бейдж `N/A`, а **не число `0`** | `COMPLETED` |
| **8** | **Пакет > 20 ссылок** | Запрос с 21 ссылкой | API немедленно возвращает HTTP 422 с предупреждением о лимите | Ошибка API |

---

## 3. Модульные тесты Rule Engine (`tests/test_rules.py`)

Проверяют соответствие шкалы штрафов примерам из датасета Skycoach:

```python
import pytest
from src.schemas.analysis import VlmRawObservation
from src.models.enums import BannerDefect, IntegrationClass
from src.services.rules.engine import SkycoachRuleEngine


def test_full_payout_good_video():
    """Тест: баннер четко виден, дефектов нет -> 100% выплата."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=10.5,
        screen_percentage=14.0,
        has_voice_cta=False,
        has_text_cta=True,
        promo_code="VALFUN",
        observed_defects=[],
        visual_observations="Баннер размещен по центру, виден четко.",
    )
    result = SkycoachRuleEngine.evaluate(obs)
    assert result.integration_class == IntegrationClass.DIRECT_AD
    assert result.prominence_score >= 4
    assert result.deduction_percent == 0
    assert "Full payout" in result.payout_recommendation


def test_cut_off_edge_deduction():
    """Тест: баннер обрезан по краю кадра -> 20% штраф."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=8.0,
        screen_percentage=10.0,
        has_voice_cta=False,
        has_text_cta=True,
        promo_code=None,
        observed_defects=[BannerDefect.CUT_OFF_EDGE],
        visual_observations="Логотип срезан левым краем.",
    )
    result = SkycoachRuleEngine.evaluate(obs)
    assert result.deduction_percent == 20
    assert "20% deduction" in result.payout_recommendation


def test_too_small_banner_deduction():
    """Тест: баннер слишком мелкий -> 30% штраф."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=5.0,
        screen_percentage=2.8,
        has_voice_cta=False,
        has_text_cta=False,
        promo_code=None,
        observed_defects=[BannerDefect.TOO_SMALL],
        visual_observations="Баннер очень мелкий в углу экрана.",
    )
    result = SkycoachRuleEngine.evaluate(obs)
    assert result.deduction_percent == 30
    assert "30% deduction" in result.payout_recommendation


def test_banner_not_visible_excluded():
    """Тест: баннер полностью перекрыт -> 0% (исключен из оплаты)."""
    obs = VlmRawObservation(
        has_skycoach_mention=True,
        is_product_advertised=True,
        banner_duration_seconds=1.0,
        screen_percentage=0.5,
        has_voice_cta=False,
        has_text_cta=False,
        promo_code=None,
        observed_defects=[BannerDefect.NOT_VISIBLE],
        visual_observations="Баннер полностью перекрыт интерфейсом Reels.",
    )
    result = SkycoachRuleEngine.evaluate(obs)
    assert result.deduction_percent == 100
    assert "Excluded" in result.payout_recommendation
```

---

## 4. Интеграционные тесты API и кэширования (`tests/test_api.py`)

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_batch_url_limit_exceeded(client: AsyncClient):
    """Проверка лимита: больше 20 ссылок должно возвращать 422."""
    urls = [f"https://www.instagram.com/reel/post{i}/" for i in range(21)]
    response = await client.post("/api/tasks", json={"urls": urls})
    assert response.status_code == 422
    assert "20" in response.json()["detail"]


@pytest.mark.asyncio
async def test_caching_duplicate_url(client: AsyncClient, completed_task_fixture):
    """Повторная отправка завершенной ссылки должна мгновенно отдавать готовый кэш."""
    url = completed_task_fixture.canonical_url
    response = await client.post("/api/tasks", json={"urls": [url]})
    assert response.status_code == 200
    data = response.json()
    assert data["cached_count"] == 1
    assert data["tasks"][0]["is_cached"] is True
    assert data["tasks"][0]["status"] == "COMPLETED"
```

---

## 5. Запуск набора тестов
```bash
# Запуск всех тестов с отчетом покрытия
uv run pytest -v --cov=src tests/
```

---

## 6. Чек-лист готовности Этапа 6
- [ ] Все приёмочные тесты из раздела ТЗ запускаются и успешно проходят.
- [ ] Протестирована правильность отображения `None` для скрытых метрик.
- [ ] Протестированы все штрафы Skycoach: 0% (full), 20% (cut off), 30% (small), 100% (excluded).
- [ ] Проверено время ответа кэша ($< 15$ мс).
