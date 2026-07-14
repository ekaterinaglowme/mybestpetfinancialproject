# Дизайн: асинхронный приём заявок и обработка пулом воркеров

**Дата:** 2026-07-14
**Статус:** на согласовании (ред. 2 — чистый async, без предварительного решения)
**Автор:** Екатерина (PetBank)
**Связано:** [journal external_service_calls](2026-07-14-external-service-calls-design.md) — вливается сюда (воркер пишет туда вызовы ЧС и БКИ). Этап **S3** роадмапа.

## 1. Зачем (бизнес-цель)

Сейчас `POST /applications/v2` синхронно ходит в БКИ (~1 сек, при недоступности —
секунды) прямо на горячем пути → раздувает latency, привязывает ответ клиенту к
скорости бюро (июльский инцидент с p95).

Хотим **мгновенный приём**: ручка принимает заявку, отвечает «принято» + номер, а
**все проверки и решение** уносятся в фон. Это снимает бюро с горячего пути и даёт
фундамент под RabbitMQ (следующий шаг S3).

Аналогия: окошко приёма только выдаёт талончик с номером; чёрный список, бюро и
решение делает бригада в бэк-офисе; клиент узнаёт решение по номеру.

## 2. Ключевые решения (брейншторм 2026-07-14, ред. 2)

1. **Ручка `POST /applications/v2` — только приём.** Валидация формата (pydantic) →
   создать заявку `status='pending'` → поставить задачу в очередь → **`202` «принято»
   + `application_id`**. Никаких бизнес-проверок на горячем пути (без «предварительного
   решения»).
2. **ВСЕ проверки — в воркере:** чёрный список, возраст, внутренняя история (активный
   заём / прошлый невозврат), БКИ → **единое решение** (одна функция, без раздвоения
   на предварительное/финальное).
3. **Durable-очередь в БД** (transactional outbox). RabbitMQ — следующий шаг,
   механизм держим сменным.
4. **Пул из N asyncio-воркеров** (worker pool), поднимается в `lifespan`. Набор
   заранее запущенных воркеров ждёт задач, свободный берёт задачу, обрабатывает,
   возвращается в пул. НЕ ОС-потоки: работа воркера (ЧС, БКИ) сетевая/асинхронная,
   потоки дали бы накладные расходы без выгоды. `WORKER_CONCURRENCY` = число воркеров.
5. **Внешние сервисы вызываются по ВСЕМ заявкам**, включая тех, кого завернёт
   чёрный список — ради сбора датасета скоркарты. Решение отражает все правила
   (отказ по ЧС остаётся отказом, но БКИ-данные всё равно собраны).
6. **Внешний сервис недоступен (ЧС или БКИ) → повтор с backoff.** Задача остаётся в
   очереди, счётчик попыток; после `TASK_MAX_ATTEMPTS` → заявка `declined` (fail-closed).
7. **Очередь — отдельная таблица `application_tasks`** (задача = «обработать заявку
   целиком»). Именно её заменит RabbitMQ.

## 3. Модель состояний

**`applications.status`:**
- `pending` — принято, ждёт обработки (в очереди / у воркера); НЕ финал;
- `approved` — одобрено (после обработки);
- `declined` — отказано (после обработки).

**`application_tasks.state`:**
- `pending` — ждёт обработки (создаётся для КАЖДОЙ заявки);
- `done` — обработана, решение записано;
- `failed` — внешний сервис недоступен после `TASK_MAX_ATTEMPTS`; заявка при этом
  финализируется `declined` (fail-closed).

## 4. Схема таблиц

### 4.1. Новая `application_tasks` (очередь)

| Колонка | Тип | Null | Смысл |
|---|---|---|---|
| `id` | BigInteger, PK, autoincrement | нет | номер задачи |
| `application_id` | Uuid, FK → `applications.application_id` | нет | какую заявку обработать |
| `state` | String | нет | `pending` / `done` / `failed` |
| `attempts` | Integer, default 0 | нет | сколько раз пытались |
| `next_attempt_at` | DateTime(tz), default now | нет | не брать раньше этого времени (backoff) |
| `created_at` | DateTime(tz), server_default now | нет | когда поставлена |
| `updated_at` | DateTime(tz) | да | последнее изменение |

Индекс `ix_application_tasks_ready` по `(state, next_attempt_at)` — выборка готовых.
Данные заявки (паспорт, дата рождения, ФИО) воркер берёт из `applications` по
`application_id` — в задаче не дублируем. `state` — свободная строка (без CHECK).

### 4.2. `external_service_calls` (журнал)

Как в [спеке журнала](2026-07-14-external-service-calls-design.md). Воркер пишет туда
**каждый** внешний вызов: `service='stoplist'` (чёрный список) и `service='bki'`
(бюро), с `payload={request, response}`, `status`, `http_status`, `latency_ms`.
`payload` — JSON-блоб на паттерне проекта:
`JSON().with_variant(JSONB(), "postgresql")` (JSONB на Postgres, JSON на SQLite).
Таблица и `save_external_call` создаются в рамках этой работы.

