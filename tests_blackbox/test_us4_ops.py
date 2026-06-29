"""US-4. Эксплуатационные/сквозные сценарии.

429 и таймаут проверяются на отдельном «строгом» стенде (низкий лимит, короткий
таймаут) — фикстура strict_base_url.
"""

import time

import httpx
import pytest

# Паспорт, на котором мок СтопЛиста отвечает с задержкой (см. blacklist_mock.py).
SLOW_PASSPORT = "9999999999"


@pytest.mark.blackbox
def test_health_zhiv(base_url):
    """Сервис жив.

    Когда: GET /health.
    Тогда: 200 и тело {"status": "ok"}.
    """
    r = httpx.get(f"{base_url}/health", timeout=10)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.blackbox
def test_rate_limit_otdaet_429(strict_base_url):
    """Под градом запросов часть отклоняется кодом 429.

    Дано: строгий стенд с низким лимитом RPS.
    Когда: быстро шлём много POST /applications подряд.
    Тогда: хотя бы один ответ — 429 (защита от перегрузки работает). Точное
           число не фиксируем — оно зависит от настроек, не от бизнес-правил.
    """
    payload = {
        "last_name": "Нагрузкин",
        "first_name": "Поток",
        "middle_name": "",
        "phone": "+79990000001",
        "birth_date": "1995-01-01",
        "country": "Россия",
        "amount": 1000,
    }
    codes = [
        httpx.post(f"{strict_base_url}/applications", json=payload, timeout=10).status_code
        for _ in range(20)
    ]
    assert 429 in codes, f"ни одного 429 среди {codes}"


@pytest.mark.blackbox
def test_dolgiy_zapros_obryvaetsya_503(strict_base_url):
    """Слишком долгий запрос обрывается предохранителем-таймаутом.

    Дано: строгий стенд с коротким REQUEST_TIMEOUT_SECONDS; паспорт, на котором
          мок СтопЛиста отвечает с большой задержкой.
    Когда: POST /applications/v2 с этим паспортом.
    Тогда: HTTP 503 (request timeout) — сервер не зависает на медленной зависимости.
    """
    payload = {
        "last_name": "Долгов",
        "first_name": "Тормоз",
        "middle_name": "",
        "phone": "+79990000002",
        "birth_date": "1995-01-01",
        "email": "slow@example.ru",
        "passport": SLOW_PASSPORT,
        "region": "Москва",
        "loan_purpose": "покупка",
        "amount": 1000,
    }
    # На строгом стенде rate limiter (RATE_LIMIT_RPS=2) общий для /applications и
    # /applications/v2 — предыдущий тест (test_rate_limit_otdaet_429) истощает токены.
    # Дожидаемся восстановления хотя бы 1 токена (capacity=2, rps=2 -> до 1с), чтобы
    # запрос дошёл до таймаут-предохранителя, а не отсёкся 429 раньше времени.
    time.sleep(1.0)
    r = httpx.post(f"{strict_base_url}/applications/v2", json=payload, timeout=10)
    assert r.status_code == 503, r.text
