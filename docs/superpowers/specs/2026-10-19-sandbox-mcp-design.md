# Sandbox MCP · дизайн пилота на GCP

Дата: 19.10.2026. Статус: согласован с владельцем проекта (вариант A).

Источники: доска Miro AIOFFICE-sandbox (фреймы «Итоговая схема v5», «BDD 01–18», «HLD: карта сервисов»),
`../sandbox-v4-decisions-2026-10-16.md`, `../sandbox-final-v5-bdd.md`, `../sandbox-hld-presentation-2026-10-16.md`,
`../PROJECT_CONTEXT.md`.

## 1. Цель

Воспроизвести в пилоте на Google Cloud целевую связку из HLD:

```
Claude Code ──HTTPS──▶ LiteLLM MCP Gateway ──▶ Sandbox MCP ──▶ задания ──▶ проверки commit ──▶ Coolify (TEST) ──▶ URL
```

Сотрудник работает в Claude Code по своей подписке. Через LiteLLM идёт **только MCP-трафик**; запросы к модели
LiteLLM не проксирует и API-ключ Anthropic не нужен. LiteLLM даёт virtual key на пользователя, логи вызовов tools
и ограничение доступа к серверам. Бизнес-правила и допуски остаются в Sandbox MCP.

Вне рамок пилота: PROD-выпуск, IT-комитет, реестр систем, OpenMetadata, Prisma AIRS, корпоративный Keycloak, GitLab.

## 2. Решения, принятые 19.10.2026

| Вопрос | Решение |
|---|---|
| Куда деплоим приложения | Coolify по REST API (стенд из `ai-sandbox-tests-gcloud`) |
| Что идёт через LiteLLM | Только MCP; модель — напрямую по подписке |
| Как код попадает в Coolify | Публичный git-репозиторий (GitHub), Coolify клонирует и собирает сам |
| Где живут LiteLLM и MCP | Отдельная VM `sandbox-mcp` в GCP, docker compose |
| Стек | Python 3.13, официальный MCP SDK (FastMCP, streamable HTTP), FastAPI, SQLAlchemy 2, PostgreSQL, pytest, ruff, uv |

## 3. Архитектура

### 3.1. Компоненты на VM `sandbox-mcp`

