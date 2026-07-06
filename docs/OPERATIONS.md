# ⚙️ Эксплуатация PetBank

Как запускать локально, тестировать, собирать Docker-образ, как устроен CI/CD и
деплой на VM, как смотреть логи и метрики на проде. Описывается ветка `main`.

Содержание:

1. [Локальный запуск](#1-локальный-запуск)
2. [Тесты](#2-тесты)
3. [Docker локально](#3-docker-локально)
4. [CI/CD-пайплайн](#4-cicd-пайплайн)
5. [Деплой на VM](#5-деплой-на-vm)
6. [Эксплуатация на проде: логи и метрики](#6-эксплуатация-на-проде-логи-и-метрики)
7. [Git-workflow](#7-git-workflow)
8. [Траблшутинг](#8-траблшутинг)

---

## 1. Локальный запуск

**Требования:** Python 3.10+ (в коде используется синтаксис `X | None` и
`list[str]`). В CI и Docker-образе — Python 3.14.

> README проекта говорит «Python 3.8+» — это неточно, на 3.8/3.9 код не
> импортируется. Ориентируйся на 3.10+ (а лучше 3.14, как на проде).

```bash
# 1. (рекомендуется) виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. зависимости для запуска
pip install -r requirements.txt

# 3. запуск
python server.py                 # порт 8000
python server.py 8080            # другой порт
```

После старта в консоли:

```
PetBank запущен: http://localhost:8000  (Ctrl+C — остановить)
Swagger UI:       http://localhost:8000/docs
Правило одобрения: возраст 18-35 лет, страна не в стоп-листе (китай)
```

В **PyCharm** можно просто нажать зелёную кнопку **Run** на `main.py`.

Порт берётся в порядке приоритета: аргумент CLI → env `PORT` → `8000`.

---

## 2. Тесты

```bash
pip install -r requirements-dev.txt   # ставит и прод-зависимости, и pytest+httpx
pytest -q                             # запустить все тесты
pytest tests/test_decision.py -v      # только юнит-тесты бизнес-логики
pytest -k metrics                     # по подстроке имени
```

Конфиг pytest — в `pyproject.toml`: `pythonpath = ["."]` (чтобы `import server`
работал из корня), `testpaths = ["tests"]`.

### Что покрывают тесты

| Файл | Слой | Что проверяет |
|---|---|---|
| `tests/test_decision.py` | Чистая логика, без HTTP | `calculate_age` (включая 29 февраля и границы), валидацию `ApplicationRequest`, `make_decision` (одобрение/отказ по возрасту и стране, формирование `full_name`) |
| `tests/test_http.py` | HTTP через `TestClient` | `/health`, `/`, одобрение/отказ, **422** при невалидном теле и битом JSON, **404** на неизвестный путь |
| `tests/test_logging.py` | Логи + middleware | `JsonFormatter` (валидный JSON, кириллица, `request_id`), middleware (метаданные, **полное тело в логе**, фоллбэк на текст при битом JSON, заголовок `X-Request-ID`, корреляция бизнес- и access-логов) |
| `tests/test_metrics.py` | Метрики | `/metrics` отдаёт текст Prometheus, наличие `petbank_decisions_total`, `petbank_rejection_reasons_total`, гистограммы сумм, RED-метрик и `petbank_app_info` |

`TestClient` гоняет ASGI-приложение **in-process** — реальный сокет не
поднимается, тесты быстрые.

> Метрики глобальны и накапливаются между тестами — тесты проверяют **наличие**
> нужных строк/лейблов, а не точные значения.

---

## 3. Docker локально

```bash
# собрать образ
docker build -t petbank:local .

# (опционально) с версией/коммитом для метрики petbank_app_info
docker build --build-arg GIT_COMMIT=$(git rev-parse HEAD) -t petbank:local .

# запустить
docker run --rm -p 8000:8000 petbank:local

# проверить
curl http://localhost:8000/health
```

Про образ (`Dockerfile`):

- база `python:3.14-slim`;
- сначала ставятся зависимости (слой кешируется, пока не менялся
  `requirements.txt`), потом копируется код (`server.py main.py metrics.py
  logging_setup.py`);
- работает под **непривилегированным** пользователем `appuser` (не root);
- `ENV PYTHONUNBUFFERED=1` — логи сразу видны (без буферизации);
- `HEALTHCHECK` дёргает `/health` через `python`-urllib (в slim-образе нет
  `curl`);
- `CMD ["python", "main.py"]`.

`.dockerignore` исключает из образа всё лишнее (тесты, docs, `.git`, venv, `.env`
и т.п.) — в образ попадает только рантайм-код.

---

## 4. CI/CD-пайплайн

Файл: `.github/workflows/ci.yml`. Имя workflow — **CI/CD**.

**Триггеры:** `push` в `main` и `pull_request` в `main`.

Три job'а, последовательно зависимых:

```
   push/PR в main
        │
        ▼
   ┌──────────┐   зелёный   ┌──────────────┐   зелёный   ┌──────────────┐
   │  test    │ ──────────► │   docker     │ ──────────► │   deploy     │
   │ (pytest) │             │ (build образ)│             │ (на VM)      │
   └──────────┘             └──────────────┘             └──────────────┘
                            push образа в ghcr            только при push
                            только при push в main        в main
```

### job `test`
- Python 3.14 → `pip install -r requirements-dev.txt` → `pytest -q`.
- Гоняется и на PR, и на push.

### job `docker` (needs: test)
- Buildx; `docker/metadata-action` проставляет теги: `latest` (только для дефолтной
  ветки) и `sha-<commit>`.
- Передаёт build-arg `GIT_COMMIT=${{ github.sha }}` (для метрики `petbank_app_info`).
- **На PR** — только собирает образ (проверка, что `Dockerfile` собирается),
  **не публикует**.
- **На push в `main`** — логинится в `ghcr.io` (встроенный `GITHUB_TOKEN`,
  право `packages: write`) и пушит образ.
- Кеш слоёв — через `type=gha`.

### job `deploy` (needs: test, docker)
- Только при `push` в `main` (на PR не запускается).
- `concurrency: deploy-production` — два деплоя одновременно не пойдут.
- По SSH на VM выполняет одной командой:
  `docker login ghcr.io` → `docker pull …:latest` → `docker rm -f petbank` →
  `docker run -d --restart unless-stopped --name petbank --memory=512m --cpus=0.5 -p 8000:8000 …`
  → `sleep 10` → `curl /health` → `curl /ready` → `docker image prune -f` → `docker logout`.
- `curl /ready` ловит выкатку со сломанной связностью к БД (неверные `DB_*`):
  без него деплой зелёный, пока прод сыплет 500-ми.
- Если `pull` упадёт — старый контейнер не трогается, прод продолжает работать.

**Итог:** смёржила PR в `main` → если тесты зелёные, образ собрался и
опубликовался, а на VM автоматически поднялся свежий контейнер. На самих PR
деплоя нет.

---

## 5. Деплой на VM

Полная инструкция — в [`deploy/README.md`](../deploy/README.md). Здесь — суть.

**Образ:** `ghcr.io/ekaterinaglowme/mybestpetfinancialproject:latest` (приватный).

**Схема:** на VM крутится Docker-контейнер `petbank`, слушает `:8000`. Автозапуск
после ребута/падения обеспечивает сам Docker (`--restart unless-stopped`) —
**systemd не используется**.

### Что нужно один раз настроить на сервере
- Установить Docker Engine; добавить пользователя `deploy` в группу `docker`
  (`usermod -aG docker deploy`).
- Завести SSH-доступ для `deploy` (публичный ключ в `authorized_keys`).
- Фаервол: открыть SSH и `8000/tcp`.

### GitHub Secrets / Variables (Settings → Secrets and variables → Actions)

| Имя | Тип | Значение |
|---|---|---|
| `SSH_HOST` | Variable | IP виртуалки (`212.147.238.3`) |
| `SSH_USER` | Variable | `deploy` |
| `SSH_KEY` | Secret | приватный deploy-ключ (ed25519, целиком) |

Вход в ghcr — встроенным `GITHUB_TOKEN`, отдельный токен не нужен.

### Управление контейнером на VM

```bash
docker ps                 # запущен ли petbank
docker logs -f petbank    # логи (JSON-строки)
docker restart petbank    # перезапуск
docker rm -f petbank      # остановить и удалить
```

> ⚠️ Членство в группе `docker` фактически = root на машине. Для учебного стенда
> ок; в проде деплой-аккаунт стоит изолировать. Сервис отдаёт ПДн по plaintext
> HTTP — в проде нужен reverse-proxy (nginx) + TLS.

---

## 6. Эксплуатация на проде: логи и метрики

### Логи

JSON-строки в stdout контейнера:

```bash
docker logs -f petbank                          # живой поток
docker logs petbank | grep '"status":"declined"' # все отказы
docker logs petbank | grep '7c9e…'              # всё по одному request_id
```

Каждый запрос даёт минимум две связанные строки (бизнес-лог + access-лог) с общим
`request_id`. Формат и поля — в [ARCHITECTURE.md](ARCHITECTURE.md#логи-структурные-json).

### Метрики

`GET /metrics` отдаёт Prometheus-формат. Предполагается, что их скрейпит
внешний Prometheus заказчика и строит дашборды в Grafana.

**Готовые дашборды в Grafana** (демо-инстанс `212.147.238.3:3000`, орг «Katya» / orgId 3):

- **[PetBank — бизнес-метрики](http://212.147.238.3:3000/d/petbank-business?orgId=3)** —
  решения, доля одобрения, страны, причины отказов, суммы заявок, HTTP по эндпоинтам и логи.
- [FastAPI Observability](http://212.147.238.3:3000/d/fea3x93t76328c/fastapi-observability?orgId=3) —
  HTTP RED + CPU/RAM (community-дашборд [grafana.com #22676](https://grafana.com/grafana/dashboards/22676/)).

```bash
curl http://<vm>:8000/metrics            # сырой текст метрик
curl -s http://<vm>:8000/metrics | grep petbank_   # только бизнес-метрики
```

Полезные метрики для дашбордов:
- `petbank_decisions_total{status,country}` — поток одобрений/отказов;
- `petbank_rejection_reasons_total{reason}` — структура отказов;
- `http_request_duration_seconds_*` — латентность (RED);
- `petbank_app_info{version,commit}` — какая версия задеплоена.

---

## 7. Git-workflow

Правила — в [`CLAUDE.md`](../CLAUDE.md):

- Перед новой веткой — обновиться от main: `git fetch origin && git rebase origin/main`.
- Деплой на VM — **только при push в `main`** (после зелёных тестов). Промежуточные
  коммиты в feature-ветке безопасны (деплой не триггерят).
- Ветки именуются по смыслу: `feat/...`, `fix/...`, `ci/...`, `docs/...`.

**Worktree.** В проекте используются git worktree — отдельные рабочие копии веток
в `.claude/worktrees/`. Это позволяет вести несколько фич параллельно, не
переключая основную папку. Посмотреть:

```bash
git worktree list
```

Подробнее о текущих ветках и worktree — в [STATUS.md](STATUS.md#карта-веток).

---

## 8. Траблшутинг

| Симптом | Вероятная причина | Что делать |
|---|---|---|
| `ModuleNotFoundError: fastapi` при запуске | Не установлены зависимости | `pip install -r requirements.txt` |
| `import server` падает с SyntaxError | Python < 3.10 | Поставить Python 3.10+ (синтаксис `X \| None`) |
| Тесты не находят `server` | Запуск не из корня репозитория | Запускать `pytest` из корня (там `pyproject.toml` с `pythonpath`) |
| `/applications` отвечает 422 | Невалидное тело (нет обязательного поля, плохая дата, отрицательная сумма) | Сверить тело с [API.md → валидация](API.md#4-валидация-полей) |
| Заявка `declined`, хотя данные верные | Сработало бизнес-правило | Смотреть `reasons` в ответе и бизнес-лог по `request_id` |
| Порт 8000 занят | Уже запущен другой процесс/контейнер | Сменить порт (`python server.py 8080`) или освободить |
| CI-деплой упал на health-check | Контейнер не поднялся / `/health` не отвечает | `docker logs petbank` на VM; проверить, что образ спуллился |
| `/metrics` пустоват | Ещё не было трафика | Сделать пару запросов; метрики наполняются по факту |
| Локальный код не похож на этот документ | Локальная папка отстала от `main` | См. [INDEX.md → синхронизация](INDEX.md#️-сначала-прочти-это-твоя-локальная-папка-отстала-от-main) |

---

Дальше: [чек-листы под задачи](CHECKLISTS.md) · [состояние проекта](STATUS.md) ·
[архитектура](ARCHITECTURE.md).
