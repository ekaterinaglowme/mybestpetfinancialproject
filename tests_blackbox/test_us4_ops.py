"""US-4. Эксплуатационные/сквозные сценарии.

429 проверяется на отдельном «строгом» стенде (низкий лимит) — фикстура
strict_base_url.
"""

import httpx
import pytest

# Паспорт, который мок СтопЛиста считает чистым.
CLEAN_PASSPORT = "1234567890"


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
def test_ready_gotov_prinimat_trafik(base_url):
    """Сервис готов принимать трафик: связность с реальной БД доказана.

    Дано: здоровый стенд (app + Postgres подняты).
    Когда: GET /ready.
    Тогда: 200 и тело {"status": "ready"} — единственная сквозная проверка
           readiness через настоящий Postgres (юнит-тесты мокают сессию).
    """
    r = httpx.get(f"{base_url}/ready", timeout=10)
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


@pytest.mark.blackbox
def test_rate_limit_otdaet_429(strict_base_url):
    """Под градом запросов часть отклоняется кодом 429.

    Дано: строгий стенд с низким лимитом RPS.
    Когда: быстро шлём много POST /applications/v2 подряд.
    Тогда: хотя бы один ответ — 429 (защита от перегрузки работает). Точное
           число не фиксируем — оно зависит от настроек, не от бизнес-правил.
    """
    payload = {
        "last_name": "Нагрузкин",
        "first_name": "Поток",
        "middle_name": "",
        "phone": "+79990000001",
        "birth_date": "1995-01-01",
        "email": "nagruzkin@example.ru",
        "passport": CLEAN_PASSPORT,
        "region": "Москва",
        "loan_purpose": "покупка",
        "amount": 1000,
    }
    codes = [
        httpx.post(f"{strict_base_url}/applications/v2", json=payload, timeout=10).status_code
        for _ in range(20)
    ]
    assert 429 in codes, f"ни одного 429 среди {codes}"
