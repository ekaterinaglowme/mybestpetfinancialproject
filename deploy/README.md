# Деплой PetBank

CI/CD на GitHub Actions (`.github/workflows/ci.yml`):

- **PR в `main`** → тесты (`pytest`) + сборка Docker-образа (без публикации). Деплоя нет.
- **push/мёрж в `main`** → тесты → сборка и публикация образа в ghcr → деплой на VM
  (каждый следующий шаг только если предыдущий зелёный).

Деплой = на VM подтягивается свежий образ из GitHub Container Registry и
перезапускается systemd-сервис `petbank`, который этот образ запускает контейнером.
Сервис слушает `:8000`. На проде теперь **Docker**, а не venv: на VM нужен только
установленный Docker — код и зависимости (FastAPI/Uvicorn) приезжают внутри образа.

Образ: `ghcr.io/ekaterinaglowme/mybestpetfinancialproject:latest`
(приватный, привязан к репозиторию).

## ⚠️ Перед мёржем в main (переход на Docker)

После мёржа CI сам делает `docker pull` + `systemctl restart petbank`. Если VM ещё
не подготовлена под Docker, рестарт упадёт и health-check в CI станет красным.

**До мёржа вручную на VM:**
1. Установить Docker Engine (если его нет): `curl -fsSL https://get.docker.com | sh`.
2. Добавить пользователя `deploy` в группу `docker` (чтобы пуллить/запускать без sudo):
   `usermod -aG docker deploy`, затем новый SSH-сеанс, чтобы группа применилась.
3. Заменить unit-файл на новый (он запускает контейнер, см. `petbank.service`):
   `cp deploy/petbank.service /etc/systemd/system/petbank.service && systemctl daemon-reload`.
4. `systemctl restart petbank` подхватит новый unit — процесс uvicorn из venv
   заменится контейнером. venv `/opt/petbank/.venv` больше не нужен.

**Если мёрж случился раньше:** сервис уйдёт в `failed`. Зайти по SSH, выполнить
шаги 1-3, затем `systemctl restart petbank` — дальше CI-деплои пойдут нормально.

## Что нужно в GitHub Secrets / Variables

Выставляет владелец репозитория (Settings → Secrets and variables → Actions):

| Имя         | Где      | Значение                                  |
|-------------|----------|-------------------------------------------|
| `SSH_HOST`  | Variable | IP виртуалки (`212.147.238.3`)            |
| `SSH_USER`  | Variable | `deploy`                                   |
| `SSH_KEY`   | Secret   | приватный deploy-ключ (ed25519, целиком)  |

Для входа в ghcr отдельный токен не нужен — CI использует встроенный `GITHUB_TOKEN`
(в workflow выданы права `packages: write` на сборку и `packages: read` на деплой).

## Подготовка сервера (один раз)

```bash
# пользователь deploy
useradd -m -s /bin/bash deploy
install -d -m 700 -o deploy -g deploy ~deploy/.ssh
echo "<публичный deploy-ключ>" >> ~deploy/.ssh/authorized_keys
chown deploy:deploy ~deploy/.ssh/authorized_keys && chmod 600 ~deploy/.ssh/authorized_keys

# Docker Engine + deploy в группу docker
curl -fsSL https://get.docker.com | sh
usermod -aG docker deploy
systemctl enable --now docker

# systemd-юнит (запускает контейнер) и узкий sudo на управление сервисом
cp deploy/petbank.service        /etc/systemd/system/petbank.service
cp deploy/petbank-deploy.sudoers /etc/sudoers.d/petbank-deploy
chmod 440 /etc/sudoers.d/petbank-deploy
visudo -c
systemctl daemon-reload
systemctl enable petbank

# фаервол
ufw allow OpenSSH
ufw allow 8000/tcp
ufw --force enable
```

Первый запуск: образа ещё нет на сервере — либо дождаться первого деплоя из `main`,
либо разово стянуть вручную:

```bash
# под пользователем deploy (он в группе docker)
docker login ghcr.io           # аккаунт с доступом к приватному пакету
docker pull ghcr.io/ekaterinaglowme/mybestpetfinancialproject:latest
sudo systemctl start petbank
curl http://127.0.0.1:8000/health
```

Дальше каждый деплой из `main` сам делает `docker pull` свежего `:latest` и
`systemctl restart petbank` — ручное вмешательство не нужно.

## Рекомендации (не блокеры)

- Сервис отдаёт персональные данные (ФИО, телефон, дата рождения) по plaintext HTTP
  на `0.0.0.0:8000`. Для учебного стенда ок; в проде — reverse-proxy (nginx) + TLS.
- Branch protection на `main`: «Require status checks to pass → Tests» — чтобы PR
  нельзя было смёржить с красными тестами.
- Образ в ghcr приватный. Сделать публичным (тогда `docker login` на VM не нужен) —
  в репозитории: Packages → пакет → Package settings → Change visibility.