### 4.3. Изменение `applications`

Новое значение `status='pending'`. Заявка теперь сохраняется при приёме со
`status='pending'`, `reasons=[]`; решение и причины проставляет воркер. `status` —
свободная строка (CHECK на `applications` нет) — сверить при плане.

## 5. Компоненты и файлы

- **`server.py`** — ручка `create_application_v2` = только приём (202); новая
  `GET /applications/{application_id}`.
- **`decision.py`** (новый, выносим из `server.py`) — единая функция решения
  `decide(...)` (бывшая `make_decision_v2`, без генерации id/received_at).
- **`worker.py`** (новый) — пул воркеров: `run_worker_pool(n)`, `worker_loop(worker_id)`,
  `process_one_task(session, task)`.
- **`repository.py`** — `enqueue_application_task`, `claim_application_task`,
  `complete_task`, `reschedule_task`, `fail_task`, `finalize_application_decision`,
  `get_application`, `save_external_call`.
- **`models.py`** + миграция — `ApplicationTask`, `ExternalServiceCall`, статус `pending`.
- **`bki_parse.py`** — хелпер `xml_to_dict` для журнала.
- **`black_list.py`** — вызов ЧС теперь из воркера; клиент возвращает и «сырой» ответ
  для журнала (сейчас отдаёт только bool — расширить при плане).

## 6. Поток

```mermaid
sequenceDiagram
    participant C as Клиент
    participant H as Ручка v2 (приём)
    participant DB as БД (applications + application_tasks)
    participant W as Пул воркеров (asyncio ×N)
    C->>H: POST /applications/v2
    H->>DB: create application (pending) + enqueue task
    H-->>C: 202 «принято» + application_id
    Note over H,W: дальше — в фоне, клиент уже получил «принято»
    W->>DB: claim task (state=pending, next_attempt_at<=now, FOR UPDATE SKIP LOCKED)
    W->>W: чёрный список + возраст + история + БКИ → единое решение
    W->>DB: решение (approved/declined) + bki_report + журнал (ЧС и БКИ) + заём; task done
    Note over W,DB: внешний сервис недоступен → attempts++, next_attempt_at=now+backoff;<br/>после TASK_MAX_ATTEMPTS → task failed, заявка declined (fail-closed)
    C->>H: (позже) GET /applications/{application_id}
    H-->>C: статус (pending / approved / declined) + reasons
```

## 7. Интерфейсы (сигнатуры)

```python
# decision.py
def decide(application, *, in_black_list, black_list_check_failed,
           bki, bki_check_failed, has_active_loan, has_prior_default) -> tuple[str, list[str]]:
    """Единое решение по данным заявки + результатам проверок.
    Возвращает (status, reasons): status 'approved' если reasons пуст, иначе 'declined'."""

# repository.py
async def enqueue_application_task(session, *, application_id) -> None: ...
async def claim_application_task(session) -> ApplicationTask | None:
    """Одна готовая задача: state='pending' AND next_attempt_at<=now,
    FOR UPDATE SKIP LOCKED LIMIT 1 (два воркера не возьмут одну)."""
async def complete_task(session, task) -> None: ...                 # state='done'
async def reschedule_task(session, task, *, delay_s: float) -> None:
    """attempts++, next_attempt_at=now+delay (пока attempts<max)."""
async def fail_task(session, task) -> None: ...                     # state='failed'
async def finalize_application_decision(session, *, application_id, status, reasons) -> None: ...
async def get_application(session, application_id) -> Application | None: ...

# worker.py
async def process_one_task(session, task) -> None:
    """Загрузить заявку → ЧС + возраст + история + БКИ → decide → записать решение,
    bki_report, журнал, заём; complete/reschedule/fail в зависимости от исхода."""
async def worker_loop(worker_id: int) -> None:
    """Цикл одного воркера: claim задачу; нет задач — пауза WORKER_POLL_INTERVAL."""
async def run_worker_pool(n: int) -> None:
    """Поднять N worker_loop как asyncio-задачи; вызывается из lifespan."""
```

Конфиг (env, дефолты в коде): `WORKER_CONCURRENCY`=4,
`WORKER_POLL_INTERVAL_SECONDS`=0.5, `TASK_MAX_ATTEMPTS`=5, `TASK_BACKOFF_SECONDS`=30.

## 8. Надёжность, ошибки, идемпотентность

- **Рестарт контейнера:** задачи в БД; незавершённые (`pending`) подхватятся пулом
  после старта. Незакоммиченная транзакция воркера откатывается — задача снова
  `pending`. Ничего не теряется.
- **Двойная обработка:** `FOR UPDATE SKIP LOCKED` — воркеры из пула не возьмут одну
  задачу. Запись решения + `bki_report` + `state=done` — в одной транзакции.
