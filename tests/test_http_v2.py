import uuid as uuid_mod
from datetime import date

import pytest

import db
import server
from bki import BkiOutcome
from bki_parse import BkiFeatures
from models import ExternalServiceCall
from repository import create_loan
from sqlalchemy import select

VALID = {
    "last_name": "Иванов", "first_name": "Иван", "phone": "+79991234567",
    "birth_date": "2000-05-15", "email": "ivan@example.ru",
    "passport": "1234567890", "region": "Москва",
    "loan_purpose": "покупка", "amount": 100000,
}


@pytest.fixture
def clean_blacklist(monkeypatch):
    async def fake(passport):
        return False
    monkeypatch.setattr(server, "check_passport", fake)


def _features(delinquent: bool = False) -> BkiFeatures:
    return BkiFeatures(
        score=702, n_contracts=1, has_writeoff=delinquent,
        has_current_delinquency=delinquent,
        overdue_amount_kop=434900 if delinquent else 0,
        max_dpd=6 if delinquent else 0, n_late=0, debt_load_kop=0,
        inq_30=1, inq_90=3, inq_365=6,
    )


@pytest.fixture(autouse=True)
def clean_bki(monkeypatch):
    """По умолчанию бюро отвечает чистой историей; тесты переопределяют."""
    async def fake(passport):
        return BkiOutcome(status="ok", features=_features(), raw_xml="<ok/>")
    monkeypatch.setattr(server, "get_report_with_retry", fake)


async def test_v2_approved(async_client, clean_blacklist):
    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


async def test_v2_declined_in_black_list(async_client, monkeypatch):
    async def fake(passport):
        return True
    monkeypatch.setattr(server, "check_passport", fake)
    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert any("чёрном списке" in r for r in body["reasons"])


async def test_v2_fail_closed_when_service_down(async_client, monkeypatch):
    async def fake(passport):
        raise server.BlackListError("down")
    monkeypatch.setattr(server, "check_passport", fake)
    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert any("Не удалось проверить" in r for r in body["reasons"])


async def test_v2_underage(async_client, clean_blacklist):
    resp = await async_client.post(
        "/applications/v2", json={**VALID, "birth_date": "2015-01-01"}
    )
    assert resp.json()["status"] == "declined"


async def test_v2_missing_passport_422(async_client, clean_blacklist):
    body = {k: v for k, v in VALID.items() if k != "passport"}
    resp = await async_client.post("/applications/v2", json=body)
    assert resp.status_code == 422


@pytest.mark.parametrize("bad_passport", [
    "12345",                    # короче 10 цифр
    "12345678901",              # длиннее 10 цифр
    "abcdefghij",               # буквы
    '1234" Номер="999999',      # инъекция в XML-запрос к бюро
])
async def test_v2_invalid_passport_422(async_client, clean_blacklist, bad_passport):
    # Кривой паспорт отклоняется на валидации — до похода в бюро.
    resp = await async_client.post(
        "/applications/v2", json={**VALID, "passport": bad_passport},
    )
    assert resp.status_code == 422


async def test_v2_loan_created_on_approval_with_amount(async_client, clean_blacklist):
    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.json()["status"] == "approved"
    aid = resp.json()["application_id"]
    loan = await async_client.get(f"/loans/{aid}")
    assert loan.status_code == 200
    assert loan.json()["application_id"] == aid
    assert loan.json()["amount"] == 100000.0


async def test_v2_no_loan_when_declined(async_client, monkeypatch):
    async def fake(passport):
        return True
    monkeypatch.setattr(server, "check_passport", fake)
    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.json()["status"] == "declined"
    aid = resp.json()["application_id"]
    assert (await async_client.get(f"/loans/{aid}")).status_code == 404


async def test_v2_persisted(async_client, clean_blacklist):
    import db
    from sqlalchemy import select

    from models import Application

    await async_client.post("/applications/v2", json=VALID)
    async with db.AsyncSessionLocal() as s:
        row = (await s.execute(select(Application))).scalars().first()
    assert row is not None
    assert row.passport == "1234567890"
    assert row.email == "ivan@example.ru"