| Сервис (compose) | Назначение |
|---|---|
| `caddy` | TLS (Let's Encrypt) на домене `mcp.<ip>.sslip.io`, единственный порт наружу — 443 |
| `litellm` | MCP Gateway. `mcp_servers.sandbox_mcp` → `http://sandbox-mcp:8080/mcp`. Virtual keys в PostgreSQL |
| `postgres` | Одна инсталляция, две базы: `litellm`, `sandbox` |
| `sandbox-mcp` | MCP-сервер (tools) + внутренний HTTP `/healthz`. Пишет задания, ничего долгого не делает |
| `sandbox-worker` | Тот же образ, команда `worker`. Забирает задания по lease, выполняет пайплайн |

Coolify живёт на другой VM (`sandbox-coolify`, стенд Codex). Worker ходит к нему по HTTPS с API-токеном.

### 3.2. Доверие и личность

- Claude Code → LiteLLM: заголовок `x-litellm-api-key: Bearer <virtual key>`. Ключ выдаётся на сотрудника.
- LiteLLM → Sandbox MCP: внутренняя docker-сеть, заголовок `Authorization: Bearer <SANDBOX_GATEWAY_TOKEN>`
  (статический сервисный токен из `.env`). Любой запрос без него — 401.
- Личность пользователя: LiteLLM пробрасывает заголовки с идентификатором ключа/пользователя
  (`x-litellm-*`). MCP читает их в `actor` и сохраняет в задании. Если заголовка нет — `actor = "unknown"`,
  вызов не отклоняется (пилот), но это фиксируется в логе. Объектные права (BDD 11): `status` по чужому
  `app_id`/`job_id` возвращает отказ, если `actor` известен и не совпадает с владельцем.

### 3.3. Поток данных

1. `assess_application` → запись `applications` (app_id, owner, repo, purpose, data_classes, kind, requirements_version).
2. `validate_and_deploy` → запись `jobs` (job_id, app_id, commit_sha, env, idempotency_key UNIQUE, state=`queued`).
3. Worker: `SELECT ... FOR UPDATE SKIP LOCKED` → lease на 10 минут → стадии ниже, каждая пишет `checks[]`
   и `progress`.
4. `status` читает запись и отдаёт её без обращения к Coolify (кроме стадии ожидания деплоя, где worker сам
   опрашивает Coolify и обновляет запись).

## 4. Инструменты MCP

Имена и контракт — из решений v4. Все аргументы и ответы — JSON, описания на русском.

### `assess_application`
Вход: `repo_url` (https, GitHub), `git_ref` (ветка или SHA), `app_name`, `purpose` (текст), `data_classes`
(список из `none | internal | personal | financial`).
Действия: валидация URL (только `https://github.com/<owner>/<repo>`), слаг из `app_name`, резервирование
`app_id` (ULID) и slug. Shallow clone в temp, `detect_kind`:
- `static` — `index.html` в корне или в `dist|build|public|out`;
- `node_build` — `package.json` со скриптом `build`;
- `dockerfile` — `Dockerfile` в корне;
- иначе `unsupported`.
Выход: `app_id`, `slug`, `kind`, `decision` (`allowed | needs_clarification | rejected`), `requirements`
(версия `R1`, список текстовых требований TEST-профиля), `reason` при отказе.
Правило (BDD 04): `personal` или `financial` в `data_classes` → `needs_clarification`, деплой закрыт.
`unsupported` → `rejected`.

### `validate_and_deploy`
Вход: `app_id`, `commit_sha` (40 hex), `env` (только `TEST`; иное → отказ с кодом `env_not_allowed`),
`idempotency_key`.
Действия: проверка владельца и `decision == allowed`; поиск задания по `idempotency_key` — если есть,
вернуть его (BDD 10); иначе создать `queued`.
Выход: `job_id`, `state`, `created` (bool).

### `status`
Вход: `job_id` или `app_id` (одно из двух).
Выход: `state` (`queued | running | succeeded | failed | blocked`), `stage`, `checks[]`
(`name`, `result` = `passed | failed | error | pending | skipped`, `summary`), `url`, `expires_at`,
`next_action` (текст для пользователя), `error` при `failed`.
`blocked` означает «проверки не прошли, нужно исправить код» (BDD 07); `failed` — ошибка платформы или
деплоя (BDD 08, 16). Они различаются в `next_action`.

## 5. Пайплайн worker

Стадии в порядке выполнения; каждая — отдельный класс с методом `run(ctx) -> CheckResult`:

| Стадия | Инструмент | Блокирует |
|---|---|---|
| `clone` | `git clone --depth 1` + `git checkout <sha>`; проверка, что SHA принадлежит `repo_url` | да, при ошибке `failed` |
| `detect` | `detect_kind` ещё раз на точном commit; сравнение с оценкой | да, если `unsupported` |
| `gitleaks` | `gitleaks detect --no-git --source . --report-format json` | да, при любой находке → `blocked` |
| `trivy` | `trivy fs --scanners vuln,misconfig --severity HIGH,CRITICAL --exit-code 1 .` | да, → `blocked` |
| `coolify_create` | `POST /api/v1/applications/public` (build_pack по `kind`: `static` → `static`, `node_build` → `nixpacks` с `is_static`, `dockerfile` → `dockerfile`); `domains = https://<slug>.<APPS_BASE_DOMAIN>`; при повторном деплое того же `app_id` — переиспользуем `coolify_uuid`, обновляем ветку/коммит | да |
| `coolify_deploy` | `POST /api/v1/deploy?uuid=...`; опрос `GET /api/v1/deployments/{uuid}` до `finished|failed`, таймаут 15 мин | да |
| `smoke` | `GET https://<slug>.<domain>/` ожидаем 200 за 60 с | да, → `failed` |
| `register_ttl` | `expires_at = now + 14 дней` | нет |

Таймаут или исключение в любой стадии → `result = error`, задание `failed`, допуск не выдаётся (BDD 08).
Отдельный тик worker раз в час: задания с истёкшим `expires_at` → `DELETE /api/v1/applications/{uuid}`,
состояние `expired` (BDD 18, упрощённо).

Сканеры — бинарники в образе worker (gitleaks, trivy, git). Пользовательский код исполняется только у Coolify,
worker его не собирает и не запускает.

## 6. Хранилище

PostgreSQL 16, Alembic-миграции. Таблицы:

- `applications`: `app_id` PK, `owner`, `slug` UNIQUE, `repo_url`, `app_name`, `purpose`, `data_classes` JSONB,
  `kind`, `decision`, `requirements_version`, `coolify_uuid` NULL, `created_at`.
- `jobs`: `job_id` PK, `app_id` FK, `commit_sha`, `env`, `idempotency_key` UNIQUE, `state`, `stage`,
  `checks` JSONB, `url`, `expires_at`, `error`, `lease_until`, `created_at`, `updated_at`.

Для юнит-тестов — SQLite через тот же SQLAlchemy (JSONB → JSON).

## 7. Конфигурация

Только через переменные `SANDBOX_*` (pydantic Settings), пример в `.env.example`:
`SANDBOX_DATABASE_URL`, `SANDBOX_GATEWAY_TOKEN`, `SANDBOX_COOLIFY_URL`, `SANDBOX_COOLIFY_TOKEN`,
`SANDBOX_COOLIFY_PROJECT_UUID`, `SANDBOX_COOLIFY_SERVER_UUID`, `SANDBOX_COOLIFY_ENVIRONMENT_NAME`,
`SANDBOX_APPS_BASE_DOMAIN`, `SANDBOX_TTL_DAYS=14`, `SANDBOX_COOLIFY_FAKE=true|false`.
При `SANDBOX_COOLIFY_FAKE=true` используется in-memory Coolify-клиент (пока стенда нет).
Секреты — в `.env` вне git и в `.secrets/`; в чат и логи не выводятся.

## 8. Инфраструктура и развёртывание

- `infra/create-vm.sh`: `gcloud compute instances create sandbox-mcp` (e2-medium, Ubuntu 24.04, 30 GB,
  `--project project-c6b97dc5-607e-455a-910 --account temirbulat_r@bi.group`, zone `europe-west3-a`),
  правило файрвола `tcp:443` для всех, SSH только через IAP. Startup-script ставит Docker.
- `infra/deploy.sh`: `gcloud compute scp` каталога `deploy/` и `docker compose up -d` по IAP-SSH.
- `deploy/compose.yaml`, `deploy/Caddyfile`, `deploy/litellm/config.yaml`, `deploy/.env.example`.
- Домен: `mcp.<внешний IP с дефисами>.sslip.io`, Caddy сам получает сертификат.
- Виртуальные ключи LiteLLM создаются скриптом `infra/litellm-key.sh <user>` через `/key/generate` с
  `mcp_servers: [sandbox_mcp]`.

## 9. Подключение Claude Code

```bash
claude mcp add --transport http sandbox https://mcp.<ip>.sslip.io/sandbox_mcp/mcp \
  --header "x-litellm-api-key: Bearer <virtual key>"
```

Проверка: в Claude Code видны `assess_application`, `validate_and_deploy`, `status`; сценарий
«оценить → задеплоить → статус с URL» проходит на тестовом публичном репозитории со статикой.

## 10. Тестирование

- Юнит (pytest, SQLite, fake Coolify, fake сканеры через подмену команд): детект типа, правила допуска,
  идемпотентность, переходы состояний, различие `blocked` / `failed`, права на `status`.
- Контракт tools: вызов через MCP-клиент из SDK к серверу in-process.
- Интеграция локально: `docker compose -f deploy/compose.yaml` с реальным LiteLLM и `SANDBOX_COOLIFY_FAKE=true`;
  `claude mcp add` на `http://localhost:4000/sandbox_mcp/mcp`.
- E2E на VM: тот же compose, реальный Coolify, публичный репозиторий со статикой.

## 11. Открытые вопросы

- Адрес и токен Coolify появятся после стенда Codex; до этого — fake-клиент.
- Какие именно заголовки с личностью пробрасывает LiteLLM MCP Gateway — проверяется на шаге интеграции;
  при отсутствии остаёмся с `actor = unknown` на пилоте.
- Базовый домен приложений (`APPS_BASE_DOMAIN`): wildcard через sslip.io на IP VM Coolify либо настоящий домен.
