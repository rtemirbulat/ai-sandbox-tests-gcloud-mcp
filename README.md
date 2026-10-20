# Sandbox MCP · пилот песочницы BI на GCP

MCP-сервер песочницы вайбкодинга: Claude Code через LiteLLM MCP Gateway оценивает приложение,
проверяет конкретный commit (Gitleaks, Trivy) и разворачивает его в TEST через Coolify.

```
Claude Code ──HTTPS──▶ Caddy ──▶ LiteLLM (/sandbox_mcp/mcp, virtual key) ──▶ sandbox-mcp ──▶ PostgreSQL
                                                                                  ▲            │
                                                                     sandbox-worker ◀── задания ┘
                                                                          │
                                                   git clone → detect → gitleaks → trivy → Coolify API → smoke
```

Документы: [дизайн](docs/superpowers/specs/2026-10-19-sandbox-mcp-design.md),
[план реализации](docs/superpowers/plans/2026-10-19-sandbox-mcp.md), [runbook для DevOps](docs/OPERATIONS.md). Контекст платформы — в
родительской папке (`PROJECT_CONTEXT.md`, `sandbox-v4-decisions-*.md`, `sandbox-final-v5-bdd.md`).

## Инструменты MCP

| Tool | Вход | Выход |
|---|---|---|
| `assess_application` | `repo_url` (публичный GitHub), `git_ref`, `app_name`, `purpose`, `data_classes` | `app_id`, `slug`, `kind`, `commit_sha`, `decision`, `requirements` (R1) |
| `validate_and_deploy` | `app_id`, `commit_sha` (40 hex), `env` = `TEST`, `idempotency_key` | `job_id`, `state`, `created` |
| `status` | `job_id` или `app_id` | `state`, `stage`, `checks[]`, `url`, `expires_at`, `next_action` |

Состояния задания: `queued → running → succeeded | blocked | failed`, затем `expired` по TTL.
`blocked` — проверки не пройдены, нужно исправить код; `failed` — ошибка платформы или деплоя.

## Разработка

```bash
make install        # venv + зависимости
make test           # 33 pytest, SQLite, fake Coolify, fake сканеры
make lint           # ruff
```

Структура: `src/sandbox_mcp/db` (модели, репозиторий), `services` (git, detect, rules, scanners,
coolify, pipeline), `mcp` (tools и Starlette-приложение), `worker.py`, `cli.py`.

## Подключение Claude Code (для сотрудника)

Пилотный шлюз: `https://mcp.35-234-71-242.sslip.io`. Для подключения нужны два credential,
которые выдаёт администратор песочницы (см. ниже «Выдача доступа»):

| Credential | Заголовок | Что даёт |
|---|---|---|
| LiteLLM virtual key (`sk-...`) | `x-litellm-api-key: Bearer sk-...` | Доступ к MCP через шлюз, логи и лимиты LiteLLM |
| Токен песочницы (64 hex) | `x-sandbox-token: <token>` | Личность: владелец приложений и заданий |

1. Зарегистрировать сервер в Claude Code (один раз, на уровне пользователя):

   ```bash
   claude mcp add --scope user --transport http sandbox \
     https://mcp.35-234-71-242.sslip.io/sandbox_mcp/mcp \
     --header "x-litellm-api-key: Bearer sk-ВАШ_КЛЮЧ" \
     --header "x-sandbox-token: ВАШ_ТОКЕН"
   ```

2. Проверить: `claude mcp list` должен показать `sandbox ... ✔ Connected`. В Claude Code
   появятся инструменты `sandbox_mcp-assess_application`, `sandbox_mcp-validate_and_deploy`,
   `sandbox_mcp-status`.

3. Рабочий сценарий в Claude Code: запушить код в публичный GitHub-репозиторий, затем попросить
   Claude «оценить приложение из <repo> и выпустить его в TEST». Claude вызовет
   `assess_application` → `validate_and_deploy` → `status` и вернёт URL вида
   `https://<slug>.sandbox.34-179-210-136.sslip.io` со сроком жизни 14 дней. Если `status`
   вернул `blocked`, нужно исправить код (секреты, уязвимости HIGH/CRITICAL) и повторить с новым
   commit; `failed` означает ошибку платформы, повторить с новым `idempotency_key`.

Удалить подключение: `claude mcp remove sandbox -s user`. Оба credential личные, в чат, git и
общие документы не копировать; при утечке администратор отзывает ключ в LiteLLM
(`POST /key/delete`) и выдаёт новый токен песочницы.

### Выдача доступа (администратор)

```bash
export LITELLM_MASTER_KEY=$(grep ^LITELLM_MASTER_KEY= deploy/.env | cut -d= -f2-)  # не source: в значениях бывают | и $
infra/litellm-key.sh https://mcp.35-234-71-242.sslip.io "$LITELLM_MASTER_KEY" <login>   # печатает JSON с полем key
infra/sandbox-token.sh <login>                                                          # печатает токен один раз
```

`<login>` — корпоративный логин сотрудника; он станет `owner` приложений. Токен хранится в базе
только в виде sha256, повторно его показать нельзя, при утере выпускается новый.

### Админка LiteLLM

UI шлюза: `https://mcp.35-234-71-242.sslip.io/ui/` (Caddy проксирует весь LiteLLM, включая UI и API).
Вход: имя пользователя `admin`, пароль = `LITELLM_MASTER_KEY` из `deploy/.env`. В UI видны virtual keys
(Virtual Keys), журнал вызовов tools (Logs), MCP-серверы (MCP Servers → `sandbox_mcp`) и пользователи.
Отдельные логин и пароль можно задать переменными `UI_USERNAME` / `UI_PASSWORD` в compose для сервиса litellm.
API того же шлюза: `curl -H "Authorization: Bearer $LITELLM_MASTER_KEY" https://mcp.35-234-71-242.sslip.io/key/list`.

