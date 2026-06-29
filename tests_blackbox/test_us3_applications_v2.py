"""US-3. Заявка v2 с проверкой паспорта по внешнему чёрному списку.

Чёрно-ящичные тесты: бьём по реальному HTTP поднятого приложения, ничего не
импортируя из его кода.
"""

import httpx
import pytest

# Паспорт, который мок СтопЛиста считает «в списке» (см. tests_blackbox/blacklist_mock.py).
BLACKLISTED_PASSPORT = "0000000000"
# Любой другой паспорт мок считает чистым.
CLEAN_PASSPORT = "1234567890"


def _v2_payload(passport: str) -> dict:
    """Валидная заявка v2 совершеннолетнего; меняется только паспорт."""
    return {
        "last_name": "Тестов",
        "first_name": "Тест",
        "middle_name": "Тестович",
        "phone": "+79990000000",
        "birth_date": "1990-01-01",
        "email": "test@example.ru",
        "passport": passport,
        "region": "Москва",
        "loan_purpose": "покупка",
        "amount": 100000,
    }


@pytest.mark.blackbox
def test_stoplist_uchastvuet_v_formirovanii_otveta(base_url):
    """Внешний СтопЛист реально участвует в формировании решения по заявке v2.

    Цель этого теста — НЕ проверить конкретное бизнес-правило (пороги возраста и
    тексты причин меняются и живут в юнит-тестах), а доказать, что приложение
    действительно ходит во внешний сервис и его ответ формирует результат —
    то есть «мы туда заходим и он участвует».

    Дано: два одинаковых совершеннолетних валидных заявителей, различающихся
          ТОЛЬКО паспортом — один паспорт мок СтопЛиста считает «в списке»,
          другой чистым.
    Когда: подаём обе заявки на POST /applications/v2 по реальному HTTP.
    Тогда: оба запроса успешны (HTTP 200), но РЕШЕНИЯ ОТЛИЧАЮТСЯ. Раз ответ
           зависит от паспорта (а отличает их только проверка по СтопЛисту) —
           значит внешний сервис реально в цепочке формирования ответа.
    """
    flagged = httpx.post(
        f"{base_url}/applications/v2", json=_v2_payload(BLACKLISTED_PASSPORT), timeout=10
    )
    clean = httpx.post(
        f"{base_url}/applications/v2", json=_v2_payload(CLEAN_PASSPORT), timeout=10
    )

    assert flagged.status_code == 200, flagged.text
    assert clean.status_code == 200, clean.text

    flagged_status = flagged.json()["status"]
    clean_status = clean.json()["status"]
    assert flagged_status != clean_status, (
        "Решение не изменилось при смене паспорта — значит ответ внешнего "
        f"СтопЛиста не влияет на результат (оба: {flagged_status!r}). "
        "Интеграция с внешним сервисом не работает."
    )


# Паспорт, на котором мок СтопЛиста отвечает ошибкой 500 (имитация недоступности).
ERROR_PASSPORT = "5000000000"


@pytest.mark.blackbox
def test_stoplist_nedostupen_fail_closed(base_url):
    """СтопЛист недоступен → заявка не падает 5xx, а безопасно отклоняется.

    Дано: паспорт, на котором мок СтопЛиста возвращает ошибку (имитация сбоя).
    Когда: POST /applications/v2.
    Тогда: HTTP 200 (НЕ 5xx — ключевое свойство fail-closed) и решение declined.
           Сервис деградирует безопасно, а не ломается.
    """
    r = httpx.post(
        f"{base_url}/applications/v2", json=_v2_payload(ERROR_PASSPORT), timeout=10
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "declined"
