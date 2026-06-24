# 🧭 Состояние проекта PetBank

Где проект сейчас, как он сюда пришёл, что в ветках, что недоделано и куда расти.
Это «карта местности» после паузы — чтобы быстро понять, что происходит.

Содержание:

1. [Что сейчас на `main` (коротко)](#1-что-сейчас-на-main-коротко)
2. [История проекта (как развивался)](#2-история-проекта-как-развивался)
3. [Карта веток](#3-карта-веток)
4. [Локальная папка vs `main`](#4-локальная-папка-vs-main)
5. [Известные пробелы и техдолг](#5-известные-пробелы-и-техдолг)
6. [Куда расти (roadmap)](#6-куда-расти-roadmap)

---

## 1. Что сейчас на `main` (коротко)

`main` — это **рабочая, задеплоенная версия**. На ней есть всё:

- ✅ FastAPI + Uvicorn, Pydantic-валидация, Swagger UI на `/docs`;
- ✅ бизнес-правила: возраст 18–35, стоп-лист стран (Китай);
- ✅ JSON-логирование в stdout + `request_id` на каждый запрос;
- ✅ Prometheus-метрики на `/metrics` (HTTP + бизнес);
- ✅ Docker-образ + авто-деплой на VM через GitHub Actions (`docker run`);
- ✅ 4 набора тестов (логика, HTTP, логи, метрики).

Последний релиз на `main`: **Prometheus-метрики** (коммит `3940a62`).

---

## 2. История проекта (как развивался)

Проект рос итерациями. Для каждой крупной фичи есть спека и план в
`docs/superpowers/` — это «проектные документы», по которым делалась работа.

```
 stdlib            FastAPI          Pydantic         Docker          JSON-логи        Метрики
 http.server  ──►  + правила   ──►  + Swagger   ──►  контейнер  ──►  + request_id ──► /metrics
 (возраст≥18)      возраст/страна   (валидация)      + деплой        (middleware)     (Prometheus)
     │                 │                 │               │               │               │
     start         06-15            06-16           cf7a09f          06-18           3940a62
                                                  + docker-run                       ← main сейчас
                                                    (0751935)
```

| Этап | Что добавилось | Артефакты |
|---|---|---|
| **0. Старт** | Сервер на голой stdlib (`http.server`), одно правило «возраст ≥ 18», тесты, CI с деплоем через rsync + systemd | — |
| **1. FastAPI + правила** (06-15) | Правило `MAX_AGE = 35`, обязательное поле `country` со стоп-листом, миграция `http.server` → FastAPI+Uvicorn (тонкая обёртка) | `specs/2026-06-15-fastapi-age-country-rules-design.md`, `plans/2026-06-15-…` |
| **2. Pydantic + Swagger** (06-16) | Ручная валидация → Pydantic-модели, авто Swagger UI на `/docs`; формат ошибок `400 → 422` | `specs/2026-06-16-swagger-ui-pydantic-design.md`, `plans/2026-06-16-…` |
| **3. Логирование решений** | Текстовые бизнес-логи в `make_decision` | PR `feat/decision-logging` |
| **4. Docker** | `Dockerfile`, сборка образа в CI, публикация в `ghcr.io` | коммит `cf7a09f` |
| **5. Деплой через docker run** | Уход от systemd: `docker run --restart unless-stopped`; удалены systemd-юнит и sudoers | коммит `0751935` |
| **6. JSON-логирование** (06-18) | `logging_setup.py` (JsonFormatter, `request_id` через ContextVar), middleware логирования HTTP-запросов, `X-Request-ID` | `specs/2026-06-18-json-request-logging-design.md` |
| **7. Лимиты контейнера** | `--memory=512m --cpus=0.5` в деплое | коммит `3ec471b` |
| **8. Prometheus-метрики** (06-18) | `metrics.py`, `/metrics`, instrumentator, build-info | `specs/2026-06-18-prometheus-metrics-design.md` |

> Спека (`specs/`) = «что и почему делаем». План (`plans/`) = «по шагам, как
> делаем, с тестами». Это лучший источник, чтобы понять **мотивацию** каждой фичи.

---

## 3. Карта веток

```bash
git branch -a          # все ветки
git worktree list      # рабочие копии веток
```

### Влито в `main`
- `main` — основная, актуальная.
- `feat/decision-logging` — влита.
- Работа из остальных фич (FastAPI, Pydantic, Docker, docker-run, JSON-логи,
  метрики) **тоже уже в `main`** — она попала туда через серию коммитов/PR.

### Worktree-ветки (`.claude/worktrees/`)
- `worktree-feat+json-request-logging`
- `worktree-feat+metrics`

Их содержимое **совпадает с тем, что уже на `main`** (проверено: `server.py` в
`worktree-feat+metrics` идентичен `main`). То есть это **доделанная, уже влитая
работа** — worktree остались как «хвосты». Их можно убрать:

```bash
git worktree remove .claude/worktrees/feat+metrics
git worktree remove .claude/worktrees/feat+json-request-logging
```

### Старые feature-ветки (не влиты «как есть», но их работа в `main`)
`add-old-age-condition`, `feat/app-structure`, `feat/docker-copy-all`,
`feat/pydantic-swagger`, `feat/deploy-docker-run` — это **исторические ветки**.
Их изменения уже интегрированы в `main` другими коммитами, сами ветки —
устаревшие. Безопасно удалить (после того как убедишься, что не нужны):

```bash
git branch -d <ветка>     # -d не даст удалить неслитое; -D — форсом
```

> Прежде чем массово чистить ветки — убедись, что в них нет уникальной
> незакоммиченной идеи. Судя по диффам, всё ценное уже в `main`.

---

## 4. Локальная папка vs `main`

🔴 **Сейчас твоя рабочая папка переключена на `feat/deploy-docker-run` — это
снимок «до JSON-логов и метрик».** Поэтому локальные `server.py`, `Dockerfile`,
`ci.yml`, `CLAUDE.md` отличаются от `main`.

Что есть локально, но **не** в git (твои незакоммиченные эксперименты):
- `docker-compose.yml` — поднимает PostgreSQL 16. Приложение БД **не использует**;
  это задел на будущее. Файла нет на `main`.
- `.env`, `.env.example` — переменные для Postgres-compose. Не в git.
- правки в `CLAUDE.md` (локально добавлена строка про запрет merge-коммитов).

**Рекомендация:** перейти на `main`, чтобы видеть актуальный код (сначала сохрани
эксперименты):

```bash
git stash                       # или git add -A && git commit -m "wip: эксперименты"
git checkout main
git pull origin main
git stash pop                   # если прятала
```

Если `docker-compose.yml`/`.env.example` нужны — закоммить их отдельной веткой,
чтобы не потерять.

---

## 5. Известные пробелы и техдолг

Не баги, но стоит держать в голове:

| Тема | Суть | Где |
|---|---|---|
| **README отстаёт** | Таблица эндпоинтов не упоминает `/docs` и `/metrics`; «Файлы» не упоминают `logging_setup.py`/`metrics.py`; написано «Python 3.8+» (на деле 3.10+) | `README.md` |
| **`openapi.yaml` отстаёт** | Описывает старый формат ошибок `400` (`ErrorResponse`), хотя сейчас `422`; нет `/metrics`. Это legacy для Postman | `openapi.yaml` |
| **ПДн в логах** | Тело заявки (ФИО, телефон, ДР) пишется в лог открытым текстом — осознанно. Нужен ограниченный доступ/срок хранения, опц. маскирование | `server.py` middleware |
| **Plaintext HTTP** | `0.0.0.0:8000` без TLS. В проде — nginx + TLS | деплой |
| **`/metrics` без auth** | Так задумано (внутренний скрейп), но не должен торчать в интернет | `server.py` |
| **`docker` = root** | Деплой-аккаунт фактически рутовый на VM | `deploy/README.md` |
| **Postgres не подключён** | `docker-compose.yml` есть, но приложение БД не использует и файл не в git | локально |
| **Стейл-ветки/worktree** | Несколько устаревших веток и 2 worktree, чья работа уже в `main` | git |

---

## 6. Куда расти (roadmap)

Идеи на будущее (не обязательства, а направления):

- **Сохранение заявок в БД.** Postgres уже намечен в `docker-compose.yml` —
  подключить (например, через SQLAlchemy), сохранять решения, добавить эндпоинт
  истории. Это превратит сервис из stateless в полноценный.
- **Маскирование ПДн** в access-логах (одно место — сборка поля `body` в
  middleware).
- **TLS + reverse-proxy** (nginx) перед сервисом на проде.
- **Branch protection** на `main`: «Require status checks → Tests».
- **Привести в актуальное состояние `README.md` и `openapi.yaml`** (или вовсе
  отказаться от ручного `openapi.yaml` в пользу авто-схемы FastAPI).
- **Новые бизнес-правила** — по [чек-листу](CHECKLISTS.md#-добавить-или-изменить-бизнес-правило)
  (минимальная сумма, расширение стоп-листа, проверка телефона форматом и т.п.).
- **Чистка веток и worktree** — убрать устаревшее (см. [раздел 3](#3-карта-веток)).

---

Назад к началу: [INDEX.md](INDEX.md) · [архитектура](ARCHITECTURE.md) ·
[API](API.md) · [эксплуатация](OPERATIONS.md) · [чек-листы](CHECKLISTS.md).
