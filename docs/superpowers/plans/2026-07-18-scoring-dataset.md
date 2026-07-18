# Analytics store `scoring_dataset` — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: используйте superpowers:subagent-driven-development
> (рекомендуется) или superpowers:executing-plans. Шаги — чекбоксы `- [ ]`.

**Goal:** Постоянная таблица `scoring_dataset` (строка на заявку: BKI-фичи + метаданные + исход),
наполняемая ежедневной `pg_cron`-джобой, — чтобы агрессивный ретеншен не стёр обучающие данные скоринга.

**Architecture:** Всё в БД (миграции Alembic). Реюзабельная вьюха `bki_features_live` (разбор BKI из
JSONB журнала) → её потребляют и Superset, и процедура-снапшот. Процедура `snapshot_scoring_dataset()`
делает идемпотентный upsert (фаза 1 — фичи+заявка, фаза 2 — исход). `pg_cron` вызывает её ежедневно
перед retention. Метка bad/good — во вьюхе `scoring_dataset_labeled` (считается на текущую дату).

**Tech Stack:** PostgreSQL 16, Alembic (async, asyncpg), `pg_cron` 1.6 (на проде), pytest blackbox
(docker compose), plpgsql-процедура.

> **⚠️ ОБНОВЛЕНО 2026-07-18:** PR #52 (`0007_drop_bki_reports`) **ВЛИТ в `main`**. Значит миграции
> этого плана сдвигаются на +1: `0007_scoring_dataset` → **`0008_scoring_dataset`** (down_revision
> `0007_drop_bki_reports`), `0008_scoring_dataset_cron` → **`0009_scoring_dataset_cron`** (down_revision
> `0008_scoring_dataset`). Ниже номера `0007`/`0008` читать как `0008`/`0009`. Коллизия `0007` с #52
> больше не актуальна (#52 выиграл `0007`, витрина #50 закрыта).

## Global Constraints

- Ветка от `origin/main` через worktree; rebase-only, мёрж-коммиты запрещены.
- **Alembic:** head `origin/main` = `0006_external_service_calls`. Новые: `0007_scoring_dataset`,
  `0008_scoring_dataset_cron`, обе `down_revision` цепочкой от `0006`. **NB:** #52 (`0007_drop_bki_reports`)
  тоже занимает `0007` от `0006` — при мёрже второго из двух PR **переименовать/ребейзнуть** его на `0008+`.
- Миграции ОБЯЗАНЫ накатываться на stock `postgres:16` (blackbox/CI, без `pg_cron`) и на проде
  (`pg_cron` 1.6). Всё, что требует расширения, — под `IF EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_cron')`.
- Приложение НЕ трогаем (store наполняет БД-джоба). ORM-модель `scoring_dataset` не заводим.
- Blackbox: маркер `@pytest.mark.blackbox`, docstring в формате user story (Дано/Когда/Тогда),
  прямое подключение asyncpg к стенду (`postgresql://petbank:petbank@localhost:5432/petbank_test`).
- venv — из основного репо: `~/Desktop/github/mybestpetfinancialproject/.venv/bin/python`.
- **Ретеншен `loans` = 65 дней** (не 30) — заложить в будущий план партиционирования `loans`.
- Канон дефолта: `вернули`=good; `не вернули`=bad; `выдано` ≥60 дн от `issued_at`=bad; `выдано` <60 дн=незрелый.

---

## Файловая структура

- Create: `alembic/versions/0007_scoring_dataset.py` — вьюха `bki_features_live`, таблица
  `scoring_dataset`, вьюха `scoring_dataset_labeled`, процедура `snapshot_scoring_dataset()`.
- Create: `alembic/versions/0008_scoring_dataset_cron.py` — `cron.schedule` (условно по `pg_cron`).
- Create: `tests_blackbox/test_scoring_dataset.py` — проверки (прямое подключение к БД).
- Create: `docs/superpowers/runbooks/scoring-dataset.md` — прод-накат + порядок джоб.

---

### Task 1: Blackbox-тесты (сначала падают)

**Files:**
- Create: `tests_blackbox/test_scoring_dataset.py`

**Interfaces:**
- Consumes: фикстура `base_url` из `tests_blackbox/conftest.py` (поднимает стенд + `alembic upgrade head`).
- Produces: helpers `_exec(sql,*a)` / `_fetch(sql,*a)` — синхронные обёртки над asyncpg.

- [ ] **Step 1: Написать тест-файл.** Пять сценариев: вьюха фич, разметка, наполнение процедурой,
  переживание чистки, идемпотентность.

```python
"""Analytics store scoring_dataset: наполнение из журнала/loans, разметка, переживание чистки.

Проверки прямым подключением к БД (порт 5432 проброшен стендом). Стенд поднимает base_url
(она же накатывает миграции). Данные сидим сами, время эмулируем датами.
"""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest

DSN = "postgresql://petbank:petbank@localhost:5432/petbank_test"

# Пример ответа БКИ (как xml_to_dict кладёт в payload.response): скор 570, 2 договора.
BKI_PAYLOAD = {
    "request": {"passport": "4512123456"},
    "response": {"КредитныйОтчетОтвет": {
        "Скоринг": {"Балл": {"#text": "570"}},
        "ИнформационнаяЧасть": {"Запросы": {"@За30Дней": "12", "@За90Дней": "17", "@За12Месяцев": "19"}},
        "СведенияОбОбязательствах": {"Договор": [
            {"Состояние": {"@Код": "12"}, "Суммы": {"СуммаОбязательства": "4066000", "ПросроченнаяЗадолженность": "0"},
             "ПлатежнаяДисциплина": {"ПлтСтрока": "1A3311A11111X11A1111A1A11"}},
            {"Состояние": {"@Код": "13"}, "Суммы": {"СуммаОбязательства": "448400", "ПросроченнаяЗадолженность": "0"},
             "ПлатежнаяДисциплина": {"ПлтСтрока": "0"}},
        ]},
    }},
}


def _run(coro):
    return asyncio.run(coro)


def _exec(sql, *args):
    async def _c():
        conn = await asyncpg.connect(DSN)
        try:
            return await conn.execute(sql, *args)
        finally:
            await conn.close()
    return _run(_c())


def _fetchrow(sql, *args):
    async def _c():
        conn = await asyncpg.connect(DSN)
        try:
            return await conn.fetchrow(sql, *args)
        finally:
            await conn.close()
    return _run(_c())


def _seed_application(*, decision="approved", region="Москва", amount=100000):
    """Создаёт user+application+журнал БКИ, возвращает application_id."""
    import json
    app_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async def _c():
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute(
                "INSERT INTO users (id, last_name, first_name, middle_name, birth_date, phone) "
                "VALUES ($1,'Тест','Тест','', '1990-01-01', $2)",
                user_id, f"+7{uuid.uuid4().int % 10**10:010d}")
            await conn.execute(
                "INSERT INTO applications (application_id, user_id, amount, status, reasons, received_at, region) "
                "VALUES ($1,$2,$3,$4,'[]'::jsonb, now(), $5)",
                app_id, user_id, amount, decision, region)
            await conn.execute(
                "INSERT INTO external_service_calls (service, application_id, status, payload, called_at) "
                "VALUES ('bki', $1, 'ok', $2::jsonb, now())",
                app_id, json.dumps(BKI_PAYLOAD))
            return app_id
        finally:
            await conn.close()
    return _run(_c())


@pytest.mark.blackbox
def test_vitrina_bki_features_live(base_url):
    """Вьюха bki_features_live разбирает фичи из журнала.

    Дано: в журнале есть ответ БКИ по заявке (скор 570, 2 договора).
    Когда: читаем bki_features_live по этой заявке.
    Тогда: score=570, inq_90=17, n_contracts=2, debt_load=448400 (в долг только состояние 13),
           max_dpd=3, n_late>0.
    """
    app_id = _seed_application()
    row = _fetchrow("SELECT * FROM bki_features_live WHERE application_id = $1", app_id)
    assert row["score"] == 570
    assert row["inq_90"] == 17
    assert row["n_contracts"] == 2
    assert row["debt_load"] == 448400
    assert row["max_dpd"] == 3
    assert row["n_late"] > 0


@pytest.mark.blackbox
def test_snapshot_napolnyaet_store(base_url):
    """Процедура снимает фичи+метаданные заявки.

    Дано: заявка с ответом БКИ в журнале.
    Когда: CALL snapshot_scoring_dataset().
    Тогда: в scoring_dataset строка с фичами (score 570) и метаданными (region, decision).
    """
    app_id = _seed_application(decision="approved", region="Москва")
    _exec("CALL snapshot_scoring_dataset()")
    row = _fetchrow("SELECT * FROM scoring_dataset WHERE application_id = $1", app_id)
    assert row is not None
    assert row["score"] == 570
    assert row["region"] == "Москва"
    assert row["decision"] == "approved"
    assert row["bki_status"] == "ok"


@pytest.mark.blackbox
def test_snapshot_dopisyvaet_iskhod(base_url):
    """Исход дописывается из loans во второй фазе.

    Дано: заявка с займом 'не вернули', issued_at 70 дней назад.
    Когда: CALL snapshot_scoring_dataset().
    Тогда: в строке loan_status='не вернули', issued_at заполнен; вьюха метит is_bad=true, is_mature=true.
    """
    app_id = _seed_application()
    issued = date.today() - timedelta(days=70)
    _exec("INSERT INTO loans (application_id, amount, issued_at, status) VALUES ($1, 50000, $2, 'не вернули')",
          app_id, issued)
    _exec("CALL snapshot_scoring_dataset()")
    row = _fetchrow("SELECT * FROM scoring_dataset_labeled WHERE application_id = $1", app_id)
    assert row["loan_status"] == "не вернули"
    assert row["is_bad"] is True
    assert row["is_mature"] is True


@pytest.mark.blackbox
def test_stroka_perezhivaet_chistku(base_url):
    """Строка store цела после удаления из операционных таблиц.

    Дано: снятый слепок заявки+займа.
    Когда: удаляем строки из loans и applications (эмуляция ретеншена).
    Тогда: строка в scoring_dataset на месте, данные не потеряны.
    """
    app_id = _seed_application()
    _exec("INSERT INTO loans (application_id, amount, issued_at, status) VALUES ($1, 50000, current_date, 'выдано')", app_id)
    _exec("CALL snapshot_scoring_dataset()")
    _exec("DELETE FROM loans WHERE application_id = $1", app_id)
    _exec("DELETE FROM applications WHERE application_id = $1", app_id)
    row = _fetchrow("SELECT score, region FROM scoring_dataset WHERE application_id = $1", app_id)
    assert row is not None and row["score"] == 570


@pytest.mark.blackbox
def test_snapshot_idempotenten(base_url):
    """Повторный прогон не плодит дублей.

    Дано: заявка.
    Когда: CALL snapshot_scoring_dataset() дважды.
    Тогда: ровно одна строка на application_id.
    """
    app_id = _seed_application()
    _exec("CALL snapshot_scoring_dataset()")
    _exec("CALL snapshot_scoring_dataset()")
    row = _fetchrow("SELECT count(*) AS n FROM scoring_dataset WHERE application_id = $1", app_id)
    assert row["n"] == 1
```

- [ ] **Step 2: Прогнать — падают** (миграций `0007`/`0008` ещё нет, объектов нет).

Run: `pytest tests_blackbox/test_scoring_dataset.py -v` (нужен Docker)
Expected: FAIL/ERROR — `bki_features_live`/`scoring_dataset`/процедуры не существуют.

- [ ] **Step 3: Commit.**

```bash
git add tests_blackbox/test_scoring_dataset.py
git commit -m "test: blackbox scoring_dataset (падают до миграции)"
```

---

### Task 2: Миграция `0007` — вьюха фич, таблица, вьюха-разметка, процедура

**Files:**
- Create: `alembic/versions/0007_scoring_dataset.py`

**Interfaces:**
- Consumes: ревизия `0006_external_service_calls` (down_revision); таблицы `users`, `applications`,
  `loans`, `external_service_calls` (есть в `0001–0006`).
- Produces: view `bki_features_live`, table `scoring_dataset`, view `scoring_dataset_labeled`,
  procedure `snapshot_scoring_dataset()`.

- [ ] **Step 1: Написать миграцию.** Весь DDL — через `op.execute`. Расширения не требуются
  (процедура и вьюхи — портируемый SQL, работают и на stock postgres).

```python
"""scoring_dataset: постоянный store фич+исходов + вьюха разбора БКИ + снапшот-процедура.

Revision ID: 0007_scoring_dataset
Revises: 0006_external_service_calls
Create Date: 2026-07-18
"""
from alembic import op

revision = "0007_scoring_dataset"
down_revision = "0006_external_service_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Вьюха разбора БКИ из журнала — одна строка на заявку (последний вызов bki),
    #    все статусы (у no_history/unavailable payload пустой → фичи NULL). Реюзают Superset и процедура.
    op.execute(
        """
        CREATE VIEW bki_features_live AS
        WITH bki AS (
            SELECT DISTINCT ON (e.application_id)
                   e.application_id, e.status AS bki_status, e.payload
            FROM external_service_calls e
            WHERE e.service = 'bki' AND e.application_id IS NOT NULL
            ORDER BY e.application_id, e.called_at DESC
        ),
        base AS (
            SELECT
                b.application_id, b.bki_status,
                (b.payload#>>'{response,КредитныйОтчетОтвет,Скоринг,Балл,#text}')::int                       AS score,
                (b.payload#>>'{response,КредитныйОтчетОтвет,ИнформационнаяЧасть,Запросы,@За30Дней}')::int    AS inq_30,
                (b.payload#>>'{response,КредитныйОтчетОтвет,ИнформационнаяЧасть,Запросы,@За90Дней}')::int    AS inq_90,
                (b.payload#>>'{response,КредитныйОтчетОтвет,ИнформационнаяЧасть,Запросы,@За12Месяцев}')::int AS inq_365,
                CASE jsonb_typeof(b.payload#>'{response,КредитныйОтчетОтвет,СведенияОбОбязательствах,Договор}')
                    WHEN 'array'  THEN b.payload#>'{response,КредитныйОтчетОтвет,СведенияОбОбязательствах,Договор}'
                    WHEN 'object' THEN jsonb_build_array(b.payload#>'{response,КредитныйОтчетОтвет,СведенияОбОбязательствах,Договор}')
                    ELSE '[]'::jsonb
                END AS contracts
            FROM bki b
        )
        SELECT
            base.application_id, base.bki_status, base.score, base.inq_30, base.inq_90, base.inq_365,
            jsonb_array_length(base.contracts) AS n_contracts,
            coalesce((SELECT bool_or((c->'Состояние'->>'@Код') IN ('52','59'))
                        FROM jsonb_array_elements(base.contracts) c), false)                    AS has_writeoff,
            coalesce((SELECT bool_or((c->'Состояние'->>'@Код') IN ('52','59')
                                AND (c->'Суммы'->>'ПросроченнаяЗадолженность')::bigint > 0)
                        FROM jsonb_array_elements(base.contracts) c), false)                    AS has_current_delinquency,
            (SELECT coalesce(sum((c->'Суммы'->>'ПросроченнаяЗадолженность')::bigint),0)
                FROM jsonb_array_elements(base.contracts) c)                                    AS overdue_amount,
            (SELECT coalesce(sum((c->'Суммы'->>'СуммаОбязательства')::bigint),0)
                FROM jsonb_array_elements(base.contracts) c
               WHERE (c->'Состояние'->>'@Код') IN ('13','52','59'))                             AS debt_load,
            (SELECT max(CASE ch WHEN '1' THEN 0 WHEN 'A' THEN 1 WHEN '2' THEN 2 WHEN '3' THEN 3
                                WHEN '4' THEN 4 WHEN '5' THEN 5 WHEN '9' THEN 6 END)
                FROM jsonb_array_elements(base.contracts) c,
                     regexp_split_to_table(coalesce(c->'ПлатежнаяДисциплина'->>'ПлтСтрока',''),'') ch)  AS max_dpd,
            (SELECT count(*) FILTER (WHERE ch IN ('A','2','3','4','5','9'))
                FROM jsonb_array_elements(base.contracts) c,
                     regexp_split_to_table(coalesce(c->'ПлатежнаяДисциплина'->>'ПлтСтрока',''),'') ch)   AS n_late
        FROM base
        """
    )

    # 2) Постоянная таблица store.
    op.execute(
        """
        CREATE TABLE scoring_dataset (
            application_id uuid PRIMARY KEY,
            received_at    timestamptz,
            age            integer,
            region         text,
            amount         numeric(12,2),
            loan_purpose   text,
            decision       text,
            bki_status     text,
            score          integer,
            n_contracts    integer,
            has_writeoff   boolean,
            has_current_delinquency boolean,
            overdue_amount bigint,
            debt_load      bigint,
            max_dpd        integer,
            n_late         integer,
            inq_30         integer,
            inq_90         integer,
            inq_365        integer,
            issued_at      date,
            loan_amount    numeric(12,2),
            repaid_at      date,
            loan_status    text,
            updated_at     timestamptz DEFAULT now()
        )
        """
    )

    # 3) Вьюха-разметка (метка bad/good по канону, на текущую дату).
    op.execute(
        """
        CREATE VIEW scoring_dataset_labeled AS
        SELECT s.*,
            (s.loan_status IN ('вернули','не вернули')
             OR (s.loan_status = 'выдано' AND s.issued_at <= current_date - 60)) AS is_mature,
            CASE
                WHEN s.loan_status = 'вернули' THEN false
                WHEN s.loan_status = 'не вернули' THEN true
                WHEN s.loan_status = 'выдано' AND s.issued_at <= current_date - 60 THEN true
                ELSE NULL
            END AS is_bad
        FROM scoring_dataset s
        """
    )

    # 4) Снапшот-процедура: фаза 1 (фичи+заявка) upsert, фаза 2 (исход) update.
    op.execute(
        """
        CREATE PROCEDURE snapshot_scoring_dataset() LANGUAGE plpgsql AS $proc$
        BEGIN
            INSERT INTO scoring_dataset AS s (
                application_id, received_at, age, region, amount, loan_purpose, decision,
                bki_status, score, n_contracts, has_writeoff, has_current_delinquency,
                overdue_amount, debt_load, max_dpd, n_late, inq_30, inq_90, inq_365, updated_at)
            SELECT
                a.application_id, a.received_at,
                date_part('year', age(a.received_at, u.birth_date))::int,
                a.region, a.amount, a.loan_purpose, a.status,
                f.bki_status, f.score, f.n_contracts, f.has_writeoff, f.has_current_delinquency,
                f.overdue_amount, f.debt_load, f.max_dpd, f.n_late, f.inq_30, f.inq_90, f.inq_365, now()
            FROM applications a
            JOIN users u ON u.id = a.user_id
            LEFT JOIN bki_features_live f ON f.application_id = a.application_id
            WHERE a.received_at >= now() - interval '30 days'
            ON CONFLICT (application_id) DO UPDATE SET
                received_at = EXCLUDED.received_at, age = EXCLUDED.age, region = EXCLUDED.region,
                amount = EXCLUDED.amount, loan_purpose = EXCLUDED.loan_purpose, decision = EXCLUDED.decision,
                bki_status = EXCLUDED.bki_status, score = EXCLUDED.score, n_contracts = EXCLUDED.n_contracts,
                has_writeoff = EXCLUDED.has_writeoff, has_current_delinquency = EXCLUDED.has_current_delinquency,
                overdue_amount = EXCLUDED.overdue_amount, debt_load = EXCLUDED.debt_load,
                max_dpd = EXCLUDED.max_dpd, n_late = EXCLUDED.n_late,
                inq_30 = EXCLUDED.inq_30, inq_90 = EXCLUDED.inq_90, inq_365 = EXCLUDED.inq_365, updated_at = now();

            UPDATE scoring_dataset s SET
                issued_at = l.issued_at, loan_amount = l.amount, repaid_at = l.repaid_at,
                loan_status = l.status, updated_at = now()
            FROM loans l
            WHERE l.application_id = s.application_id
              AND l.issued_at >= (now() - interval '65 days')::date;
        END;
        $proc$
        """
    )


def downgrade() -> None:
    op.execute("DROP PROCEDURE IF EXISTS snapshot_scoring_dataset()")
    op.execute("DROP VIEW IF EXISTS scoring_dataset_labeled")
    op.execute("DROP TABLE IF EXISTS scoring_dataset")
    op.execute("DROP VIEW IF EXISTS bki_features_live")
```

- [ ] **Step 2: Прогнать blackbox — проходят** (стенд stock postgres, без pg_cron).

Run: `pytest tests_blackbox/test_scoring_dataset.py -v`
Expected: PASS — все 5 сценариев.

- [ ] **Step 3: Убедиться, что остальные blackbox не сломались.**

Run: `pytest tests_blackbox/ -v -m blackbox`
Expected: PASS.

- [ ] **Step 4: Commit.**

```bash
git add alembic/versions/0007_scoring_dataset.py
git commit -m "feat: scoring_dataset (store + вьюха фич + вьюха разметки + снапшот-процедура)"
```

---

### Task 3: Миграция `0008` — расписание `pg_cron` (условно) + runbook

**Files:**
- Create: `alembic/versions/0008_scoring_dataset_cron.py`
- Create: `docs/superpowers/runbooks/scoring-dataset.md`

**Interfaces:**
- Consumes: ревизия `0007_scoring_dataset`; на проде — `pg_cron` 1.6, `cron.database_name=petbank`.
- Produces: задание `cron.job` `scoring-dataset-snapshot` (`@daily`), запускающее процедуру ПЕРЕД
  обслуживанием партиций.

- [ ] **Step 1: Написать миграцию.** Расписание — только если есть `pg_cron` (в тестах/CI его нет).

```python
"""Ежедневный запуск snapshot_scoring_dataset() через pg_cron (только на проде).

Revision ID: 0008_scoring_dataset_cron
Revises: 0007_scoring_dataset
Create Date: 2026-07-18
"""
from alembic import op

revision = "0008_scoring_dataset_cron"
down_revision = "0007_scoring_dataset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
                -- Снапшот в 03:00, ДО обслуживания партиций/ретеншена (partman-maintenance ставим позже).
                PERFORM cron.schedule('scoring-dataset-snapshot', '0 3 * * *',
                                      $q$CALL snapshot_scoring_dataset()$q$);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
                PERFORM cron.unschedule('scoring-dataset-snapshot');
            END IF;
        END $$;
        """
    )
```

- [ ] **Step 2: Прогнать blackbox — не сломались** (на stock postgres блок `pg_cron` пропускается).

Run: `pytest tests_blackbox/ -v -m blackbox`
Expected: PASS.

- [ ] **Step 3: Написать runbook** `docs/superpowers/runbooks/scoring-dataset.md`:
  - накат `alembic upgrade head` под `migrator`;
  - проверка: `SELECT jobname, schedule FROM cron.job WHERE jobname='scoring-dataset-snapshot';`
  - разовый прогон: `CALL snapshot_scoring_dataset();` → `SELECT count(*) FROM scoring_dataset;`
  - **порядок с ретеншеном:** snapshot (03:00) должен идти РАНЬШЕ `partman-maintenance` (когда появится);
  - Superset: указать на вьюху `bki_features_live` / `scoring_dataset_labeled` напрямую (это реальные
    вьюхи БД, read-only права хватает — виртуальный dataset больше не нужен).

- [ ] **Step 4: Commit.**

```bash
git add alembic/versions/0008_scoring_dataset_cron.py docs/superpowers/runbooks/scoring-dataset.md
git commit -m "feat: pg_cron-расписание снапшота scoring_dataset (условно) + runbook"
```

---

## Self-review

- **Покрытие спеки:** таблица (§4.1) → Task 2; вьюха-разметка (§4.2) → Task 2 + тест разметки Task 1;
  джоба 2 фазы (§5) → процедура Task 2 + тесты наполнения/исхода Task 1; охват «все заявки» (§3.2) →
  `LEFT JOIN bki_features_live` (заявка попадает даже без БКИ); идемпотентность (§3.6) → `ON CONFLICT`
  + тест; переживание чистки (§8.3) → тест; pg_cron-порядок (§3.5) → Task 3 + runbook; ретеншен
  `loans`=65 (§6) → в процедуре окно 65 дн + Global Constraints (для плана партиционирования).
- **Плейсхолдеры:** нет — весь SQL/тесты приведены.
- **Типы/имена:** `snapshot_scoring_dataset()`, `bki_features_live`, `scoring_dataset`,
  `scoring_dataset_labeled`, job `scoring-dataset-snapshot` — согласованы между миграциями, тестами, runbook.
- **Известный нюанс:** BKI-разбор дублирует `bki_parse.py` в SQL — эталон один (вьюха `bki_features_live`),
  оба места держать в синхроне при смене протокола бюро.
