import os
import stat

import httpx
import pytest

from sandbox_mcp.services import pipeline
from sandbox_mcp.services.coolify import AppSpec, FakeCoolifyClient, HttpCoolifyClient
from sandbox_mcp.services.scanners import CheckResult, run_gitleaks, run_trivy
from sandbox_mcp.settings import Settings

# scanners


@pytest.fixture
def path_with(tmp_path, monkeypatch):
    def _set(name, body):
        d = tmp_path / "bin"
        d.mkdir(exist_ok=True)
        f = d / name
        f.write_text("#!/bin/sh\n" + body + "\n")
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")

    return _set


def test_gitleaks_clean(path_with, tmp_path):
    path_with("gitleaks", "exit 0")
    r = run_gitleaks(tmp_path / "src")
    assert r.name == "gitleaks" and r.result == "passed"


def test_gitleaks_findings(path_with, tmp_path):
    path_with("gitleaks", 'echo "[{\\"RuleID\\":\\"aws\\"}]" > "$REPORT"; exit 1')
    r = run_gitleaks(tmp_path / "src")
    assert r.result == "failed" and "1" in r.summary


def test_gitleaks_crash_is_error(path_with, tmp_path):
    path_with("gitleaks", "echo boom >&2; exit 2")
    assert run_gitleaks(tmp_path / "src").result == "error"


def test_trivy_findings(path_with, tmp_path):
    path_with("trivy", "echo found; exit 1")
    r = run_trivy(tmp_path)
    assert r.result == "failed" and "found" in r.summary


def test_trivy_fatal_is_error(path_with, tmp_path):
    path_with("trivy", "echo 'FATAL Fatal error run error: DB error' >&2; exit 1")
    assert run_trivy(tmp_path).result == "error"


def test_missing_binary_is_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert run_trivy(tmp_path).result == "error"
    assert run_gitleaks(tmp_path).result == "error"


# coolify


def spec():
    return AppSpec(
        name="demo",
        repo_url="https://github.com/o/r",
        branch="main",
        commit_sha="a" * 40,
        build_pack="static",
        publish_directory="/",
        domain="https://demo.apps.example",
    )


def test_fake_lifecycle():
    c = FakeCoolifyClient()
    uuid = c.create_application(spec())
    dep = c.deploy(uuid)
    assert c.deployment_status(dep) == "finished"
    assert c.application_url(uuid) == "https://demo.apps.example"
    c.delete_application(uuid)
    assert uuid not in c.apps


def test_http_client_calls():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path, req.headers.get("authorization")))
        if req.url.path == "/api/v1/applications/public":
            return httpx.Response(201, json={"uuid": "app-1"})
        if req.url.path == "/api/v1/deploy":
            return httpx.Response(200, json={"deployments": [{"deployment_uuid": "dep-1"}]})
        if req.url.path == "/api/v1/deployments/dep-1":
            return httpx.Response(200, json={"status": "finished"})
        if req.url.path == "/api/v1/applications/app-1":
            return httpx.Response(200, json={"fqdn": "https://demo.apps.example"})
        return httpx.Response(404)

    c = HttpCoolifyClient(
        "https://coolify.test",
        "tok",
        project_uuid="p",
        server_uuid="s",
        environment_name="production",
        transport=httpx.MockTransport(handler),
    )
    uuid = c.create_application(spec())
    dep = c.deploy(uuid)
    assert uuid == "app-1" and dep == "dep-1"
    assert c.deployment_status(dep) == "finished"
    assert c.application_url(uuid) == "https://demo.apps.example"
    assert all(a == "Bearer tok" for _, _, a in calls)


# pipeline


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        gateway_token="t",
        workdir=str(tmp_path / "work"),
        apps_base_domain="apps.test",
        deploy_timeout_seconds=5,
    )


@pytest.fixture
def quiet_scanners(monkeypatch):
    monkeypatch.setattr(pipeline, "run_gitleaks", lambda p: CheckResult("gitleaks", "passed"))
    monkeypatch.setattr(pipeline, "run_trivy", lambda p: CheckResult("trivy", "passed"))
    monkeypatch.setattr(pipeline, "smoke_get", lambda url, timeout: CheckResult("smoke", "passed"))


def _app(repo, url, kind="static"):
    return repo.create_application(
        owner="u",
        repo_url=url,
        app_name="Demo",
        purpose="p",
        data_classes=["none"],
        kind=kind,
        decision="allowed",
        requirements_version="R1",
    )


def test_happy_path_then_redeploy(repo, settings, local_repo, quiet_scanners):
    url, sha = local_repo
    app = _app(repo, url)
    job, _ = repo.get_or_create_job(app.app_id, sha, "TEST", "k")
    coolify = FakeCoolifyClient()
    pipeline.run_job(job.job_id, repo, settings, coolify)
    j = repo.get_job(job.job_id)
    assert j.state == "succeeded" and j.url == "https://demo.apps.test" and j.expires_at
    assert [c["name"] for c in j.checks] == [
        "clone", "detect", "gitleaks", "trivy", "coolify_create", "coolify_deploy", "smoke",
    ]  # fmt: skip
    assert repo.get_application(app.app_id).coolify_uuid == "fake-app-1"
    assert j.checks[-1]["result"] == "skipped"  # fake Coolify: smoke is skipped
    # second deploy reuses the Coolify application
    job2, _ = repo.get_or_create_job(app.app_id, sha, "TEST", "k2")
    pipeline.run_job(job2.job_id, repo, settings, coolify)
    assert repo.get_job(job2.job_id).state == "succeeded" and len(coolify.apps) == 1


def test_gitleaks_blocks(repo, settings, local_repo, monkeypatch):
    url, sha = local_repo
    monkeypatch.setattr(
        pipeline, "run_gitleaks", lambda p: CheckResult("gitleaks", "failed", "Найдено секретов: 1")
    )
    app = _app(repo, url)
    job, _ = repo.get_or_create_job(app.app_id, sha, "TEST", "k")
    pipeline.run_job(job.job_id, repo, settings, FakeCoolifyClient())
    j = repo.get_job(job.job_id)
    assert j.state == "blocked" and j.stage == "gitleaks" and "секретов" in j.error
    assert j.checks[-1]["result"] == "failed" and len(j.checks) == 3


def test_scanner_error_fails(repo, settings, local_repo, monkeypatch):
    url, sha = local_repo
    monkeypatch.setattr(pipeline, "run_gitleaks", lambda p: CheckResult("gitleaks", "error", "x"))
    app = _app(repo, url)
    job, _ = repo.get_or_create_job(app.app_id, sha, "TEST", "k")
    pipeline.run_job(job.job_id, repo, settings, FakeCoolifyClient())
    assert repo.get_job(job.job_id).state == "failed"


def test_bad_sha_fails_at_clone(repo, settings, local_repo):
    url, _ = local_repo
    app = _app(repo, url)
    job, _ = repo.get_or_create_job(app.app_id, "b" * 40, "TEST", "k")
    pipeline.run_job(job.job_id, repo, settings, FakeCoolifyClient())
    j = repo.get_job(job.job_id)
    assert j.state == "failed" and j.stage == "clone" and j.error


def test_deploy_failure_is_failed(repo, settings, local_repo, quiet_scanners):
    url, sha = local_repo
    app = _app(repo, url)
    job, _ = repo.get_or_create_job(app.app_id, sha, "TEST", "k")
    coolify = FakeCoolifyClient()
    coolify.deployment_status = lambda dep: "failed"
    pipeline.run_job(job.job_id, repo, settings, coolify)
    j = repo.get_job(job.job_id)
    assert j.state == "failed" and j.stage == "coolify_deploy"
