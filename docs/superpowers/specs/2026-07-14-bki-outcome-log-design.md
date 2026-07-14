# Спека: итог БКИ в логах + подробный дашборд внешних сервисов

Дата: 2026-07-14. Статус: одобрено пользователем.

## Зачем

Нужно по каждой заявке видеть итог обращения в БКИ (ok / нет истории /
недоступно) и ключевые фичи, анализировать это в Loki, и в дашборде «внешние
сервисы» подробно понимать, что происходит с бюро. Сейчас в логах есть только
решение (approved/declined), а на дашборде — базовый набор БКИ-панелей.

Работа делится на две части с разными путями поставки:
- **Часть A — код** (PR → деплой): структурный лог итога БКИ + метрика балла.
- **Часть B — живая Grafana** (правка через API): подробные БКИ-панели.

---

## Часть A. Код

### A1. Структурный лог итога БКИ по заявке

Одна дополнительная строка лога в ручке `create_application_v2`
(`app/src/server.py`), сразу после `decision = make_decision_v2(...)`, где
доступен `bki_outcome` (статус + фичи) и `decision`. `make_decision_v2` не
трогаем (она статус-строку не получает по дизайну).

```python
score = bki_outcome.features.score if bki_outcome.features else None
delinquency = (
    bki_outcome.features.has_current_delinquency
    if bki_outcome.features else None
)
logger.info(
    "Заявка %s — БКИ: %s (балл %s)",
    decision["application_id"], bki_outcome.status, score,
    extra={
        "event": "bki_outcome",
        "application_id": decision["application_id"],
        "bki_status": bki_outcome.status,      # ok | no_history | unavailable
        "bki_score": score,                    # int | None
        "bki_delinquency": delinquency,        # bool | None
        "decision": decision["status"],        # approved | declined
    },
)
```

JSON-логирование проекта (`logging_setup.JsonFormatter`) само выносит поля из
`extra` в JSON — они попадают в Loki как индексируемые поля.

**Loki:** `{service_name="petbank"} | json | event="bki_outcome"` →
фильтр/группировка по `bki_status`, `decision`, `bki_delinquency`, `bki_score`.

### A2. Метрика распределения балла БКИ

Новый Histogram в `app/src/metrics.py` — чтобы распределение баллов было видно
в Grafana нативно (а не считалось из логов):

```python
BKI_SCORE = Histogram(
    "petbank_bki_score",
    "Распределение балла БКИ по заявкам с полученным отчётом",
    buckets=(300, 400, 500, 550, 600, 650, 700, 750, 800, 850, 900),
)
```

Наблюдаем в `bki.get_report_with_retry` (или в ручке) при наличии балла:
`if outcome.features and outcome.features.score is not None:
BKI_SCORE.observe(outcome.features.score)`. Место — рядом с существующим
`BKI_RESULT.labels(...).inc()` в `bki.py`, где уже известен итог.

### Безопасность (Часть A)

В лог и метрику кладём только статус/балл/флаг — НИ паспорта, НИ сырого XML
(они остаются в таблице `bki_reports`). Новых персональных данных не появляется.

### Тесты (Часть A)

`tests/test_http_v2.py` через `caplog` — три сценария (уже написаны):
- `ok` с фичами → `bki_status="ok"`, `bki_score=702`, `bki_delinquency=False`, `decision="approved"`;
- `no_history` → `bki_status="no_history"`, `bki_score=None`, `bki_delinquency=None`, `decision="approved"`;
- `unavailable` → `bki_status="unavailable"`, `bki_score=None`, `decision="declined"`.

`tests/test_bki_client.py` — метрика `BKI_SCORE` наблюдается при наличии балла
(`_count` растёт на «ok» с фичами и НЕ растёт на `no_history`/`unavailable`).

---

## Часть B. Подробный дашборд «PetBank — внешние сервисы»

Дополнить блок БКИ до полной картины. Датасорсы: `prom-3` (Prometheus),
`loki-katya-3` (Loki). Правка живого дашборда `uid=petbank-external` через API,
перед сохранением — diff (правило работы с живой Grafana).

### Панели из Prometheus (метрики уже есть / A2)

1. Итоги обращений (ok / нет истории / недоступно), /с — стек. *(есть)*
2. Доля недоступности бюро, % — hero. *(есть)*
3. Доля «нет истории», % — **новая** (`no_history` / всего).
4. Латентность p50 / p95 / p99. *(есть)*
5. Латентность БКИ — **heatmap** по `petbank_external_call_seconds_bucket{service="bki"}` — **новая**.
6. Средняя длительность вызова — stat. *(есть)*
7. Отказы по БКИ: просрочка vs бюро недоступно, /с. *(есть)*
8. **Распределение балла БКИ** (из `petbank_bki_score`, A2) — **новая**: heatmap
   распределения по времени + timeseries p50/p90 балла (два запроса в одной панели).
9. Пирог итогов за период. *(есть)*

### Панели из Loki (на основе лога A1)

10. Решения по `bki_status` — **новая**: из `event="bki_outcome"`, разбивка
    approved/declined в разрезе ok / no_history / unavailable (сколько отказов
    дал каждый исход бюро).
11. Доля заявок с текущей просрочкой (`bki_delinquency=true`), % — **новая**.
12. Таблица логов итога БКИ — **новая**: `application_id`, `bki_status`,
    `bki_score`, `bki_delinquency`, `decision` (точечный разбор конкретных
    заявок — «видеть статус по заявке»).
13. Логи отказов/сбоев бюро — сырые строки. *(есть)*

---

## Порядок поставки

1. Часть A (код) — ветка `feat/bki-outcome-log`, PR → мерж → деплой. Метрика и
   лог начинают течь на проде.
2. Часть B (Grafana) — после того как метрика `petbank_bki_score` и лог
   `event="bki_outcome"` появятся на проде (иначе панели 8, 10–12 пустые).
   Правится живьём через API с показом diff.

## Вне скоупа

Полный набор фич БКИ в логе (max_dpd/overdue/inq_*) — они в `bki_reports`.
Изменение порога SLO-алерта p95 (обсуждается отдельно). Async-вынос БКИ с
горячего пути (этап S3 роадмапа).
