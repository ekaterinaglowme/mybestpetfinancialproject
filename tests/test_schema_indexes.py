"""Проверки индексов схемы — под конкретные запросы слоя данных."""

from sqlalchemy import inspect

import db


async def test_applications_user_id_indexed():
    """applications.user_id проиндексирован.

    Под get_user_loan_flags: поиск заявок клиента по user_id + join loans.
    FK в PostgreSQL сам индекс не создаёт — заводим явно, иначе seq scan по applications.
    """
    async with db.engine.connect() as conn:
        indexes = await conn.run_sync(lambda c: inspect(c).get_indexes("applications"))
    indexed = {tuple(ix["column_names"]) for ix in indexes}
    assert ("user_id",) in indexed, f"нет индекса по user_id, есть индексы: {indexes}"
