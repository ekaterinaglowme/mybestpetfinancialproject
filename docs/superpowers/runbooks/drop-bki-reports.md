# Runbook: удаление `bki_reports` на проде

Дизайн — см. [спеку](../specs/2026-07-17-drop-bki-reports-design.md) (обновлена 2026-07-18).

**Контекст:** на проде кончилось место (`bki_reports` ~5.7 ГБ, почти всё — `raw_xml`). Витрина
отменена — скоринг-фичи БКИ берём из журнала `external_service_calls.payload` (`service='bki'`),
view/dataset `bki_features_live`. Поэтому `bki_reports` дропаем **напрямую**, без витрины и gate.

## Как дропнуть (нужны права DDL — у Superset их НЕТ, он read-only)

**Вариант A — вручную (быстрее всего, авария по месту).** Тот, у кого есть прямой доступ к БД
с правами DDL (инфра / тимлид), выполняет:
```sql
SELECT pg_size_pretty(pg_total_relation_size('bki_reports'));  -- сколько освободится (~5.7 ГБ)
DROP TABLE bki_reports;                                         -- НЕОБРАТИМО (raw_xml уйдёт)
SELECT pg_size_pretty(pg_database_size('petbank'));            -- проверить, что диск вернулся
```
Миграция `0007` идемпотентна (`DROP TABLE IF EXISTS`) → после ручного дропа при деплое станет no-op.

**Вариант B — через миграцию/деплой (по-правильному).** Мёрж DROP-PR в `main` (после апрува
тимлида) → CI прогоняет `alembic upgrade head` под пользователем `migrator` (права DDL есть) →
`0007_drop_bki_reports` дропает таблицу.

## Проверка после
```sql
SELECT to_regclass('public.bki_reports');            -- ожидаем NULL
SELECT pg_size_pretty(pg_database_size('petbank'));  -- упал ~5.7 ГБ
SELECT count(*) FROM bki_features_live;              -- фичи по-прежнему доступны (из журнала)
```

## Безопасность
- Приложение `bki_reports` в рантайме не читает/не пишет (БКИ идёт в журнал; `save_bki_report` удалён).
- Входящих FK на `bki_reports` нет; `DROP` снимает её FK на `applications`.
- Откат: `alembic downgrade -1` вернёт **пустую** структуру (данные/`raw_xml` не восстановить).
