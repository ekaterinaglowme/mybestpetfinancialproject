# Дизайн: персистентность заявок в PostgreSQL

**Дата:** 2026-06-25
**Статус:** на ревью
**Область:** код приложения + локальная разработка + прод-развёртывание + ручная CI-миграция (один спек)

---

## 1. Контекст и цель

Сейчас PetBank — **stateless**: `POST /applications` синхронно считает решение и
возвращает его, нигде не сохраняя (см. [ARCHITECTURE.md](../../ARCHITECTURE.md) §1, §9).
`docker-compose.yml` с `postgres:16` существует, но приложением не используется.

**Цель:** при каждой заявке сохранять в PostgreSQL **пользователя** и **заявку** в
двух связанных таблицах, не теряя текущее поведение ручки (решение по-прежнему
возвращается в ответе).

## 2. Принятые решения (итог брейнсторминга)

| # | Решение | Выбор |
|---|---|---|
| 1 | Идентификация пользователя | UNIQUE по связке `last_name + first_name + middle_name + birth_date + phone` |
| 2 | Что хранить в заявке | Вход (сумма, страна, …) **+** результат (status, reasons, application_id, received_at) |
| 3 | Поведение при недоступности БД | **500** — сохранение и ответ единое целое, сбой не маскируем |
| 4 | Стек доступа к БД | SQLAlchemy 2.x (async) + asyncpg |
| 5 | Миграции | Alembic; на прод накатываются **вручную** отдельной CI-job (`workflow_dispatch`) |
| 6 | Границы | Один спек: код + локалка + прод + CI |
| 7 | Тесты | SQLite в памяти (aiosqlite) |

**Связь таблиц:** один пользователь → много заявок (1:N).

## 3. Схема БД

```
   users                              applications
 ┌────────────────────────────┐    ┌────────────────────────────────┐
 │ id           PK  Uuid       │◄───│ user_id        FK → users.id   │
 │ last_name        str        │ 1  │ application_id PK  Uuid        │
 │ first_name       str        │  ∞ │ amount         Numeric(12,2)?  │
 │ middle_name      str ''     │    │ country        str             │
 │ birth_date       Date       │    │ status         str             │
 │ phone            str        │    │ reasons        JSON (JSONB)    │
 │ created_at       tstz now() │    │ received_at    tstz            │
 │                             │    │ created_at     tstz now()      │
 │ UNIQUE(last_name,first_name,│    └────────────────────────────────┘
 │   middle_name,birth_date,   │
 │   phone)                    │
 └────────────────────────────┘
```

> Легенда схемы: `PK` — первичный ключ, `FK` — внешний ключ, `?` — nullable,
> `''` — `NOT NULL DEFAULT ''`, `now()` — `server_default` текущего времени.

**Типы (кросс-совместимость Postgres ⇄ SQLite):**

| Поле | Тип SQLAlchemy | На Postgres | На SQLite (тесты) |
|---|---|---|---|
| `id`, `application_id`, `user_id` | `Uuid` | `UUID` | `CHAR(32)` |
| `reasons` | `JSON().with_variant(JSONB, "postgresql")` | `JSONB` | `JSON`/`TEXT` |
| `*_at` | `DateTime(timezone=True)` | `timestamptz` | `TIMESTAMP` |
| `amount` | `Numeric(12, 2)`, nullable | `numeric` | `NUMERIC` |
| `birth_date` | `Date` | `date` | `DATE` |

**Замечания по схеме:**
- `middle_name` — `NOT NULL DEFAULT ''` (а не nullable): иначе `NULL` ломает работу
  UNIQUE-ограничения (в SQL `NULL != NULL`, и два «однофамильца» без отчества не
  считались бы дубликатами).
- `application_id` — это тот самый `uuid4`, что сегодня генерит `make_decision`;
  становится первичным ключом таблицы заявок.
- `reasons` хранится как JSON-массив строк (список причин отказа; пуст, если
  approved). Отдельную таблицу причин не заводим — YAGNI.
- `amount` не возвращается ручкой (его нет в `ApplicationDecision`) — только
  хранится.

## 4. Слой доступа к данным

Новые модули (плоская структура, как у `metrics.py` / `logging_setup.py`):

| Модуль | Ответственность |
|---|---|
| `db.py` | Чтение env → DSN; async `engine`; `AsyncSessionLocal`; `Base`; FastAPI-зависимость `get_session`; создание/закрытие engine в lifespan |
| `models.py` | ORM-модели `User`, `Application` (типизированные `Mapped[...]`) |
| `repository.py` | `get_or_create_user(session, …)` и `save_application(session, …)` — вся работа с БД вынесена из `server.py` |

