# PetBank: миграция на FastAPI + правила «возраст до 35» и «страна»

Дата: 2026-06-15
Ветка: `add-old-age-condition`

## Контекст

PetBank — учебный сервис приёма заявок на голой stdlib (`http.server`).
В репозитории уже есть:

- pytest-сьют (`tests/test_decision.py`, `tests/test_http.py`);
- CI/CD на GitHub Actions: тесты на каждый PR/push в `main`, деплой на VM
  (rsync + systemd) только при push/мёрже в `main`;
- черновой коммит `996e9de`, добавивший `MAX_AGE = 35` с отказом при
  `age > MAX_AGE` (сообщение коммита ошибочно упоминало «65»).

## Цели

1. Уточнить и закрепить правило верхней границы возраста (`MAX_AGE = 35`,
   текущее значение в коде — верное) + юнит-тесты на эту границу.
2. Добавить новый бизнес-фактор — страна заявителя: обязательное поле
   `country`, отказ для заявок из стран из стоп-листа (по умолчанию —
   «Китай», без учёта регистра).
3. Перевести HTTP-слой сервера с `http.server` на FastAPI + Uvicorn,
   сохранив существующий контракт API (коды ответов, формы JSON) без
   изменений, чтобы существующий тест-сьют и `openapi.yaml` остались
   валидными после минимальной правки.

## Вне объёма

- Веб-морда (HTML/JS-страница для отправки заявок без Postman) — отдельный
  спек, который будет сделан после этого и будет опираться на готовый
  FastAPI-эндпоинт.
- Полный переход на Pydantic-модели и автогенерируемую OpenAPI-документацию
  FastAPI — не делаем сейчас (выбран «вариант B», тонкий FastAPI-слой над
  существующими чистыми функциями).
- Реальные изменения на проде/VM (создание venv, рестарт сервиса) — только
  документируются как ручной шаг для оператора перед мёржем в `main`.

## Бизнес-правила (`server.py`)

- `REQUIRED_STRING_FIELDS` дополняется полем `country`: валидируется так же,
  как `last_name`/`first_name`/`phone` — обязательная непустая строка,
  пробелы по краям обрезаются.
- Новая константа рядом с `MIN_AGE`/`MAX_AGE`:

  ```python
  # Страны, заявки из которых не принимаются (сравнение без учёта регистра).
  BLOCKED_COUNTRIES = {"китай"}
  ```

- В `make_decision`: если `cleaned["country"].lower()` входит в
  `BLOCKED_COUNTRIES`, в `reasons` добавляется причина отказа вида
  `Заявки из страны «{country}» не принимаются`, статус становится
  `declined`.
- `MAX_AGE = 35` остаётся как есть; проверка `age > MAX_AGE` уже добавлена в
  `996e9de` — убираются только случайные пустые строки, оставшиеся после
  черновой правки.
- Текст в `GET /` и стартовый print в `run()` обновляются, чтобы упоминать
  обе границы возраста и правило по странам (сейчас там зашит только
  `MIN_AGE`).

## FastAPI-слой (вариант B — тонкая обёртка)

- `server.py`: `Handler(BaseHTTPRequestHandler)` заменяется на
  `app = FastAPI()` с маршрутами `GET /health`, `GET /`, `POST /applications`.
- Чистые функции `calculate_age`, `validate_payload`, `make_decision` не
  меняют свой контракт (кроме добавления `country`) — переносятся как есть.
- `POST /applications` читает тело запроса вручную через
  `await request.body()` + `json.loads`, повторяя текущую логику
  `do_POST` — это сохраняет точные коды/тела ответа:
  - пустое тело → 400 `{"error": "bad_request", "message": "Пустое тело запроса"}`;
  - невалидный JSON → 400 `{"error": "bad_request", "message": "Тело запроса не является валидным JSON"}`;
  - ошибки валидации → 400 `{"error": "validation_error", "message": ..., "details": [...]}`;
  - успех → 200 + результат `make_decision`.

  Без ручного парсинга FastAPI/Pydantic вернул бы свои 422 с другим форматом
  ошибки — это сломало бы текущий контракт и тесты.
