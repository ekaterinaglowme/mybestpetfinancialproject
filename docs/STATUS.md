# 🧭 PetBank — как всё работает (статус)

Актуальный технический и бизнес-снимок системы. Источник правды — боевой код на
`origin/main` (то, что задеплоено на VM). Для ежедневной выгрузки в PDF см.
[`docs/status/`](status/README.md).

> Обновлено по коду `origin/main`. Если читаешь после паузы — сверься с `git log origin/main`.

Содержание:

1. [Бизнес: что это и зачем](#1-бизнес-что-это-и-зачем)
2. [Архитектура и поток запроса](#2-архитектура-и-поток-запроса)
3. [Тайминги, пороги, лимиты](#3-тайминги-пороги-лимиты)
4. [Метрики на /metrics](#4-метрики-на-metrics)
5. [Инфраструктура и деплой](#5-инфраструктура-и-деплой)
6. [Мониторинг и алерты](#6-мониторинг-и-алерты)
7. [Честные пробелы](#7-честные-пробелы)

---

## 1. Бизнес: что это и зачем

PetBank — микросервис **приёма кредитных заявок и выдачи микрозаймов** (учебный стенд).
Жизненный цикл:

1. Клиент шлёт заявку → сервис принимает **решение** `approved` / `declined`.
2. Если одобрено **и указана сумма** → автоматически **выдаётся заём** (тот же `application_id`).
3. По займу можно смотреть **статус и текущий долг** и **гасить** его.

**Бизнес-правила решения** (для одобрения нужны обе проверки):

| Правило | Деталь | Причина отказа (метрика) |
|---|---|---|
| Возраст | `≥ 18` лет (`MIN_AGE`) | `age_below_min` |
| Чёрный список паспортов | паспорт **не** в стоп-листе (внешний сервис СтопЛист) | `black_list` |
| Доступность проверки | СтопЛист недоступен → **fail-closed** (отказ) | `black_list_check_unavailable` |

**Заём — дисконтная ставка** (дольше держишь — ниже % к телу):

| Возраст займа | Ставка к телу |
|---|---|
| 0–7 дней | **10%** |
| 8–30 дней | **5%** |
| > 30 дней | **2%** |

Долг = `тело × (1 + ставка)`. Цель займа (`loan_purpose`) — только `покупка` или `перекредитование`.

---

## 2. Архитектура и поток запроса

**Стек:** FastAPI + Uvicorn, Python 3.14-slim, Pydantic-валидация, SQLAlchemy async +
asyncpg, PostgreSQL 16, httpx. Всё в Docker под непривилегированным `appuser`.

**Эндпоинты:**

| Метод / путь | Что делает |
|---|---|
| `POST /applications/v2` | подать заявку → решение (v1 удалён в #39) |
| `GET /loans/{id}` | статус и текущий долг займа |
| `POST /loans/{id}/repay` | погасить заём (409, если уже отдан) |
| `GET /health` | жив ли сервис |
| `GET /` · `GET /docs` | справка · Swagger UI |
| `GET /metrics` | Prometheus-метрики |

**Поток обработки заявки** (порядок не случаен):

```
запрос → [middleware: rate limit] → [middleware: JSON-лог + request_id]
   → Pydantic-валидация (иначе 422)
   → 1) ЧЁРНЫЙ СПИСОК (внешний вызов) — ДО открытия сессии БД
   → 2) make_decision_v2 (возраст + чёрный список) → approved/declined + метрики
   → 3) ОДНА транзакция БД:
        get_or_create_user → save_application → (если approved+amount) create_loan
   → ответ
```

Ключевой приём: **чёрный список дёргается до захвата соединения из пула БД** — иначе под
залпом заявок соединение висело бы открытым весь внешний вызов и пул копил бы очередь.
Запись в БД — **одной транзакцией** (commit при успехе, rollback при ошибке → 500
`Ошибка сохранения заявки`).

**Данные (PostgreSQL):**

- `users` — идемпотентность по `UniqueConstraint(ФИО + ДР + телефон)` → один человек не плодит дубли.
- `applications` (1:N к user) — сумма, статус, причины (JSONB), ПДн заявки v2.
- `loans` — заём по `application_id`: тело, дата выдачи, дата возврата.

---

## 3. Тайминги, пороги, лимиты

| Параметр | Значение | env-override | Где |
|---|---|---|---|
| Мин. возраст | **18 лет** | — | `server.py` `MIN_AGE` |
| **Таймаут чёрного списка** | **0.8 с** | `BLACK_LIST_TIMEOUT_SECONDS` | `black_list.py` |
| URL чёрного списка | `http://212.147.238.3:8090` | `BLACK_LIST_URL` | `black_list.py` |
| **Пул БД** | size **20** + overflow **10** (=до 30) | `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `db.py` |
| **Ожидание соединения из пула** | **5 с** | `DB_POOL_TIMEOUT_SECONDS` | `db.py` |
| **Таймаут одного SQL** | **5 с** | `DB_COMMAND_TIMEOUT_SECONDS` | `db.py` |
| pre-ping соединений | вкл | — | `db.py` |
| **Rate limit** | **100 rps**, burst **100** → 429 + `Retry-After` | `RATE_LIMIT_RPS` / `_BURST` | `server.py` |
| Тело запроса в лог | до **10240 байт** | — | `server.py` |
| **Docker healthcheck** | interval **30s**, timeout **3s**, start **5s**, retries **3** | — | `Dockerfile` |
| Лимиты контейнера | **512 МБ** RAM, **0.5** CPU | — | CI deploy |
| Пауза перед health после деплоя | **10 с** | — | `ci.yml` |

---

## 4. Метрики на /metrics

**HTTP (авто, prometheus-fastapi-instrumentator):** `http_requests_total{handler,status}`,
`http_request_duration_seconds_*` — RED по ручкам.

**Бизнес-метрики (`metrics.py`):**

| Метрика | Тип | Лейблы / детали |
|---|---|---|
| `petbank_decisions_total` | counter | `status` (approved/declined), `country` (сейчас `-`) |
| `petbank_rejection_reasons_total` | counter | `reason`: age_below_min / black_list / black_list_check_unavailable |
| `petbank_application_amount_rub` | histogram | бакеты 10k · 50k · 100k · 250k · 500k · 1M · ∞ |
| `petbank_rate_limited_total` | counter | заявки, отбитые лимитером (429) |
| `petbank_db_write_seconds` | histogram | `operation` (напр. `repay`) |
| `petbank_db_transaction_seconds` | histogram | время commit на запрос |
| `petbank_external_call_seconds` | histogram | `service` (`black_list`), бакеты до ~0.8 с |
| `petbank_black_list_phase_seconds` | histogram | `phase`: request / response (две фазы вызова) |
| `petbank_app_info` | info | `version`, `commit` (из CI build-arg) |

---

## 5. Инфраструктура и деплой

**VM `212.147.238.3` — всё на одной машине:** app `:8000`, Postgres `:5432`,
Prometheus `:9090` (закрыт фаерволом), Grafana `:3000`, Loki. Снаружи открыты только
`22 / 8000 / 3000`. Диск — общий бутылочник (его переполнение уронило дашборды 2026-06-30).

**CI/CD (GitHub Actions):**

- **PR в main:** `Tests` (pytest) + `Integration` (blackbox на реальном Docker-стеке) +
  `Build` (сборка образа без публикации).
- **Push в main:** сборка → публикация образа в `ghcr.io` → **деплой на VM** по SSH:
  `docker pull` + пересоздание контейнера
  (`--restart unless-stopped --memory=512m --cpus=0.5 -p 8000:8000`) → `sleep 10` →
  health-check. `concurrency: deploy-production` — два деплоя не гоняются. systemd нет,
  автоподъём после падения/ребута даёт сам Docker.

---

## 6. Мониторинг и алерты

**Дашборды (Grafana, orgId 3 «Katya»):**

- **PetBank — бизнес-метрики** (`petbank-business`, 21 панель): решения, доля одобрения,
  причины отказов, суммы (p50/p90/p99 + heatmap), HTTP по ручкам, логи (Loki). Датасорсы:
  Prometheus `prom-3`, Loki `loki-katya-3`. Refresh 10s.
- **FastAPI Observability** (community #22676): HTTP RED + CPU/RAM.

**Алерты** (как код в `grafana/provisioning/alerting/`, см. PR про grafana):

| Алерт | Условие | Eval / `for` | noData | Severity |
|---|---|---|---|---|
| **`up == 0`** (petbank-up-down) | сервис не отвечает на scrape | eval **30s**, for **1m** | **Alerting** (серия пропала = тоже падение) | critical |
| **Диск `< 15%`** (petbank-disk-low) | мало места на `/` VM | eval **1m**, for **5m** | **NoData** (нет node_exporter = не паникуем) | warning |

> Disk-алерт **требует node_exporter** на VM. Уведомления реально пойдут только после
> привязки contact point.

---

## 7. Честные пробелы

- **Чёрный список на стенде нестабилен** — может отдавать `black_list_check_unavailable`
  вместо ответа (fail-closed → лишние отказы).
- **Алерты ещё не залиты** в живую Grafana + нет contact point → уведомления пока не приходят.
- **Plaintext HTTP** на `0.0.0.0:8000`, отдаёт ПДн; `/metrics` без auth — для учебного
  стенда ок, для прода — nginx + TLS.
- **node_exporter** для disk-алерта — наличие не подтверждено.
- **`docker` = root** на VM (деплой-аккаунт в группе docker).

---

Назад: [INDEX.md](INDEX.md) · [архитектура](ARCHITECTURE.md) · [API](API.md) ·
[эксплуатация](OPERATIONS.md) · [чек-листы](CHECKLISTS.md) · [PDF-выгрузка](status/README.md).
