# PetBank

Учебный «банк». Сервер принимает заявку с персональными данными (ФИО, телефон, дата
рождения, страна) и возвращает решение: **approved** или **declined**.

Правила одобрения:
- заявителю должно быть от **18 до 35 лет** включительно;
- страна заявителя не должна быть в стоп-листе (по умолчанию — «Китай»).

## Стек

[FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/).
Нужен Python 3.8+ и установленные зависимости:

```bash
pip install -r requirements.txt
```

## Запуск

```bash
python app/src/main.py
# или другой порт:
python app/src/server.py 8080
```

В PyCharm можно просто нажать зелёную кнопку **Run** на `app/src/main.py`.

После старта в консоли появится:

```
PetBank запущен: http://localhost:8000  (Ctrl+C — остановить)
```

## Эндпоинты

| Метод | Путь            | Описание                          |
|-------|-----------------|-----------------------------------|
| POST  | `/applications` | Подать заявку, получить решение   |
| GET   | `/health`       | Проверка живости                  |
| GET   | `/`             | Короткая справка                  |

### POST /applications

Запрос:

```json
{
  "last_name": "Иванов",
  "first_name": "Иван",
  "middle_name": "Иванович",
  "phone": "+79991234567",
  "birth_date": "2000-05-15",
  "country": "Россия",
  "amount": 100000
}
```

Ответ (одобрено):

```json
{
  "application_id": "…uuid…",
  "status": "approved",
  "applicant": { "full_name": "Иванов Иван Иванович", "age": 26, "phone": "+79991234567" },
  "reasons": [],
  "received_at": "2026-06-05T16:52:00"
}
```

Если возраст вне диапазона 18–35 или страна — в стоп-листе, `status: "declined"`,
причины — в `reasons`.

## Как дёрнуть

**Postman:** проще всего импортировать контракт `app/resources/openapi.yaml`
(*Import → File → app/resources/openapi.yaml*) — Postman сам соберёт коллекцию с примерами.
Или вручную: `POST http://localhost:8000/applications`, Body → raw → JSON, тело как выше.

**PyCharm:** откройте `requests.http` и жмите ▶ над нужным запросом.

**curl:**

```bash
curl -X POST http://localhost:8000/applications \
  -H "Content-Type: application/json" \
  -d "{\"last_name\":\"Иванов\",\"first_name\":\"Иван\",\"phone\":\"+79991234567\",\"birth_date\":\"2000-05-15\",\"country\":\"Россия\"}"
```

**PowerShell:**

```powershell
$body = @{ last_name="Иванов"; first_name="Иван"; phone="+79991234567"; birth_date="2000-05-15"; country="Россия" } | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8000/applications -Method Post -ContentType "application/json" -Body $body
```

## Файлы

Приложение — одна папка `app/` (её и заворачиваем в Docker-образ):

```
app/
├── src/                    # код
│   ├── main.py             # точка входа (запускает server.py)
│   ├── server.py           # сервер: бизнес-логика и эндпоинты
│   ├── logging_setup.py    # JSON-логи, request_id
│   ├── metrics.py          # Prometheus-метрики
│   ├── ratelimit.py        # rate limiter
│   ├── request_timeout.py  # таймаут-предохранитель
│   ├── db.py               # подключение к Postgres
│   ├── models.py           # ORM-модели (SQLAlchemy)
│   └── repository.py       # сохранение user/application
└── resources/
    └── openapi.yaml        # контракт API (импорт в Postman)
```

В корне репозитория (в образ не идут): `requirements.txt`, `requests.http`,
`tests/`, миграции `alembic/` (применяются отдельной job `migrate.yml`).

## Защита под нагрузкой и SLO

`POST /applications` защищён от перегрузки (только эта ручка; `/health`,
`/metrics`, `/`, `/docs` не лимитируются):

- **Rate limiter** — глобальный token bucket. Сверх лимита → `429` +
  `Retry-After`. Метрика `petbank_rate_limited_total`.
- **Таймаут-предохранитель** — долгий запрос → `503`. Метрика
  `petbank_request_timeouts_total`. Прерывает только на `await`-точках
  (формальный предохранитель: `make_decision` синхронный и быстрый).

Конфигурация (env, `0` = выключить):

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `RATE_LIMIT_RPS` | `100` | Пополнение токенов (RPS) |
| `RATE_LIMIT_BURST` | `= RATE_LIMIT_RPS` | Ёмкость bucket (всплеск) |
| `REQUEST_TIMEOUT_SECONDS` | `1.0` | Таймаут запроса |

**SLO:** при нагрузке до 100 RPS p95 латентности `/applications` ≤ 200 мс.
Наблюдается в Grafana (дашборд `petbank-business`, секция «SLO»).

## Куда расти

Новые правила одобрения добавляются в функцию `make_decision` в `app/src/server.py` —
дописывайте проверки и складывайте причины отказа в список `reasons`.

## Чёрный список паспортов (v2)

Ручка `POST /applications/v2` перед решением проверяет паспорт по внешнему сервису:

- `BLACK_LIST_URL` — базовый адрес сервиса (по умолчанию `http://212.147.238.3:8090`).
- `BLACK_LIST_TIMEOUT_SECONDS` — таймаут запроса в секундах (по умолчанию `0.8`;
  держать заметно меньше `REQUEST_TIMEOUT_SECONDS`).

Если сервис недоступен — заявка отклоняется (fail-closed).
