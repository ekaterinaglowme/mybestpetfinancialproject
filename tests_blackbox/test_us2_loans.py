"""US-2. Жизненный цикл займа — /loans/{application_id}.

Чёрный ящик: проверяем инвариант «одобрение с суммой ⟺ заём существует» и
переходы статуса через реальную БД, без знания внутренней логики одобрения.
"""

import uuid

import httpx
import pytest


# Паспорт, который мок СтопЛиста считает чистым (см. tests_blackbox/blacklist_mock.py).
CLEAN_PASSPORT = "1234567890"


def _payload() -> dict:
    # Данные подобраны так, чтобы текущие правила ЗАЯВКУ ОДОБРИЛИ (взрослый, паспорт
    # не в стоп-листе, сумма указана) — иначе lifecycle-тест ниже нечего гасить и
    # он сделает skip.
    return {
        "last_name": "Заёмщиков",
        "first_name": "Пётр",
        "middle_name": "",
        "phone": "+79995550011",
        "birth_date": "1995-03-20",
        "email": "zaemshchikov@example.ru",
        "passport": CLEAN_PASSPORT,
        "region": "Москва",
        "loan_purpose": "покупка",
        "amount": 50000,
    }


@pytest.mark.blackbox
def test_invariant_odobrenie_s_summoy_sozdaet_zaem(base_url):
    """Заём существует тогда и только тогда, когда заявка одобрена с суммой.

    Дано: заявка с указанной суммой.
    Когда: POST /applications/v2, затем GET /loans/{тот же id}.
    Тогда: если решение approved — заём есть (200), сумма совпадает, статус
           «не отдал»; если declined — займа нет (404). Конкретное решение не
           навязываем — проверяем согласованность БД с решением.
    """
    p = _payload()
    created = httpx.post(f"{base_url}/applications/v2", json=p, timeout=10)
    assert created.status_code == 200, created.text
    body = created.json()
    app_id = body["application_id"]

    loan = httpx.get(f"{base_url}/loans/{app_id}", timeout=10)
    if body["status"] == "approved":
        assert loan.status_code == 200, loan.text
        assert loan.json()["amount"] == p["amount"]
        assert loan.json()["status"] == "не отдал"
    else:
        assert loan.status_code == 404


@pytest.mark.blackbox
def test_zhiznennyy_cikl_pogasheniya(base_url):
    """Одобренный заём можно погасить один раз; повторно — нельзя.

    Дано: одобренная заявка с суммой (если текущие правила её не одобрили —
          тест пропускается, гасить нечего).
    Когда: GET статус → POST repay → POST repay ещё раз.
    Тогда: статус «не отдал» → после repay «отдал» (200) → повторный repay 409.
    """
    created = httpx.post(f"{base_url}/applications/v2", json=_payload(), timeout=10)
    assert created.status_code == 200, created.text
    body = created.json()
    if body["status"] != "approved":
        pytest.skip("текущие правила не одобрили заявку — гасить нечего")
    app_id = body["application_id"]

    status = httpx.get(f"{base_url}/loans/{app_id}", timeout=10)
    assert status.status_code == 200
    assert status.json()["status"] == "не отдал"

    repaid = httpx.post(f"{base_url}/loans/{app_id}/repay", timeout=10)
    assert repaid.status_code == 200, repaid.text
    assert repaid.json()["status"] == "отдал"
    assert repaid.json()["repaid_at"]

    again = httpx.post(f"{base_url}/loans/{app_id}/repay", timeout=10)
    assert again.status_code == 409


@pytest.mark.blackbox
def test_nesushchestvuyushchiy_zaem_404(base_url):
    """Запрос несуществующего займа — 404, а не 500.

    Дано: случайный uuid, под которым займа нет.
    Когда: GET /loans/{uuid} и POST /loans/{uuid}/repay.
    Тогда: оба → 404.
    """
    rid = str(uuid.uuid4())
    assert httpx.get(f"{base_url}/loans/{rid}", timeout=10).status_code == 404
    assert httpx.post(f"{base_url}/loans/{rid}/repay", timeout=10).status_code == 404
