# Деплой PetBank

CI/CD на GitHub Actions (`.github/workflows/ci.yml`):

- **PR в `main`** → гоняются тесты (`pytest`). Деплоя нет.
- **push/мёрж в `main`** → тесты, затем деплой на виртуалку (только если тесты зелёные).

Деплой = `rsync` кода в `/opt/petbank` на сервере + `systemctl restart petbank`.
Сервис — обычный systemd-юнит, работает под непривилегированным юзером `deploy`,
слушает `:8000`. Никакого Docker и pip на проде: приложение на голой stdlib.

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

# rsync для деплоя + фаервол
apt-get update && apt-get install -y rsync
ufw allow OpenSSH
ufw allow 8000/tcp
ufw --force enable
```

После первого `rsync` кода в `/opt/petbank` поднять сервис:
`systemctl start petbank` и проверить `curl http://127.0.0.1:8000/health`.

## Рекомендации (не блокеры)

- Сервис отдаёт персональные данные (ФИО, телефон, дата рождения) по plaintext HTTP
  на `0.0.0.0:8000`. Для учебного стенда ок; в проде — reverse-proxy (nginx) + TLS.
- Branch protection на `main`: «Require status checks to pass → Tests» — чтобы PR
  нельзя было смёржить с красными тестами.
