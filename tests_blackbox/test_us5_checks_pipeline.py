"""US-5. Решение по возрасту и чёрному списку; БКИ — фоновый сбор данных.

Чёрно-ящичные тесты: бьём по реальному HTTP поднятого приложения. Проверяем,
что решение принимается БЕЗ бюро (только возраст + чёрный список), а БКИ ушёл
с горячего пути — его ответ (и недоступность) на решение не влияют. Сбор
БКИ-данных идёт в фоне; проверка самих данных — зона юнит-тестов.
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
def test_bki_ne_vliyaet_na_reshenie(base_url):
    """Ответ бюро больше НЕ влияет на решение (БКИ ушёл в фоновый сбор).

    Дано: два заявителя, различаются ТОЛЬКО паспортом — по одному бюро отдаёт
          чистую историю, по другому списанный договор с долгом.
    Когда: подаём обе заявки на POST /applications/v2.
    Тогда: оба HTTP 200 и оба approved — просрочка в бюро решение не заворачивает.
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
    assert bad.json()["status"] == "approved"


@pytest.mark.blackbox
def test_nedostupnost_bki_ne_zavorachivaet(base_url):
    """Бюро «лежит» — заявка всё равно одобрена (БКИ не на горячем пути).

    Дано: паспорт, по которому мок бюро всегда отвечает «повторите позже».
    Когда: подаём заявку.
    Тогда: HTTP 200 и approved — недоступность бюро на решение не влияет
           (данные соберутся в фоне как «unavailable»).
    """
    resp = httpx.post(
        f"{base_url}/applications/v2",
        json=_payload(BKI_DOWN, phone="+79995550003"), timeout=30,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


@pytest.mark.blackbox
def test_sobstvenniy_nevozvrat_ne_blokiruet(base_url):
    """Прошлый невозврат больше НЕ блокирует новую заявку (история ушла из решения).

    Дано: клиент получил заём (заявка одобрена с суммой), исход зафиксирован
          как «не вернули» через POST /loans/{id}/repay.
    Когда: тот же клиент (та же связка ФИО+ДР+телефон) подаёт новую заявку.
    Тогда: HTTP 200 и approved — внутренняя история на решение не влияет.
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
    assert second.json()["status"] == "approved"
