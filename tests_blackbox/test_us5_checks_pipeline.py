"""US-5. Пайплайн проверок: БКИ → чёрный список → внутренняя история.

Чёрно-ящичные тесты: бьём по реальному HTTP поднятого приложения. Проверяем,
что внешнее бюро реально участвует в решении, что его недоступность даёт
управляемый отказ (fail-closed: 200 + declined, НЕ 5xx), и что собственная
история невозврата блокирует новую выдачу. Тексты причин — зона юнит-тестов.
"""

import uuid

import httpx
import pytest

# Паспорта из tests_blackbox/bki_mock.py — держать синхронно.
BKI_CLEAN = "0000024949"       # бюро: история без просрочек
BKI_DELINQUENT = "0000990052"  # бюро: списанный договор с долгом
BKI_DOWN = "0000000009"        # бюро: всегда «повторите позже» (Код=9)


def _payload(passport: str, phone: str) -> dict:
    """Валидная заявка v2 совершеннолетнего; паспорт и телефон задают сценарий.

    Телефон входит в identity пользователя (ФИО+ДР+телефон) — разные телефоны
    дают РАЗНЫХ клиентов, чтобы сценарии не пересекались через внутреннюю историю.
    """
    return {
        "last_name": "Пайплайнов",
        "first_name": "Тест",
        "middle_name": "",
        "phone": phone,
        "birth_date": "1990-01-01",
        "email": "pipeline@example.ru",
        "passport": passport,
        "region": "Москва",
        "loan_purpose": "покупка",
        "amount": 50000,
    }


@pytest.mark.blackbox
def test_bki_uchastvuet_v_reshenii(base_url):
    """Кредитная история из бюро реально влияет на решение.

    Дано: два одинаковых заявителя, различаются ТОЛЬКО паспортом — по одному
          бюро отдаёт чистую историю, по другому — списанный договор с долгом.
    Когда: подаём обе заявки на POST /applications/v2.
    Тогда: оба запроса — HTTP 200, но решения ПРОТИВОПОЛОЖНЫ. Значит, ответ
           бюро дошёл до решения (мы туда сходили и его учли).
    """
    ok = httpx.post(
        f"{base_url}/applications/v2",
        json=_payload(BKI_CLEAN, phone="+79995550001"), timeout=30,
    )
    bad = httpx.post(
        f"{base_url}/applications/v2",
        json=_payload(BKI_DELINQUENT, phone="+79995550002"), timeout=30,
    )
    assert ok.status_code == 200 and bad.status_code == 200
    assert ok.json()["status"] == "approved"
    assert bad.json()["status"] == "declined"


@pytest.mark.blackbox
def test_nedostupnost_bki_daet_upravlyaemy_otkaz(base_url):
    """Бюро «лежит» — управляемый отказ, а не падение сервиса (fail-closed).

    Дано: паспорт, по которому мок бюро всегда отвечает «повторите позже».
    Когда: подаём заявку.
    Тогда: HTTP 200 (не 5xx — сервис жив) и решение «declined»: без
           проверенной кредитной истории деньги не выдаём.
    """
    resp = httpx.post(
        f"{base_url}/applications/v2",
        json=_payload(BKI_DOWN, phone="+79995550003"), timeout=30,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "declined"


@pytest.mark.blackbox
def test_sobstvenniy_nevozvrat_blokiruet_novuyu_vydachu(base_url):
    """Клиент не вернул наш заём → новую заявку не одобряем.

    Дано: клиент получил заём (заявка одобрена с суммой), исход зафиксирован
          как «не вернули» через POST /loans/{id}/repay.
    Когда: тот же клиент (та же связка ФИО+ДР+телефон) подаёт новую заявку.
    Тогда: HTTP 200 и «declined» — внутренняя история сработала.
    """
    phone = "+79995550004"
    first = httpx.post(
        f"{base_url}/applications/v2", json=_payload(BKI_CLEAN, phone=phone), timeout=30,
    )
    assert first.json()["status"] == "approved"
    loan_id = uuid.UUID(first.json()["application_id"])

    repay = httpx.post(
        f"{base_url}/loans/{loan_id}/repay", json={"outcome": "не вернули"}, timeout=30,
    )
    assert repay.status_code == 200

    second = httpx.post(
        f"{base_url}/applications/v2", json=_payload(BKI_CLEAN, phone=phone), timeout=30,
    )
    assert second.status_code == 200
    assert second.json()["status"] == "declined"
