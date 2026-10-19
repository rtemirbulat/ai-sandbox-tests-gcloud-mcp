import logging
import shutil
import time
from datetime import timedelta
from pathlib import Path

import httpx

from sandbox_mcp.db.models import utcnow
from sandbox_mcp.db.repo import Repository
from sandbox_mcp.services.coolify import AppSpec, CoolifyClient, CoolifyError
from sandbox_mcp.services.detect import detect_kind, publish_directory
from sandbox_mcp.services.git import GitError, clone_at
from sandbox_mcp.services.scanners import CheckResult, run_gitleaks, run_trivy
from sandbox_mcp.settings import Settings

log = logging.getLogger(__name__)
BUILD_PACK = {"static": "static", "node_build": "nixpacks", "dockerfile": "dockerfile"}
FINAL_DEPLOY_STATES = ("finished", "failed", "cancelled-by-user")


class Blocked(Exception):
    """Проверка не пройдена: нужно исправить код (state=blocked)."""


class Failed(Exception):
    """Ошибка платформы или деплоя (state=failed)."""


def smoke_get(url: str, timeout: int = 60) -> CheckResult:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                return CheckResult("smoke", "passed", f"GET {url} → 200")
            last = f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            last = type(e).__name__
        time.sleep(3)
    return CheckResult("smoke", "error", f"GET {url}: {last or 'нет ответа'} за {timeout} с")


def run_job(job_id: str, repo: Repository, settings: Settings, coolify: CoolifyClient) -> None:
    job = repo.get_job(job_id)
    app = repo.get_application(job.app_id)
    checks: list[dict] = []
    workdir = Path(settings.workdir) / job_id
    src = workdir / "src"
    shutil.rmtree(workdir, ignore_errors=True)

    def record(check: CheckResult, stage: str) -> None:
        checks.append(check.as_dict())
        repo.update_job(job_id, stage=stage, checks=list(checks))
        if check.result == "failed":
            raise Blocked(check.summary)
        if check.result == "error":
            raise Failed(check.summary)

    try:
        repo.update_job(job_id, stage="clone")
        try:
            sha = clone_at(app.repo_url, job.commit_sha, src)
        except GitError as e:
            record(CheckResult("clone", "error", str(e)), "clone")
        record(CheckResult("clone", "passed", f"commit {sha[:12]}"), "clone")

        kind = detect_kind(src)
        if kind == "unsupported":
            record(CheckResult("detect", "failed", "Тип проекта не распознан"), "detect")
        record(CheckResult("detect", "passed", kind), "detect")

        record(run_gitleaks(src), "gitleaks")
        record(run_trivy(src), "trivy")

        domain = f"https://{app.slug}.{settings.apps_base_domain}"
        spec = AppSpec(
            name=f"{app.slug}-{app.app_id[-6:]}",
            repo_url=app.repo_url,
            branch="main",
            commit_sha=sha,
            build_pack=BUILD_PACK[kind],
            publish_directory=publish_directory(src),
            domain=domain,
        )
        url = domain
        try:
            uuid = app.coolify_uuid
            if uuid:
                coolify.update_source(uuid, spec)
            else:
                uuid = coolify.create_application(spec)
                repo.set_coolify_uuid(app.app_id, uuid)
            record(CheckResult("coolify_create", "passed", f"application {uuid}"), "coolify_create")

            dep = coolify.deploy(uuid)
            deadline = time.time() + settings.deploy_timeout_seconds
            status = "queued"
            while time.time() < deadline:
                status = coolify.deployment_status(dep)
                if status in FINAL_DEPLOY_STATES:
                    break
                time.sleep(5)
            if status != "finished":
                record(
                    CheckResult("coolify_deploy", "error", f"deployment {dep}: {status}"),
                    "coolify_deploy",
                )
            record(CheckResult("coolify_deploy", "passed", f"deployment {dep}"), "coolify_deploy")
            url = coolify.application_url(uuid) or domain
        except CoolifyError as e:
            record(CheckResult("coolify_deploy", "error", str(e)), "coolify_deploy")

        if settings.coolify_fake:
            record(
                CheckResult("smoke", "skipped", "Coolify в fake-режиме, URL не проверялся"), "smoke"
            )
        else:
            record(smoke_get(url, 60), "smoke")

        repo.update_job(
            job_id,
            state="succeeded",
            stage="done",
            url=url,
            error=None,
            expires_at=utcnow() + timedelta(days=settings.ttl_days),
        )
    except Blocked as e:
        repo.update_job(job_id, state="blocked", error=str(e))
    except Failed as e:
        repo.update_job(job_id, state="failed", error=str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("job %s crashed", job_id)
        repo.update_job(job_id, state="failed", error=f"internal: {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
