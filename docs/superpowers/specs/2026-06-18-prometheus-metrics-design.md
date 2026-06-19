# PetBank: Prometheus-метрики на /metrics

Дата: 2026-06-18
Ветка: новая ветка от `main` (независимо от ветки логирования)

## Контекст

PetBank — сервис на FastAPI (`server.py`), деплой в Docker на VM. Нужен экспорт
метрик в формате Prometheus, чтобы их скрейпил уже настроенный на стороне
заказчика Prometheus и строил дашборды в Grafana.

## Цели

1. Эндпоинт `GET /metrics` на том же порту, что и приложение (8000), **без
   авторизации**, отдаёт метрики в текстовом формате Prometheus exposition
   (сериализует библиотека, руками формат не собираем).
2. HTTP-метрики (rate / errors / latency по ручкам) считаются автоматически.
3. Бизнес-метрики PetBank: решения approved/declined (с разбивкой по стране),
   причины отказов по категориям, распределение запрошенных сумм.

## Вне объёма

- Авторизация на `/metrics`, ограничение по сети (заказчик скрейпит сам).
- Настройка Prometheus/Grafana (на стороне заказчика уже готова).
- Изменение бизнес-правил и схемы ответа.
- Логирование — отдельная ветка/PR.

## Подход

- **HTTP-метрики:** `prometheus-fastapi-instrumentator`. В месте создания `app`:
  ```python
  from prometheus_fastapi_instrumentator import Instrumentator
  Instrumentator().instrument(app).expose(app)
  ```
  Библиотека поднимает `/metrics` и считает дефолтные HTTP-метрики
  (`http_request_duration_seconds*`, счётчики запросов с лейблами
  handler/method/status).
- **Бизнес-метрики:** официальный `prometheus_client` — `Counter`/`Histogram`
  с лейблами. Они попадают в тот же `/metrics` через общий REGISTRY.

## Компоненты

### Новый модуль `metrics.py`

Определяет бизнес-метрики (один модуль — одна ответственность, тестируется
отдельно):

| Метрика | Тип | Лейблы | Назначение |
|---|---|---|---|
| `petbank_decisions_total` | Counter | `status`, `country` | Обработанные заявки по итогу |
| `petbank_rejection_reasons_total` | Counter | `reason` | Причины отказов по категориям |
| `petbank_application_amount_rub` | Histogram | — | Распределение сумм заявок |

Категории `reason` — **ограниченный** набор значений: `age_below_min`,
`age_above_max`, `blocked_country` (не пишем сырой текст причины, чтобы не
плодить кардинальность).

### Правки `server.py`

- Импорт и подключение instrumentator сразу после `app = FastAPI(title="PetBank")`.
- В `make_decision` инкременты метрик: на каждый сработавший reason —
  `petbank_rejection_reasons_total{reason=...}`; в конце —
  `petbank_decisions_total{status=..., country=...}`; при наличии `amount` —
  `petbank_application_amount_rub.observe(amount)`.

## Кардинальность лейблов

`country` приходит из тела запроса (свободный текст) — потенциально
неограниченная кардинальность. Для текущего объёма приложения это приемлемо;
значение нормализуем (`strip().lower()`). Если в проде появится мусор —
заменить на ограниченный справочник стран. `status` (2 значения) и `reason`
(3 категории) — ограниченные.

## Тесты

Новый `tests/test_metrics.py` (pytest + `TestClient`):

- `GET /metrics` → 200, `Content-Type` начинается с `text/plain` (НЕ JSON), тело
  содержит HTTP-метрику инструментатора (`http_request_duration_seconds`).
- После одобренной и отклонённой заявок `/metrics` содержит
  `petbank_decisions_total` со `status="approved"` и `status="declined"`.
- После отказа несовершеннолетнему — `petbank_rejection_reasons_total{reason="age_below_min"}`.
- После заявки с `amount` — присутствуют `petbank_application_amount_rub_bucket`
  и `petbank_application_amount_rub_count`.

Метрики глобальны и накапливаются между тестами — проверяем наличие нужных
строк/лейблов, а не точные значения. Существующие тесты остаются зелёными.

Ручная проверка (как просил заказчик): `curl localhost:8000/metrics` на запущенном
сервере → возвращается текст с метриками.

## Файлы

| Файл | Действие |
|---|---|
| `metrics.py` | **Новый**: определения бизнес-метрик (`Counter`/`Histogram`) |
| `server.py` | Подключить instrumentator у `app`; инкременты в `make_decision` |
| `tests/test_metrics.py` | **Новый**: тесты `/metrics` и бизнес-метрик |
| `requirements.txt` | `+prometheus-fastapi-instrumentator`, `+prometheus-client` |

## Зависимости

- `prometheus-fastapi-instrumentator>=6,<8` (поднимает `/metrics`, HTTP-метрики)
- `prometheus-client>=0.19,<1` (кастомные бизнес-метрики; транзитивно тянется
  instrumentator-ом, объявлен явно — импортируем напрямую)
