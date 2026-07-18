"""bki_reports удалён (после наката миграций).

Проверка прямым подключением к БД — таблицы не видны через HTTP. Стенд поднимает
фикстура base_url (она же накатывает alembic upgrade head, включая 0007 drop).
"""
import asyncio

import asyncpg
import pytest

DSN = "postgresql://petbank:petbank@localhost:5432/petbank_test"


def _regclass(table: str):
    async def _run():
        conn = await asyncpg.connect(DSN)
        try:
            return await conn.fetchval("SELECT to_regclass($1)", f"public.{table}")
        finally:
            await conn.close()
    return asyncio.run(_run())


@pytest.mark.blackbox
def test_bki_reports_udalen(base_url):
    """Таблица bki_reports удалена миграцией.

    Дано: миграции накатаны (включая 0007 drop bki_reports).
    Когда: смотрим системный каталог.
    Тогда: таблицы bki_reports в public больше нет.
    """
    assert _regclass("bki_reports") is None, "bki_reports должна быть удалена"
