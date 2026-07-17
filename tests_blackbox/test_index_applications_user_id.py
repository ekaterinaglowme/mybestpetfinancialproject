"""Индекс applications(user_id) на боевой СУБД (после наката миграций).

Проверка прямым подключением к БД — индексы не видны через HTTP. Стенд поднимает
фикстура base_url (она же накатывает alembic upgrade head, включая CONCURRENTLY-ветку).
"""
import asyncio

import asyncpg
import pytest

DSN = "postgresql://petbank:petbank@localhost:5432/petbank_test"


def _fetch(sql: str, *args):
    async def _run():
        conn = await asyncpg.connect(DSN)
        try:
            return await conn.fetch(sql, *args)
        finally:
            await conn.close()
    return asyncio.run(_run())


@pytest.mark.blackbox
def test_applications_user_id_proindeksirovan(base_url):
    """applications.user_id проиндексирован — под поиск заявок клиента.

    Дано: миграции накатаны на стенд (PostgreSQL).
    Когда: смотрим индексы applications в системном каталоге.
    Тогда: есть b-tree по (user_id) — get_user_loan_flags не уходит в seq scan.
    """
    rows = _fetch(
        "SELECT indexdef FROM pg_indexes "
        "WHERE tablename = 'applications' AND indexname = 'ix_applications_user_id'"
    )
    assert rows, "нет индекса ix_applications_user_id на applications"
    assert "(user_id)" in rows[0]["indexdef"], rows[0]["indexdef"]