async def test_v2_logs_bki_call_to_journal(async_client, clean_blacklist):
    # БКИ собирается в фоне и пишется в JSON-журнал (не в типизированные колонки).
    resp = await async_client.post("/applications/v2", json=VALID)
    application_id = uuid_mod.UUID(resp.json()["application_id"])
    async with db.AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(ExternalServiceCall).where(
                ExternalServiceCall.application_id == application_id,
                ExternalServiceCall.service == "bki",
            )
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "ok"
    assert rows[0].payload["response"] == {"ok": ""}  # xml_to_dict("<ok/>")


async def test_v2_bki_delinquency_no_longer_declines(async_client, clean_blacklist, monkeypatch):
    # MVP: БКИ ушёл из решения — просрочка в бюро больше НЕ заворачивает заявку.
    async def fake(passport):
        return BkiOutcome(status="ok", features=_features(delinquent=True), raw_xml="<bad/>")
    monkeypatch.setattr(server, "get_report_with_retry", fake)

    resp = await async_client.post("/applications/v2", json=VALID)
    body = resp.json()
    assert body["status"] == "approved"
    assert body["reasons"] == []


async def test_v2_bki_unavailable_does_not_affect_decision(async_client, clean_blacklist, monkeypatch):
    # MVP: БКИ ушёл из решения — недоступность бюро НЕ заворачивает заявку,
    # но её след (unavailable) всё равно копится в bki_reports (фоновый сбор).
    async def fake(passport):
        return BkiOutcome(status="unavailable", features=None, raw_xml=None)
    monkeypatch.setattr(server, "get_report_with_retry", fake)

    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"          # бюро не влияет на решение
    application_id = uuid_mod.UUID(body["application_id"])
    async with db.AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(ExternalServiceCall).where(
                ExternalServiceCall.application_id == application_id,
                ExternalServiceCall.service == "bki",
            )
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "unavailable"        # но данные для статистики собраны
    assert rows[0].payload["response"] is None    # сырого ответа не было


async def test_v2_no_history_is_approved(async_client, clean_blacklist, monkeypatch):
    # «Истории нет» (Код=3) — валидный ответ, НЕ сбой: заявка одобряется.
    async def fake(passport):
        return BkiOutcome(status="no_history", features=None, raw_xml="<nohist/>")
    monkeypatch.setattr(server, "get_report_with_retry", fake)

    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.json()["status"] == "approved"


async def test_v2_active_loan_no_longer_declines(async_client, clean_blacklist):
    # MVP: внутренняя история ушла из решения — активный заём больше НЕ заворачивает.
    first = await async_client.post("/applications/v2", json=VALID)
    assert first.json()["status"] == "approved"
    # Вторая заявка того же человека (тот же VALID = тот же identity) → тоже одобрена.
    second = await async_client.post("/applications/v2", json=VALID)
    assert second.json()["status"] == "approved"


async def test_v2_prior_default_no_longer_declines(async_client, clean_blacklist):
    # MVP: прошлый невозврат больше НЕ влияет на решение (история ушла из решения).
    first = await async_client.post("/applications/v2", json=VALID)
    application_id = first.json()["application_id"]
    # Фиксируем невозврат существующей ручкой.
    repay = await async_client.post(
        f"/loans/{application_id}/repay", json={"outcome": "не вернули"},
    )
    assert repay.status_code == 200
    second = await async_client.post("/applications/v2", json=VALID)
    assert second.json()["status"] == "approved"


async def test_v2_logs_blacklist_call_to_journal(async_client, clean_blacklist):
    # Вызов чёрного списка тоже пишется в единый JSON-журнал.
    resp = await async_client.post("/applications/v2", json=VALID)
    application_id = uuid_mod.UUID(resp.json()["application_id"])
    async with db.AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(ExternalServiceCall).where(
                ExternalServiceCall.application_id == application_id,
                ExternalServiceCall.service == "stoplist",
            )
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload["response"] == {"in_terror_list": False}


async def test_hot_and_cold_latency_metrics_separate(async_client, clean_blacklist):
    # Горячий приём (база+ЧС) и холодный фоновый БКИ меряются РАЗДЕЛЬНЫМИ метриками.
    from prometheus_client import REGISTRY

    def val(name):
        return REGISTRY.get_sample_value(name) or 0.0

    before_hot = val("petbank_hot_path_seconds_count")
    before_cold = val("petbank_bki_collect_seconds_count")
    await async_client.post("/applications/v2", json=VALID)
    assert val("petbank_hot_path_seconds_count") == before_hot + 1
    assert val("petbank_bki_collect_seconds_count") == before_cold + 1
