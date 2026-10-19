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
[план реализации](docs/superpowers/plans/2026-10-19-sandbox-mcp.md). Контекст платформы — в
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

## Локальный стек

```bash
cp deploy/.env.example deploy/.env   # заполнить случайными значениями, MCP_DOMAIN=localhost
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d postgres litellm sandbox-mcp sandbox-worker
infra/litellm-key.sh http://localhost:4000 "$LITELLM_MASTER_KEY" temirbulat_r   # virtual key
claude mcp add --transport http sandbox http://localhost:4000/sandbox_mcp/mcp \
  --header "x-litellm-api-key: Bearer sk-<virtual key>"
```

Пока `SANDBOX_COOLIFY_FAKE=true`, деплой имитируется: задание проходит все стадии, URL
вида `https://<slug>.<SANDBOX_APPS_BASE_DOMAIN>` возвращается без реального приложения.
Smoke-проверка в fake-режиме пропускается.

## VM в GCP

```bash
infra/create-vm.sh                    # e2-medium, Ubuntu 24.04, europe-west3-a, firewall 80/443 + IAP SSH
# записать MCP_DOMAIN из вывода в deploy/.env, затем:
infra/deploy.sh                       # scp + docker compose up -d --build на VM
curl https://$MCP_DOMAIN/health/liveliness
```

Проект `project-c6b97dc5-607e-455a-910`, аккаунт `temirbulat_r@bi.group`. Параметры
переопределяются переменными `PROJECT`, `ACCOUNT`, `ZONE`, `NAME`, `MACHINE`.

## Переменные окружения (`SANDBOX_*`)

| Переменная | Назначение |
|---|---|
| `DATABASE_URL` | SQLAlchemy URL; в compose — PostgreSQL |
| `GATEWAY_TOKEN` | Bearer-токен, которым LiteLLM ходит в sandbox-mcp |
| `COOLIFY_FAKE` | `true` — in-memory Coolify, `false` — реальный API |
| `COOLIFY_URL`, `COOLIFY_TOKEN`, `COOLIFY_PROJECT_UUID`, `COOLIFY_SERVER_UUID`, `COOLIFY_ENVIRONMENT_NAME` | Параметры стенда Coolify |
| `APPS_BASE_DOMAIN` | Базовый домен приложений: `<slug>.<domain>` |
| `TTL_DAYS` | Срок жизни TEST (по умолчанию 14) |
| `WORKDIR` | Каталог для clone (в образе `/work`) |

## Безопасность

- Пользовательский код не исполняется в worker: только clone и сканирование; сборка и запуск — у Coolify.
- sandbox-mcp доступен только из docker-сети и только с `GATEWAY_TOKEN`; наружу открыт один порт 443 (Caddy).
- Личность пользователя берётся из заголовков LiteLLM (список `ACTOR_HEADERS` в `mcp/server.py`);
  при их отсутствии пилот работает с `actor=unknown`, что отмечается в данных задания.
- Секреты только в `deploy/.env` и `.secrets/` (вне git).

## Что ещё не сделано

- Реальный Coolify: адрес и токен появятся после стенда из `../ai-sandbox-tests-gcloud`.
- Проверка, какие заголовки идентичности LiteLLM реально пробрасывает в MCP.
- PROD, IT-комитет, реестр систем, OpenMetadata, корпоративный Keycloak — вне пилота.
