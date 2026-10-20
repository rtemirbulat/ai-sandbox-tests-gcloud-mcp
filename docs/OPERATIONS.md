# Sandbox MCP · runbook для DevOps

Снимок 20.10.2026. Пилот, не production: одна VM на роль, без HA и бэкапов.

## 1. Инвентарь

GCP-проект `project-c6b97dc5-607e-455a-910`, аккаунт `temirbulat_r@bi.group`, зона `europe-west3-a`.
Все команды `gcloud` ниже подразумевают `--project project-c6b97dc5-607e-455a-910 --account temirbulat_r@bi.group`.

| VM | Внешний IP | Внутренний IP | Тип | Диск | ОС / Docker | Сеть, теги | Расписание |
|---|---|---|---|---|---|---|---|
| `sandbox-mcp` | 35.234.71.242 (эфемерный) | 10.156.0.2 | e2-medium, 2 vCPU / 4 GB | 30 GB pd-balanced | Ubuntu 24.04, Docker 29.9, Compose 5.6 | `default`, тег `mcp` | нет, работает постоянно |
| `sandbox-coolify` | 34.179.210.136 (статический `sandbox-coolify-ip`) | 10.88.0.2 | e2-standard-2, 2 vCPU / 8 GB | 50 GB pd-balanced | Ubuntu 24.04, Docker 29.9 | отдельная VPC стенда, теги `paas-eval`, `paas-eval-web` | `terminationTime` 2026-10-21T18:59Z (23:59 Актобе), после этого VM STOP |

Домены без регистрации через sslip.io: `mcp.35-234-71-242.sslip.io` (шлюз), `coolify.34-179-210-136.sslip.io` (панель Coolify),
`<slug>.sandbox.34-179-210-136.sslip.io` (приложения TEST). Смена IP `sandbox-mcp` сломает домен и сертификат: при необходимости
зарезервировать статический адрес или перейти на корпоративный DNS.

Репозитории: код MCP `ai-sandbox-tests-gcloud-mcp` (локально, на GitHub не запушен; remote `rtemirbulat/ai-sandbox-tests-gcloud-mcp`),
стенд Coolify `ai-sandbox-tests-gcloud` (папка `evaluations/coolify`, `infrastructure.md`).

## 2. Что крутится

### VM sandbox-mcp · `/opt/sandbox-mcp` · `docker compose -f deploy/compose.yaml --env-file deploy/.env`

