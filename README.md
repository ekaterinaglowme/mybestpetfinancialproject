# PetBank

Учебный «банк». Сервер принимает заявку с персональными данными (ФИО, телефон, дата
рождения) и возвращает решение: **approved** или **declined**.

Текущее правило одобрения одно: **заявителю должно быть не меньше 18 лет**.

## Стек

Только стандартная библиотека Python (`http.server`) — **никаких зависимостей и `pip install`**.
Нужен только сам Python 3.8+. Скачать: https://www.python.org/downloads/
(при установке поставьте галочку «Add python.exe to PATH»).

## Запуск

```bash
python server.py
# или другой порт:
python server.py 8080
```

В PyCharm можно просто нажать зелёную кнопку **Run** на `main.py`.

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
  "birth_date": "1990-05-15",
  "amount": 100000
}
```

Ответ (одобрено):

```json
{
  "application_id": "…uuid…",
  "status": "approved",
  "applicant": { "full_name": "Иванов Иван Иванович", "age": 36, "phone": "+79991234567" },
  "reasons": [],
  "received_at": "2026-06-05T16:52:00"
}
```

Если возраст меньше 18 — `status: "declined"` и причина в `reasons`.

## Как дёрнуть

**Postman:** проще всего импортировать контракт `openapi.yaml`
(*Import → File → openapi.yaml*) — Postman сам соберёт коллекцию с примерами.
Или вручную: `POST http://localhost:8000/applications`, Body → raw → JSON, тело как выше.

**PyCharm:** откройте `requests.http` и жмите ▶ над нужным запросом.

**curl:**

```bash
curl -X POST http://localhost:8000/applications \
  -H "Content-Type: application/json" \
  -d "{\"last_name\":\"Иванов\",\"first_name\":\"Иван\",\"phone\":\"+79991234567\",\"birth_date\":\"1990-05-15\"}"
```

**PowerShell:**

```powershell
$body = @{ last_name="Иванов"; first_name="Иван"; phone="+79991234567"; birth_date="1990-05-15" } | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8000/applications -Method Post -ContentType "application/json" -Body $body
```

## Файлы

- `server.py` — сам сервер (вся логика тут).
- `main.py` — точка входа (запускает `server.py`).
- `openapi.yaml` — контракт API, импортируется в Postman.
- `requests.http` — готовые запросы для PyCharm.

## Куда расти

Новые правила одобрения добавляются в функцию `make_decision` в `server.py` —
дописывайте проверки и складывайте причины отказа в список `reasons`.
