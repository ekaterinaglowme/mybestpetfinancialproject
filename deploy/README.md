# Деплой PetBank

CI/CD на GitHub Actions (`.github/workflows/ci.yml`):

- **PR в `main`** → гоняются тесты (`pytest`). Деплоя нет.
- **push/мёрж в `main`** → тесты, затем деплой на виртуалку (только если тесты зелёные).

Деплой = `rsync` кода в `/opt/petbank` на сервере + `systemctl restart petbank`.
Сервис — обычный systemd-юнит, работает под непривилегированным юзером `deploy`,
слушает `:8000`. Никакого Docker на проде: зависимости (FastAPI/Uvicorn)
ставятся в venv `/opt/petbank/.venv`, системный Python не трогаем.

## ⚠️ Перед мёржем в main (переход на FastAPI)

Сервис теперь требует `fastapi`/`uvicorn` (см. `requirements.txt`). CI
деплоит автоматически при мёрже в `main` и сразу перезапускает сервис —
если на VM ещё нет venv с этими зависимостями, `systemctl restart petbank`
упадёт с `ModuleNotFoundError`, health-check в CI станет красным, прод
будет недоступен.

Все шаги ниже нужно выполнить **до** мёржа — после мёржа CI рестартует
сервис автоматически и без паузы для ручного вмешательства.

**До мёржа вручную на VM:**
1. Сначала засинхронизировать код этой ветки на VM (разово, любым способом,
   например `rsync`/`scp`), чтобы на сервере появился файл
   `/opt/petbank/requirements.txt` — без него следующий шаг не выполнится.
2. Затем создать venv и поставить зависимости:
   `python3 -m venv /opt/petbank/.venv && /opt/petbank/.venv/bin/pip install -r /opt/petbank/requirements.txt`.
   (Те же команды используются ниже в «Подготовка сервера» для свежей VM —
   здесь это разовая миграция уже существующего сервера.)
3. Обновить `/etc/systemd/system/petbank.service` (новый `ExecStart`, см.
   ниже) и выполнить `systemctl daemon-reload`.

**Если мёрж всё же случился раньше:** сервис упадёт в `failed` после
рестарта. Зайти на VM по SSH, выполнить шаги 1-3 выше, затем
`systemctl restart petbank` — после этого CI-деплои снова будут
проходить нормально (venv и unit-файл останутся на месте).

## Что нужно в GitHub Secrets

Выставляет владелец репозитория (Settings → Secrets and variables → Actions):

| Secret      | Значение                                  |
|-------------|-------------------------------------------|
| `SSH_HOST`  | IP виртуалки (`212.147.238.3`)            |
| `SSH_USER`  | `deploy`                                   |
| `SSH_KEY`   | приватный deploy-ключ (ed25519, целиком)  |

Публичный ключ этой пары лежит в `~deploy/.ssh/authorized_keys` на сервере.
Ключ — отдельный, только под деплой; личные ключи в Secrets не кладём.

## Подготовка сервера (один раз)

```bash
# пользователь deploy
useradd -m -s /bin/bash deploy
install -d -m 700 -o deploy -g deploy ~deploy/.ssh
echo "<публичный deploy-ключ>" >> ~deploy/.ssh/authorized_keys
chown deploy:deploy ~deploy/.ssh/authorized_keys && chmod 600 ~deploy/.ssh/authorized_keys

# каталог приложения
install -d -o deploy -g deploy /opt/petbank

# systemd-юнит и узкий sudo
cp deploy/petbank.service        /etc/systemd/system/petbank.service
cp deploy/petbank-deploy.sudoers /etc/sudoers.d/petbank-deploy
chmod 440 /etc/sudoers.d/petbank-deploy
visudo -c
systemctl daemon-reload
systemctl enable petbank

# rsync для деплоя, venv-модуль + фаервол
apt-get update && apt-get install -y rsync python3-venv
ufw allow OpenSSH
ufw allow 8000/tcp
ufw --force enable
```

После первого `rsync` кода в `/opt/petbank` — создать venv и поставить
зависимости:

```bash
python3 -m venv /opt/petbank/.venv
/opt/petbank/.venv/bin/pip install -r /opt/petbank/requirements.txt
```

Затем поднять сервис: `systemctl start petbank` и проверить
`curl http://127.0.0.1:8000/health`.

После этого первого раза `requirements.txt` обновляется автоматически
обычным `rsync` при каждом деплое — venv пересоздавать не нужно, но при
добавлении/обновлении зависимостей нужно вручную повторить
`/opt/petbank/.venv/bin/pip install -r /opt/petbank/requirements.txt`
на VM (CI это не делает).

## Рекомендации (не блокеры)

- Сервис отдаёт персональные данные (ФИО, телефон, дата рождения) по plaintext HTTP
  на `0.0.0.0:8000`. Для учебного стенда ок; в проде — reverse-proxy (nginx) + TLS.
- Branch protection на `main`: «Require status checks to pass → Tests» — чтобы PR
  нельзя было смёржить с красными тестами.
