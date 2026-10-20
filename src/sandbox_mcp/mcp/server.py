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
from sandbox_mcp.services.identity import DbIdentityResolver
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


_PLAYBOOKS = Path(__file__).parent / "playbooks"


def _playbook(name: str) -> str:
    """Prompt texts live next to the code as markdown, editable without touching Python."""
    return (_PLAYBOOKS / f"{name}.md").read_text(encoding="utf-8")


def build_mcp(repo: Repository, settings: Settings, resolver=None) -> MCPServer:
    resolver = resolver or DbIdentityResolver(repo)

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
            git_ref=git_ref,
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

    @mcp.prompt(
        name="publish",
        title="Опубликовать приложение в TEST",
        description="Пошаговый сценарий: подготовить репозиторий, запушить в GitHub, пройти оценку "
        "и выпустить приложение в песочницу TEST.",
    )
    def publish(
        repo_url: Annotated[
            str, Field(description="URL репозитория на GitHub, если он уже есть; иначе пусто")
        ] = "",
        app_name: Annotated[str, Field(description="Название приложения")] = "",
    ) -> str:
        """Инструкция для Claude: довести текущий проект до работающего URL в TEST."""
        repo_line = (
            f"Репозиторий уже указан: {repo_url}."
            if repo_url
            else "Репозиторий не указан: проверь `git remote -v`; если remote нет, создай "
            "публичный репозиторий командой `gh repo create <name> --public --source . --push` "
            "(нужен `gh auth login`) или попроси пользователя создать его на github.com и "
            "дать URL."
        )
        name_line = (
            f"Название приложения: {app_name}."
            if app_name
            else ("Название приложения спроси у пользователя или возьми из package.json / README.")
        )
        return _playbook("publish").format(repo_line=repo_line, name_line=name_line)

    @mcp.prompt(
        name="classify",
        title="Классификация разработки T0–T4",
        description="Определить категорию (уровень риска T0–T4) идеи или приложения по данным, "
        "аудитории и интеграциям; что дальше для каждого уровня.",
    )
    def classify(
        idea: Annotated[
            str,
            Field(description="Описание идеи или приложения; пусто, если смотреть текущий проект"),
        ] = "",
    ) -> str:
        """Инструкция для Claude: присвоить уровень T0–T4 с модификаторами и объяснить маршрут."""
        idea_line = (
            f"Идея или приложение: {idea}"
            if idea
            else "Описание не передано: изучи текущий проект (README, схема данных, интеграции) и "
            "уточни у пользователя назначение."
        )
        return _playbook("classify").format(idea_line=idea_line)

    @mcp.prompt(
        name="check_status",
        title="Проверить статус выпуска",
        description="Как читать ответ status и что делать при blocked, failed и succeeded.",
    )
    def check_status(
        job_id: Annotated[str, Field(description="job_id из validate_and_deploy")] = "",
    ) -> str:
        """Инструкция для Claude: опросить status и объяснить результат пользователю."""
        target = (
            f"Вызови status с job_id={job_id}."
            if job_id
            else ("Вызови status с job_id последнего validate_and_deploy (или app_id приложения).")
        )
        return _playbook("check_status").format(target=target)

    return mcp