- `GET /health` и `GET /` — простые функции, возвращающие dict (FastAPI сам
  сериализует в JSON).
- Неизвестные пути — стандартный 404 от FastAPI (`{"detail": "Not Found"}`).
  Текущие тесты проверяют только код 404, не тело, так что это не регрессия.
- `run()`: вместо `ThreadingHTTPServer.serve_forever()` —
  `uvicorn.run(app, host=host, port=port)`.
- `main.py` — без изменений (`from server import run; run()` продолжает
  работать, в т.ч. зелёная кнопка Run в PyCharm).

## Тесты

- `tests/test_decision.py`:
  - в хелперы `_valid_payload()` и `_cleaned()` добавляется `"country": "Россия"`;
  - параметризованные проверки обязательных строковых полей
    (`test_validate_required_string_missing` /
    `..._blank`) дополняются значением `"country"`;
  - новые тесты границы `MAX_AGE`:
    - возраст ровно 35 → `approved`, `reasons == []`;
    - возраст 36 → `declined`, причина содержит `str(MAX_AGE)`;
  - новый тест: `country="Китай"` → `declined`, причина упоминает страну.
- `tests/test_http.py`:
  - переписывается на `fastapi.testclient.TestClient(app)` вместо ручного
    подъёма `ThreadingHTTPServer` с `Handler`;
  - в `_adult_payload()` добавляется `country: "Россия"`;
  - существующие проверки (health, root, approved/declined по возрасту,
    validation error, invalid json, 404) переносятся на `TestClient`;
  - новый тест: заявка с `country="Китай"` → 200, `status == "declined"`,
    причина про страну.

## Зависимости, CI/CD и деплой

- Новый `requirements.txt` (корень репозитория, прод-зависимости):
  ```
  fastapi>=0.110,<1
  uvicorn[standard]>=0.29,<1
  ```
- `requirements-dev.txt`:
  ```
  -r requirements.txt
  pytest>=8,<9
  httpx>=0.27        # нужен для fastapi.testclient.TestClient
  ```
  `ci.yml` не меняется — он уже делает
  `pip install -r requirements-dev.txt`, что теперь подтянет и прод-зависимости.
- `deploy/petbank.service`: `ExecStart` переключается на venv —
  `/opt/petbank/.venv/bin/python /opt/petbank/server.py` (было
  `/usr/bin/python3 /opt/petbank/server.py`).
- `deploy/README.md`: добавляется шаг подготовки venv на сервере:
  ```bash
  python3 -m venv /opt/petbank/.venv
  /opt/petbank/.venv/bin/pip install -r /opt/petbank/requirements.txt
  ```
  и обновляется юнит (`systemctl daemon-reload` после правки `ExecStart`).

### ⚠️ Риск деплоя при мёрже в `main`

CI деплоит на VM автоматически при push/мёрже в `main`: rsync кода →
`systemctl restart petbank` → проверка `curl http://127.0.0.1:8000/health`.
Если на момент мёржа на VM нет venv с `fastapi`/`uvicorn`, рестарт сервиса
упадёт (`ModuleNotFoundError`), health-check в CI станет красным, а прод —
недоступен до ручного исправления.

**Перед мёржем этой ветки в `main` оператор должен на VM:**
1. Создать venv и установить зависимости из `requirements.txt` (см. шаги
   выше в `deploy/README.md`).
2. Обновить `/etc/systemd/system/petbank.service` (новый `ExecStart`) и
   выполнить `systemctl daemon-reload`.

Это будет явно описано в `deploy/README.md` и продублировано в описании PR.

## Документация

- `README.md`: раздел «Стек» — убрать формулировку «никаких зависимостей»,
  добавить `pip install -r requirements.txt` в инструкцию запуска; обновить
  описание правил (возраст 18–35, страна) и примеры запросов с полем
  `country`.
- `openapi.yaml`: добавить `country` (required) в `ApplicationRequest`,
  обновить примеры (включая пример отказа по стране).
- `requests.http`: добавить `country` во все существующие тела запросов,
  добавить новый запрос-пример «отказ по стране».