- **Повторные вызовы безопасны:** ЧС и запрос отчёта БКИ — read-only.
- **Журнал vs bki_report при повторах:** `external_service_calls` пишет **каждый**
  вызов (в т.ч. неудачные попытки — в этом ценность журнала). `bki_report` (1:1 с
  заявкой) и решение пишутся **один раз** — при завершении задачи (`done`/`failed`),
  не при промежуточных повторах, иначе дубль по PK.
- **Внешний сервис недоступен:** попытка → `reschedule_task` (attempts++, backoff).
  После `TASK_MAX_ATTEMPTS` → `fail_task` + заявка `declined` (fail-closed, причина
  «не удалось проверить …»).
- **Пул и БД:** внешние вызовы (ЧС, БКИ) идут ВНЕ открытой транзакции; короткая
  транзакция — только на запись результата (не держим соединение пула во время сети).

## 9. Что это ломает

- **Контракт `POST /applications/v2`: 200 → 202.** Тело — подтверждение приёма
  (`status='pending'`, `application_id`), НЕ решение. Обновить:
  - blackbox-тесты `tests_blackbox/` (ждут 200 + готовый статус);
  - README-диаграмму (mermaid потока v2);
  - потребителей, если ждут синхронный вердикт.
- Появляется **`GET /applications/{application_id}`** — узнать статус/решение.
- **Заём выдаётся воркером** после `approved`, не в ручке.

## 10. Границы объёма

**Входит:**
- Таблицы `application_tasks` и `external_service_calls` + миграция(и).
- Ручка-приём (202) + `GET /applications/{id}`.
- `decision.py` (единая `decide`), `worker.py` (пул), запуск пула в `lifespan`.
- repository-функции очереди/финализации + `save_external_call`; расширение
  клиента ЧС для «сырого» ответа.
- Юниты + обновление blackbox под новый контракт.

**НЕ входит (следующие шаги):**
- **RabbitMQ** — замена механизма очереди.
- Вебхук клиенту о готовности решения (пока только `GET`).
- LISTEN/NOTIFY вместо поллинга; автоскейл пула.

## 11. Тест-план (юниты, SQLite; + blackbox)

- Ручка: `POST` → `202`, `status='pending'`, заявка и задача созданы, никакие
  внешние сервисы НЕ вызваны.
- `decide`: возраст<18 / ЧС / активный заём / прошлый невозврат / просрочка БКИ /
  недоступность → `declined` с нужной причиной; всё чисто → `approved`; «нет
  истории» БКИ (Код=3) — не влияет.
- repository: `enqueue` создаёт `pending`; `claim` берёт готовую и не берёт с
  `next_attempt_at` в будущем; `reschedule`/`fail`/`complete` меняют state;
  `finalize_application_decision` пишет статус+reasons.
- `process_one_task` (замокать ЧС и `get_report_with_retry`): approved/declined/
  недоступность-повтор; проверить запись `bki_report`, `external_service_calls`
  (обе службы), выдачу займа при approved, идемпотентность при повторе.
- `GET /applications/{id}` до обработки (`pending`) и после (`approved`/`declined`).
- blackbox US: `POST` → `202` → дождаться обработки пулом → `GET` финал.

## 12. Открытые вопросы / дефолты (поправить при ревью)

- `id` таблиц — автономер (BigInteger).
- Статусы/`state` — англ. (`pending`/`approved`/`declined`, `pending`/`done`/`failed`).
- `WORKER_CONCURRENCY`=4, `TASK_MAX_ATTEMPTS`=5, backoff=30с — прикидки, калибруются.
- Имя ветки `feat/external-service-journal` осталось от журнала; при PR переименовать
  в `feat/async-application-processing` (журнал — часть этой работы).

---

## 13. MVP на 1 день (2026-07-14) — сбор БКИ-статистики

Тонкий срез под дедлайн. Цель: снять БКИ с горячего пути и **копить БКИ-данные**
для скоркарты («уровень доверия»), не строя пока очередь/пул.

- **Решение — только возраст + чёрный список** (fail-closed при недоступности ЧС).
  БКИ **и** внутренняя история из решения УБРАНЫ. Ответ — `200` + решение
  (контракт НЕ меняется). Заём выдаётся при `approved` (как сейчас).
- После сохранения заявки — **FastAPI `BackgroundTasks`**: фоново
  `get_report_with_retry(passport)` → `save_bki_report(...)` в **отдельной**
  db-сессии (HTTP-запрос уже завершён). Данные копятся в существующей `bki_reports`.
- **НЕ durable:** рестарт теряет незавершённый фоновый сбор — для статистики терпимо.
- Анализ (IV / бины / корреляция балла с возвратом) — потом в SQL/Superset поверх
  `bki_reports`.

**Следующий шаг** (§1–12 этой спеки): durable-очередь `application_tasks` + пул
воркеров + журнал `external_service_calls` → замена `BackgroundTasks`; затем RabbitMQ.

**Файлы MVP:** `server.py` (решение без БКИ/истории; планирование фонового сбора),
функция сбора (в `bki.py`), тесты. `decision.py`/`worker.py`/новые таблицы — НЕ в MVP.