**Сессия и транзакция.** `get_session` — зависимость FastAPI: открывает
`AsyncSession`, отдаёт в обработчик, по завершении коммитит/закрывает, на
исключении — откатывает. Одна заявка = одна транзакция (пользователь + заявка
сохраняются атомарно).

**Найти-или-создать пользователя (с защитой от гонки):**

```
stmt = select(User).where(<все пять полей совпадают>)
user = (await session.execute(stmt)).scalar_one_or_none()
if user is None:
    user = User(...); session.add(user)
    try:
        await session.flush()          # вставка + проверка UNIQUE
    except IntegrityError:             # параллельный запрос успел создать
        await session.rollback()
        user = (await session.execute(stmt)).scalar_one()
```

Без этого два одновременных запроса с одной связкой дали бы `IntegrityError` на
UNIQUE. Релевантно даже учебному сервису, т.к. uvicorn обрабатывает запросы
конкурентно.

## 5. Изменения в `POST /applications`

Поток обработки (выделена новая часть):

```
payload → make_decision(payload)            # как сейчас: чистая функция, решение
        → get_or_create_user(session, ...)  # НОВОЕ
        → save_application(session, decision, user)   # НОВОЕ
        → commit                            # НОВОЕ
        → вернуть decision (формат ответа не меняется)
```

- `make_decision` остаётся **чистой функцией** — граница «чистого/грязного»
  (ARCHITECTURE §3) сохраняется: решение считается без БД, затем результат
  персистится.
