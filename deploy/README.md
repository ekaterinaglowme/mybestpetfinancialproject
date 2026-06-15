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

**До мёржа вручную на VM:**
1. `python3 -m venv /opt/petbank/.venv && /opt/petbank/.venv/bin/pip install -r /opt/petbank/requirements.txt`
   (нужен файл `/opt/petbank/requirements.txt` — например, разово
   засинхронизировать код этой ветки на VM перед мёржем).
2. Обновить `/etc/systemd/system/petbank.service` (новый `ExecStart`, см.
   ниже) и выполнить `systemctl daemon-reload`.

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
зависимости (повторять при каждом обновлении `requirements.txt`):

```bash
python3 -m venv /opt/petbank/.venv
/opt/petbank/.venv/bin/pip install -r /opt/petbank/requirements.txt
```

Затем поднять сервис: `systemctl start petbank` и проверить
`curl http://127.0.0.1:8000/health`.

## Рекомендации (не блокеры)

- Сервис отдаёт персональные данные (ФИО, телефон, дата рождения) по plaintext HTTP
  на `0.0.0.0:8000`. Для учебного стенда ок; в проде — reverse-proxy (nginx) + TLS.
- Branch protection на `main`: «Require status checks to pass → Tests» — чтобы PR
  нельзя было смёржить с красными тестами.
