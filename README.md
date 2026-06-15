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

**Postman:** проще всего импортировать контракт `openapi.yaml`
(*Import → File → openapi.yaml*) — Postman сам соберёт коллекцию с примерами.
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

- `server.py` — сам сервер (вся логика тут).
- `main.py` — точка входа (запускает `server.py`).
- `requirements.txt` — зависимости для запуска (FastAPI, Uvicorn).
- `openapi.yaml` — контракт API, импортируется в Postman.
- `requests.http` — готовые запросы для PyCharm.

## Куда расти

Новые правила одобрения добавляются в функцию `make_decision` в `server.py` —
дописывайте проверки и складывайте причины отказа в список `reasons`.