- Обработчик получает сессию через `Depends(get_session)`.
- При любой ошибке БД (недоступна, таймаут, не та схема) транзакция откатывается
  и возвращается **500** (решение #3). Контракт успешного ответа
  (`ApplicationDecision`) не меняется.
- Приложение **само таблицы не создаёт** — схемой управляет Alembic (lifespan
  только поднимает/гасит engine).

## 6. Конфигурация (env)

Единый источник правды — переменные `DB_*`:

| Переменная | Локально | Прод (контейнер app) | Назначение |
|---|---|---|---|
| `DB_USER` | из `.env` | секрет GitHub | пользователь БД |
| `DB_PASSWORD` | из `.env` | секрет GitHub | пароль |
| `DB_DATABASE` | из `.env` | секрет GitHub | имя базы |
| `DB_HOST` | `localhost` | `petbank-db` | хост БД |
| `DB_PORT` | `5432` | `5432` | порт |

DSN приложения: `postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_DATABASE}`

**`.env` / `.env.example` правит пользователь** (ассистенту доступ к ним закрыт
настройками прав). Образец для `.env.example`:

```dotenv
# Подключение приложения к Postgres
DB_USER=petbank
DB_PASSWORD=change-me
DB_DATABASE=petbank
DB_HOST=localhost
DB_PORT=5432
```

`docker-compose.yml` правится так, чтобы образ `postgres:16` брал креды из тех же
`DB_*` (через маппинг в стандартные `POSTGRES_*`):

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: ${DB_DATABASE}
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
```

## 7. Миграции (Alembic)

- Инициализация по **async-шаблону** (`alembic init -t async`): `env.py`
  переиспользует тот же `DB_*` → DSN с `asyncpg`, второй драйвер не нужен.
- `target_metadata = Base.metadata` (из `models.py`) — для автогенерации.
- Первая миграция создаёт `users` и `applications` со связью и UNIQUE.
- Файлы версий коммитятся в репозиторий.
- На прод накат — **только вручную** (раздел 10).

## 8. Локальная разработка

```bash
docker compose up -d db          # поднять Postgres на localhost:5432
alembic upgrade head             # создать схему
python main.py                   # запустить приложение
```

Данные переживают перезапуск контейнера (volume `pgdata`). Сброс — `docker compose
down -v`.

## 9. Тесты

- **`tests/test_decision.py`** (чистая логика) — не трогаем.
- **`tests/test_http.py`** — заявки теперь идут в БД. Подключаем тестовую БД
  **SQLite (aiosqlite) в памяти** через `app.dependency_overrides[get_session]`;
  схему создаём в фикстуре (`Base.metadata.create_all`), между тестами —
  откат/пересоздание для изоляции.
- Новые проверки: заявка сохранена; повторная заявка с той же связкой
  переиспользует пользователя (в `users` одна строка, в `applications` — две);
  разные связки → разные пользователи; reasons сохранены.
- **Dev-зависимости** (`requirements-dev.txt`): добавить `aiosqlite`,
  `pytest-asyncio` (async-фикстуры БД).
- **CI:** `pytest` гоняется как раньше; внешняя БД не нужна (тесты на SQLite).

## 10. Прод-развёртывание

На VM появляется второй контейнер и docker-сеть:

```
            docker network: petbank-net
   ┌──────────────────────────────────────────────┐
   │  ┌────────────┐         ┌──────────────────┐  │
   │  │  petbank   │ DB_HOST │   petbank-db     │  │
   │  │  (app)     │────────►│  postgres:16     │  │
   │  │ -p 8000    │ =petbank│  volume pgdata   │  │
   │  └────────────┘  -db    └──────────────────┘  │
   └──────────────────────────────────────────────┘
```

Изменения:
- **`Dockerfile`:** добавить в `COPY` новые модули (`db.py`, `models.py`,
  `repository.py`); зависимости подтянутся из обновлённого `requirements.txt`.
- **`requirements.txt`:** `sqlalchemy[asyncio]>=2,<3`, `asyncpg>=0.29`,
  `alembic>=1.13`.
- **Контейнер `petbank-db`:** запускается на VM один раз — `postgres:16`,
  `--network petbank-net`, `--restart unless-stopped`, том для данных, креды из
  `DB_*`. **Без `-p` наружу** (только внутренняя сеть) — кроме оговорки для
  миграций, см. раздел 12.
- **`deploy` job (ci.yml):** `docker run` приложения дополняется
  `--network petbank-net` и `-e DB_HOST=petbank-db -e DB_PORT=5432
  -e DB_USER=… -e DB_PASSWORD=… -e DB_DATABASE=…` (значения — из секретов
  GitHub, прокидываются по SSH без попадания в логи).
- Порядок при первом раскате: создать сеть → поднять `petbank-db` → накатить
  миграции (раздел 11) → передеплоить `petbank` в сеть с env.

## 11. CI: ручная миграционная job

Новая job в `.github/workflows/` с ручным запуском:

```yaml
on:
  workflow_dispatch:        # запускается кнопкой «Run workflow»
jobs:
  migrate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.14" }
      - run: pip install -r requirements.txt
      - run: alembic upgrade head
        env:
          DB_HOST: 212.147.238.3      # внешний хост VM для миграций
          DB_PORT: "5432"
          DB_USER: ${{ secrets.DB_USER }}
          DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
          DB_DATABASE: ${{ secrets.DB_DATABASE }}
```

Отличие от приложения: приложение ходит на `petbank-db:5432` (внутренняя сеть),
а миграция — на `212.147.238.3:5432` (внешний адрес VM), потому что job
выполняется на GitHub-раннере вне docker-сети.

## 12. Безопасность и открытые вопросы

- **Внешний хост миграций требует открытого порта.** Ты задал, что миграция
  ходит на `212.147.238.3:5432` — значит порт Postgres проброшен наружу
  (`-p 5432:5432`). Это **выбранный по умолчанию вариант** (раздел 11 на нём и
  построен), но он расходится с «БД доступна только из внутренней сети докера»:
  база становится видна из интернета. Обязательная мера — **firewall на VM**,
  разрешающий 5432 только для диапазонов GitHub Actions (или открывать порт лишь
  на время прогона миграции). Приложение при этом всё равно ходит на `petbank-db`
  по внутренней сети, а не на внешний адрес.
  - Более безопасная альтернатива (если решишь отказаться от внешнего порта):
    гонять `alembic` по SSH во временном контейнере внутри `petbank-net` — тогда
    внешний `212.147.238.3` и проброс порта не нужны вовсе. Job сложнее; скажи на
    ревью, если предпочитаешь этот путь — тогда раздел 11 переедет на SSH.
- **ПДн.** В `applications` ложатся ФИО, телефон, дата рождения в открытом виде —
  как и в логах сегодня (ARCHITECTURE §7). Следствия: ограничить доступ к БД,
  бэкапы и срок хранения — учебный стенд, но отметить в STATUS.

## 13. Вне scope (YAGNI)

- Чтение/листинг заявок через API (только запись на этой итерации).
- Аутентификация/авторизация ручек.
- Пулинг/таймауты тонкой настройки, реплики, шардинг.
- Проверка БД в `/health` (можно добавить позже).
- Автонакат миграций при деплое (осознанно ручной запуск).