## Локальный стек

```bash
cp deploy/.env.example deploy/.env   # заполнить случайными значениями, MCP_DOMAIN=localhost
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d postgres litellm sandbox-mcp sandbox-worker
infra/litellm-key.sh http://localhost:4000 "$LITELLM_MASTER_KEY" temirbulat_r   # LiteLLM virtual key
docker compose -f deploy/compose.yaml --env-file deploy/.env exec sandbox-mcp sandbox-mcp token temirbulat_r  # identity token
claude mcp add --transport http sandbox http://localhost:4000/sandbox_mcp/mcp \
  --header "x-litellm-api-key: Bearer sk-<virtual key>" --header "x-sandbox-token: <token>"
```

При `SANDBOX_COOLIFY_FAKE=true` деплой имитируется: задание проходит все стадии, URL
вида `https://<slug>.<SANDBOX_APPS_BASE_DOMAIN>` возвращается без реального приложения,
smoke-проверка пропускается. Это режим для локальной разработки и юнит-тестов.

## VM в GCP

```bash
infra/create-vm.sh                    # e2-medium, Ubuntu 24.04, europe-west3-a, firewall 80/443 + IAP SSH
# записать MCP_DOMAIN из вывода в deploy/.env, затем:
infra/deploy.sh                       # scp + docker compose up -d --build на VM
curl https://$MCP_DOMAIN/health/liveliness
infra/litellm-key.sh https://$MCP_DOMAIN "$LITELLM_MASTER_KEY" <login>  # virtual key (см. «Выдача доступа»)
infra/sandbox-token.sh <login>                                          # identity token
```

Проект `project-c6b97dc5-607e-455a-910`, аккаунт `temirbulat_r@bi.group`. Параметры
переопределяются переменными `PROJECT`, `ACCOUNT`, `ZONE`, `NAME`, `MACHINE`.

## Стенд Coolify (пилот)

Реальный Coolify 4.4.6 живёт на VM `sandbox-coolify` (34.179.210.136, стенд из
`../ai-sandbox-tests-gcloud`). Для MCP там созданы API-токен `sandbox-mcp` (root, хранится в
`.secrets/coolify-token.txt` и `deploy/.env`) и проект `bi-sandbox`, окружение `production`,
сервер `localhost`. Приложения получают адреса `https://<slug>.sandbox.34-179-210-136.sslip.io`
с сертификатами Let's Encrypt от Traefik Coolify. Проверено 19.10.2026 через LiteLLM: статика
из `github/choosealicense.com` задеплоена, smoke 200, повторный выпуск переиспользует
приложение Coolify; репозиторий `gitleaks/gitleaks` остановлен на стадии Gitleaks (`blocked`).

Ограничение: VM Coolify по расписанию останавливается 21.10.2026 в 23:59 Asia/Aqtobe
(`scheduling.terminationTime`); после этого деплой падает в `failed` на `coolify_create` до
запуска VM. TTL-уборка worker удаляет приложения через `SANDBOX_TTL_DAYS`.

## Переменные окружения (`SANDBOX_*`)

| Переменная | Назначение |
|---|---|
| `DATABASE_URL` | SQLAlchemy URL; в compose — PostgreSQL |
| `GATEWAY_TOKEN` | Значение `X-Gateway-Token`, с которым LiteLLM ходит в sandbox-mcp |
| `COOLIFY_FAKE` | `true` — in-memory Coolify, `false` — реальный API |
| `COOLIFY_URL`, `COOLIFY_TOKEN`, `COOLIFY_PROJECT_UUID`, `COOLIFY_SERVER_UUID`, `COOLIFY_ENVIRONMENT_NAME` | Параметры стенда Coolify |
| `APPS_BASE_DOMAIN` | Базовый домен приложений: `<slug>.<domain>` |
| `TTL_DAYS` | Срок жизни TEST (по умолчанию 14) |
| `WORKDIR` | Каталог для clone (в образе `/work`) |

## Безопасность

- Пользовательский код не исполняется в worker: только clone и сканирование; сборка и запуск — у Coolify.
- sandbox-mcp доступен только из docker-сети и только с заголовком `X-Gateway-Token` (LiteLLM
  добавляет его через `static_headers`); наружу открыт один порт 443 (Caddy).
- Два credential на пользователя. LiteLLM virtual key (`x-litellm-api-key`) даёт доступ к MCP
  через шлюз и учитывается в его логах. Токен песочницы (`x-sandbox-token`, выдаёт
  `sandbox-mcp token <user>`, хранится хэш) определяет владельца приложений и заданий: чужие
  `status` и `validate_and_deploy` отвечают `not_found`. LiteLLM намеренно вырезает свой ключ из
  форвардимых заголовков, поэтому личность нельзя взять из него; без токена `owner=unknown`.
- Секреты только в `deploy/.env` и `.secrets/` (вне git).

## Что ещё не сделано

- Публичный репозиторий как единственный источник кода; приватные репозитории (deploy key) не поддержаны.
- Alembic нет: схема создаётся `ensure_schema` (create_all + аддитивные ALTER).
- PROD, IT-комитет, реестр систем, OpenMetadata, корпоративный Keycloak — вне пилота.
