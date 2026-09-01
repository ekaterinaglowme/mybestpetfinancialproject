"""HTTP-тесты ручек займа: статус и фиксация исхода через /loans/{id}/repay.

Займы вставляем напрямую в БД (минуя одобрение заявки), чтобы управлять датой
выдачи `issued_at` — без этого нельзя проверить возврат «задним числом».
"""

import uuid
from datetime import date, timedelta

import db
from models import Loan


async def _make_loan(*, issued_at, repaid_at=None, status="выдано", amount=50000):
    """Создать заём напрямую и вернуть его application_id."""
    aid = uuid.uuid4()
    async with db.AsyncSessionLocal() as s:
        s.add(Loan(application_id=aid, amount=amount, issued_at=issued_at,
                   repaid_at=repaid_at, status=status))
        await s.commit()
    return aid


async def test_get_loan_shows_stored_status(async_client):
    aid = await _make_loan(issued_at=date.today())
    resp = await async_client.get(f"/loans/{aid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "выдано"


async def test_repay_default_marks_vernuli_today(async_client):
    # Без тела repay по умолчанию = «вернули» сегодня (сохраняем старое поведение).
    aid = await _make_loan(issued_at=date.today())
    resp = await async_client.post(f"/loans/{aid}/repay")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "вернули"
    assert body["repaid_at"] == date.today().isoformat()


async def test_repay_ne_vernuli_keeps_repaid_at_null(async_client):
    # «Не вернули» (списание) — денег не было, repaid_at остаётся пустым.
    aid = await _make_loan(issued_at=date.today())
    resp = await async_client.post(f"/loans/{aid}/repay", json={"outcome": "не вернули"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "не вернули"
    assert body["repaid_at"] is None


async def test_repay_with_past_date_freezes_debt(async_client):
    # Возврат задним числом: долг замораживается на реальную дату возврата.
    # Даты фиксированные (до водораздела ставок) — тест не зависит от «сегодня».
    issued = date(2026, 8, 2)
    repaid = date(2026, 8, 12)  # возраст 10 дней → старая сетка, ставка 5%
    aid = await _make_loan(issued_at=issued)
    resp = await async_client.post(
        f"/loans/{aid}/repay",
        json={"outcome": "вернули", "repaid_at": repaid.isoformat()},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "вернули"
    assert body["repaid_at"] == repaid.isoformat()
    assert body["current_rate"] == 0.05


async def test_fresh_loan_uses_new_rate_grid(async_client):
    # Заём, выданный «сегодня» (после водораздела 2026-09-01), — по новой сетке:
    # свежая выдача (0 дней) стоит 20% к телу.
    aid = await _make_loan(issued_at=date.today())
    resp = await async_client.get(f"/loans/{aid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_rate"] == 0.20
    assert body["amount_owed"] == 60000.0  # 50000 × 1.20


async def test_repay_date_before_issue_422(async_client):
    issued = date.today() - timedelta(days=5)
    too_early = (issued - timedelta(days=1)).isoformat()
    aid = await _make_loan(issued_at=issued)
    resp = await async_client.post(
        f"/loans/{aid}/repay", json={"outcome": "вернули", "repaid_at": too_early}
    )
    assert resp.status_code == 422


async def test_repay_date_in_future_422(async_client):
    aid = await _make_loan(issued_at=date.today())
    future = (date.today() + timedelta(days=1)).isoformat()
    resp = await async_client.post(
        f"/loans/{aid}/repay", json={"outcome": "вернули", "repaid_at": future}
    )
    assert resp.status_code == 422


async def test_repay_ne_vernuli_with_date_422(async_client):
    # У «не вернули» даты возврата быть не может.
    aid = await _make_loan(issued_at=date.today())
    resp = await async_client.post(
        f"/loans/{aid}/repay",
        json={"outcome": "не вернули", "repaid_at": date.today().isoformat()},
    )
    assert resp.status_code == 422


async def test_repay_rejects_oshibka_outcome_422(async_client):
    # «ошибка» — системный статус, оператор его передать не может.
    aid = await _make_loan(issued_at=date.today())
    resp = await async_client.post(f"/loans/{aid}/repay", json={"outcome": "ошибка"})
    assert resp.status_code == 422


async def test_repay_twice_409(async_client):
    aid = await _make_loan(issued_at=date.today())
    assert (await async_client.post(f"/loans/{aid}/repay")).status_code == 200
    again = await async_client.post(f"/loans/{aid}/repay")
    assert again.status_code == 409


async def test_repay_unknown_loan_404(async_client):
    resp = await async_client.post(f"/loans/{uuid.uuid4()}/repay")
    assert resp.status_code == 404


async def test_repay_db_failure_marks_oshibka(async_client, monkeypatch):
    # Сбой записи при фиксации исхода → 500, а заём система помечает «ошибка»
    # (отдельной транзакцией; БД при этом доступна).
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.ext.asyncio import AsyncSession

    aid = await _make_loan(issued_at=date.today())
    orig_flush = AsyncSession.flush

    async def flaky_flush(self, *args, **kwargs):
        # Падаем только на записи исхода repay, не на восстановительной «ошибка».
        if any(getattr(o, "status", None) in ("вернули", "не вернули")
               for o in self.sync_session.dirty):
            raise SQLAlchemyError("сбой записи")
        return await orig_flush(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", flaky_flush)
    resp = await async_client.post(f"/loans/{aid}/repay")
    assert resp.status_code == 500

    monkeypatch.undo()  # дальше читаем без подмены
    check = await async_client.get(f"/loans/{aid}")
    assert check.json()["status"] == "ошибка"
