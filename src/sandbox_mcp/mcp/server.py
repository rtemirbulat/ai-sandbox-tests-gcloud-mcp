import re
import shutil
import tempfile
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from sandbox_mcp.db.repo import Repository
from sandbox_mcp.services.detect import detect_kind
from sandbox_mcp.services.git import GitError, clone_at, validate_github_url
from sandbox_mcp.services.identity import IdentityResolver
from sandbox_mcp.services.rules import assess
from sandbox_mcp.settings import Settings

_SHA = re.compile(r"^[0-9a-f]{40}$")
NEXT_ACTION = {
    "queued": "Задание в очереди. Вызовите status через 30–60 секунд.",
    "running": "Идут проверки и деплой. Вызовите status через 30–60 секунд.",
    "succeeded": "Приложение доступно по url до expires_at. Проверьте его и соберите "
    "обратную связь.",
    "blocked": "Проверки не пройдены: исправьте код по замечаниям в checks, сделайте новый "
    "commit и вызовите validate_and_deploy с новым commit_sha.",
    "failed": "Ошибка платформы или деплоя. Повторите validate_and_deploy с новым "
    "idempotency_key; если повторяется, передайте job_id в поддержку песочницы.",
    "expired": "Срок жизни TEST истёк, приложение удалено. Для нового выпуска вызовите "
    "validate_and_deploy.",
}

INSTRUCTIONS = (
    "Песочница BI. Порядок: 1) assess_application — оценка репозитория и допуск к TEST; "
    "2) validate_and_deploy — задание на проверку commit (Gitleaks, Trivy) и деплой в TEST; "
    "3) status — прогресс, результаты проверок, URL и срок жизни. PROD не поддерживается. "
    "Перед assess_application убедитесь, что код запушен в публичный GitHub-репозиторий."
)


def _owns(actor: str, owner: str) -> bool:
    return actor == "unknown" or owner == "unknown" or actor == owner


def _ensure(path: str) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def build_mcp(repo: Repository, settings: Settings, resolver=None) -> MCPServer:
    resolver = resolver or IdentityResolver(
        settings.litellm_url, cache_seconds=settings.identity_cache_seconds
    )

    def actor_from(ctx: Context) -> str:
        return resolver.resolve(ctx.headers)

    mcp = MCPServer("sandbox", version="0.1.0", instructions=INSTRUCTIONS)

    @mcp.tool()
    def assess_application(
        repo_url: Annotated[
            str, Field(description="Публичный репозиторий https://github.com/<owner>/<repo>")
        ],
        git_ref: Annotated[str, Field(description="Ветка, тег или 40-символьный SHA")],
        app_name: Annotated[str, Field(description="Название приложения (станет частью URL)")],
        purpose: Annotated[str, Field(description="Назначение и бизнес-польза, 1–3 предложения")],
        data_classes: Annotated[
            list[str],
            Field(description="Классы данных: none, internal, personal, financial"),
        ],
        ctx: Context,
    ) -> dict[str, object]:
        """Оценить приложение: тип проекта, допуск к TEST и требования версии R1."""
        try:
            url = validate_github_url(repo_url)
        except GitError as e:
            return {"decision": "rejected", "reason": str(e)}
        tmp = Path(tempfile.mkdtemp(prefix="assess-", dir=_ensure(settings.workdir)))
        try:
            try:
                sha = clone_at(url, git_ref, tmp / "src")
            except GitError as e:
                return {"decision": "rejected", "reason": f"Не удалось получить код: {e}"}
            kind = detect_kind(tmp / "src")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        a = assess(kind, data_classes)
        app = repo.create_application(
            owner=actor_from(ctx),
            repo_url=url,
            app_name=app_name,
            purpose=purpose,
            data_classes=data_classes,
            kind=kind,
            decision=a.decision,
            requirements_version=a.requirements_version,
        )
        return {
            "app_id": app.app_id,
            "slug": app.slug,
            "kind": kind,
            "commit_sha": sha,
            "decision": a.decision,
            "reason": a.reason,
            "requirements_version": a.requirements_version,
            "requirements": a.requirements,
        }

    @mcp.tool()
    def validate_and_deploy(
        app_id: Annotated[str, Field(description="app_id из assess_application")],
        commit_sha: Annotated[str, Field(description="40-символьный SHA коммита для выпуска")],
        env: Annotated[str, Field(description="Окружение; поддерживается только TEST")],
        idempotency_key: Annotated[
            str,
            Field(
                description="Уникальный ключ запроса; повтор с тем же ключом вернёт то же задание"
            ),
        ],
        ctx: Context,
    ) -> dict[str, object]:
        """Поставить задание: проверить commit (Gitleaks, Trivy) и развернуть его в TEST."""
        if env != "TEST":
            return {
                "error": "env_not_allowed",
                "message": "Выпуск возможен только в TEST; PROD требует отдельного согласования",
            }
        if not _SHA.match(commit_sha):
            return {"error": "bad_commit_sha", "message": "Нужен полный 40-символьный SHA"}
        app = repo.get_application(app_id)
        if app is None or not _owns(actor_from(ctx), app.owner):
            return {"error": "not_found", "message": "Приложение не найдено или недоступно"}
        if app.decision != "allowed":
            return {
                "error": "not_allowed",
                "message": f"Решение оценки: {app.decision}. Повторите assess_application "
                "после уточнения данных",
            }
        job, created = repo.get_or_create_job(app_id, commit_sha, env, idempotency_key)
        return {"job_id": job.job_id, "state": job.state, "created": created}

    @mcp.tool()
    def status(
        ctx: Context,
        job_id: Annotated[str | None, Field(description="job_id из validate_and_deploy")] = None,
        app_id: Annotated[
            str | None, Field(description="app_id: вернуть последнее задание приложения")
        ] = None,
    ) -> dict[str, object]:
        """Статус задания: стадия, результаты проверок, URL, срок жизни и следующий шаг."""
        if job_id:
            job = repo.get_job(job_id)
        elif app_id:
            job = repo.latest_job_for_app(app_id)
        else:
            job = None
        if job is None:
            return {"error": "not_found", "message": "Задание не найдено"}
        app = repo.get_application(job.app_id)
        if not _owns(actor_from(ctx), app.owner):
            return {"error": "not_found", "message": "Задание не найдено"}
        return {
            "job_id": job.job_id,
            "app_id": job.app_id,
            "commit_sha": job.commit_sha,
            "env": job.env,
            "state": job.state,
            "stage": job.stage,
            "checks": job.checks,
            "url": job.url,
            "expires_at": job.expires_at.isoformat() if job.expires_at else None,
            "error": job.error,
            "next_action": NEXT_ACTION.get(job.state, ""),
        }

    return mcp