| Сервис | Образ | Порты | Назначение | Состояние |
|---|---|---|---|---|
| `caddy` | caddy:2 | 0.0.0.0:80, 0.0.0.0:443 | TLS (Let's Encrypt, авто), reverse proxy → litellm:4000 | volume `deploy_caddy_data` (сертификаты) |
| `litellm` | ghcr.io/berriai/litellm:v1.94.1 (зафиксировано 20.10.2026) | 127.0.0.1:4000 | MCP Gateway: virtual keys, `/sandbox_mcp/mcp`, логи вызовов | база `litellm` в postgres |
| `sandbox-mcp` | sandbox-mcp:local (собирается из `Dockerfile`) | 8080 только в сети compose | MCP-сервер, 3 tools, принимает только `X-Gateway-Token` | stateless |
| `sandbox-worker` | тот же образ, команда `worker` | нет | клон по SHA, Gitleaks 8.30, Trivy 0.76, Coolify API, smoke, TTL-уборка раз в час | volume `deploy_trivy_cache` |
| `postgres` | postgres:16 | 5432 только в сети compose | базы `sandbox` (applications, jobs, user_tokens) и `litellm` | volume `deploy_pg_data` |

Ресурсы на 20.10: диск 9.8 из 29 GB (из них 2.3 GB build cache, можно чистить), RAM 1.6 из 3.9 GB.

### VM sandbox-coolify · Coolify 4.4.6, данные в `/data/coolify`

| Контейнер | Порты на хосте | Назначение |
|---|---|---|
| `coolify-proxy` (traefik v3.7) | 0.0.0.0:80, 443, 8080 | ingress и Let's Encrypt для панели и всех приложений |
| `coolify` 4.4.6 | 0.0.0.0:8000 (панель), 6001-6002 | панель и REST API `/api/v1`; firewall снаружи пропускает только 80/443 |
| `coolify-db` postgres:15, `coolify-redis`, `coolify-sentinel` | внутренние | служебные |
| `<uuid>-<timestamp>` | 80 внутри | контейнеры приложений: наши TEST-приложения (проект `bi-sandbox`) и пробные приложения Codex (image/git/probe, lab-*, sso-lab с Keycloak и oauth2-proxy) |

Ресурсы на 20.10: диск 7.2 из 48 GB, RAM 2.1 из 7.9 GB.

## 3. Сеть и доступ

Firewall проекта:

| Правило | Источник | Порты | Цель | Комментарий |
|---|---|---|---|---|
| `allow-https-mcp` | 0.0.0.0/0 | 80, 443 | тег `mcp` | публичный вход шлюза |
| `allow-iap-ssh` | 35.235.240.0/20 | 22 | тег `mcp` | SSH через IAP |
| `paas-eval-web`, `paas-eval-iap-ssh` | 0.0.0.0/0, IAP | 80/443, 22 | теги стенда Coolify | |
| `default-allow-ssh`, `default-allow-rdp`, `default-allow-icmp` | 0.0.0.0/0 | 22, 3389, icmp | все VM сети `default` | **стандартные правила GCP; `sandbox-mcp` в сети `default`, поэтому 22 открыт всему интернету (вход только по ключам). Рекомендация: удалить `default-allow-ssh` и `default-allow-rdp`, оставить IAP.** |

SSH: `gcloud compute ssh sandbox-mcp --zone europe-west3-a --tunnel-through-iap` (ключ по OS Login / метаданным аккаунта);
Coolify: `gcloud compute ssh paaseval@sandbox-coolify --zone europe-west3-a --tunnel-through-iap --ssh-key-file <repo стенда>/.secrets/gcloud-eval`.

Публичный IP шлюза уже сканируют боты (видно в логах LiteLLM как 404 на `/.env`, `/wp-*`). Без ключа LiteLLM отвечает 401.

## 4. Секреты

| Где | Что | Назначение |
|---|---|---|
| `deploy/.env` (локально и `/opt/sandbox-mcp/deploy/.env` на VM), копия `.secrets/vm-deploy.env` | `POSTGRES_PASSWORD`, `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`, `SANDBOX_GATEWAY_TOKEN`, `SANDBOX_COOLIFY_TOKEN`, uuid проекта/сервера Coolify, `MCP_DOMAIN`, `SANDBOX_APPS_BASE_DOMAIN` | конфигурация стека |
| `.secrets/coolify-token.txt` | API-токен Coolify `sandbox-mcp` (root, team-bound) | тот же, что в `.env` |
| `.secrets/vkey-<login>.json` | LiteLLM virtual key пользователя (`object_permission.mcp_servers: [sandbox_mcp]`) | доступ сотрудника к шлюзу |
| `.secrets/sandbox-token-<login>.txt` | токен песочницы (в базе только sha256) | личность сотрудника |
| Coolify VM `/root/sandbox-mcp-token`, `/root/coolify-eval-token` | токены Coolify API | созданы через `php artisan tinker` |

`.secrets/` и `deploy/.env` в `.gitignore`. Ротация: `LITELLM_MASTER_KEY` и `SANDBOX_GATEWAY_TOKEN` меняются в `.env` + `infra/deploy.sh` +
`docker compose restart litellm`; virtual key — `POST /key/delete` + `infra/litellm-key.sh`; токен песочницы — новая запись через `sandbox-mcp token <login>`,
старую строку удалить из `user_tokens` вручную (команды отзыва пока нет); токен Coolify — удалить в панели Coolify (Security → API tokens) и выпустить новый.

## 5. Повседневные операции

```bash
# деплой новой версии (сборка образа на VM, рестарт изменившихся контейнеров)
infra/deploy.sh
# конфиг LiteLLM монтируется файлом: после его правки нужен явный рестарт
gcloud compute ssh sandbox-mcp --zone europe-west3-a --tunnel-through-iap --command \
  'cd /opt/sandbox-mcp && sudo docker compose -f deploy/compose.yaml --env-file deploy/.env restart litellm'
# логи
... --command 'cd /opt/sandbox-mcp && sudo docker compose -f deploy/compose.yaml --env-file deploy/.env logs --tail 200 -f sandbox-worker'
# состояние заданий
... --command 'cd /opt/sandbox-mcp && sudo docker compose -f deploy/compose.yaml --env-file deploy/.env exec -T postgres psql -U sandbox -d sandbox -c "select job_id,state,stage,url,updated_at from jobs order by created_at desc limit 20;"'
# выдать доступ сотруднику
export LITELLM_MASTER_KEY=$(grep ^LITELLM_MASTER_KEY= deploy/.env | cut -d= -f2-)  # не source: в значениях бывают | и $
infra/litellm-key.sh https://mcp.35-234-71-242.sslip.io "$LITELLM_MASTER_KEY" <login>
infra/sandbox-token.sh <login>
# сквозная проверка
.venv/bin/python scripts/e2e_gateway.py https://mcp.35-234-71-242.sslip.io/sandbox_mcp/mcp <vkey> <token> https://github.com/github/choosealicense.com gh-pages --redeploy
```

Health: `GET https://mcp.35-234-71-242.sslip.io/health/liveliness` (LiteLLM), `sandbox-mcp` отвечает `/healthz` внутри сети compose
(`docker compose exec sandbox-mcp python -c "import httpx;print(httpx.get('http://localhost:8080/healthz').text)"`).

Coolify VM: запуск после планового STOP — `gcloud compute instances start sandbox-coolify --zone europe-west3-a`; если `terminationTime`
в прошлом, сначала `gcloud compute instances update sandbox-coolify --zone europe-west3-a --clear-max-run-duration` (или новое время).
Контейнеры приложений поднимаются сами (restart policy Coolify).

## 6. Известные риски и что сделать до расширения пилота

1. **Версия LiteLLM зафиксирована (`v1.94.1`)**: обновлять осознанно, после прогона `scripts/e2e_gateway.py`.
2. **Эфемерный IP `sandbox-mcp`**: смена адреса меняет домен sslip.io и ломает подключения у всех сотрудников. Зарезервировать статический IP.
3. **Нет бэкапов `deploy_pg_data`**: потеря базы = потеря user_tokens и истории заданий (ключи LiteLLM тоже в этой базе). Минимум: `pg_dump` по cron в GCS-бакет или снапшоты диска.
4. **`default-allow-ssh` и `default-allow-rdp`** на сети `default`: закрыть, оставить IAP.
5. **Coolify VM по расписанию выключается 21.10**: продлить или снять лимит, иначе выпуски падают в `failed` на `coolify_create`.
6. **Чужой код исполняется на хосте Coolify** (Docker build + контейнер приложения без gVisor): приемлемо для публичных статических репозиториев в пилоте, для node/dockerfile нужен отдельный нод или runtime-изоляция.
7. **Один worker, lease 10 минут**: при падении worker задание переподхватится через 10 минут; масштабирование — вторая реплика `sandbox-worker` (claim защищён `FOR UPDATE SKIP LOCKED`).
8. **Логи только в stdout контейнеров**: для корпоративного контура нужны JSON-логи в Elastic и алерты (диск, очередь заданий, 5xx шлюза).
9. **Публичные GitHub-репозитории как единственный источник**: для приватных нужен deploy key в Coolify или GitHub App.
10. **Нет Alembic**: схема через `ensure_schema` (create_all + аддитивные ALTER). Для прод-перехода завести миграции.
