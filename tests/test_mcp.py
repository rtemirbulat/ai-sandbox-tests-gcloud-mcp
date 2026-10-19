from datetime import timedelta

import httpx
import pytest
from mcp import Client

from sandbox_mcp import worker
from sandbox_mcp.db.models import utcnow
from sandbox_mcp.mcp.app import build_app
from sandbox_mcp.mcp.server import build_mcp
from sandbox_mcp.services.coolify import FakeCoolifyClient
from sandbox_mcp.services.identity import StaticIdentityResolver
from sandbox_mcp.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def mcp(repo, tmp_path, monkeypatch):
    s = Settings(_env_file=None, gateway_token="t", workdir=str(tmp_path / "w"))
    # tests use local paths instead of github URLs
    monkeypatch.setattr("sandbox_mcp.mcp.server.validate_github_url", lambda u: u)
    return build_mcp(repo, s, resolver=StaticIdentityResolver("tester"))


async def _assess(c, local_repo, name="Demo", classes=None):
    return (
        await c.call_tool(
            "assess_application",
            {
                "repo_url": local_repo,
                "git_ref": "main",
                "app_name": name,
                "purpose": "тест",
                "data_classes": classes or ["none"],
            },
        )
    ).structured_content


async def test_list_tools(mcp):
    async with Client(mcp) as c:
        names = sorted(t.name for t in (await c.list_tools()).tools)
    assert names == ["assess_application", "status", "validate_and_deploy"]


async def test_assess_then_deploy_then_status(mcp, local_repo):
    path, sha = local_repo
    async with Client(mcp, raise_exceptions=True) as c:
        a = await _assess(c, path)
        assert a["decision"] == "allowed" and a["kind"] == "static" and a["commit_sha"] == sha
        args = {"app_id": a["app_id"], "commit_sha": sha, "env": "TEST", "idempotency_key": "k1"}
        d = (await c.call_tool("validate_and_deploy", args)).structured_content
        assert d["created"] is True and d["state"] == "queued"
        d2 = (await c.call_tool("validate_and_deploy", args)).structured_content
        assert d2["created"] is False and d2["job_id"] == d["job_id"]
        s = (await c.call_tool("status", {"job_id": d["job_id"]})).structured_content
        assert s["state"] == "queued" and "status" in s["next_action"]
        s2 = (await c.call_tool("status", {"app_id": a["app_id"]})).structured_content
        assert s2["job_id"] == d["job_id"]
        assert (await c.call_tool("status", {})).structured_content["error"] == "not_found"


async def test_prod_and_bad_sha_rejected(mcp, local_repo):
    path, sha = local_repo
    async with Client(mcp, raise_exceptions=True) as c:
        a = await _assess(c, path)
        base = {"app_id": a["app_id"], "idempotency_key": "k"}
        d = (
            await c.call_tool("validate_and_deploy", {**base, "commit_sha": sha, "env": "PROD"})
        ).structured_content
        assert d["error"] == "env_not_allowed"
        d = (
            await c.call_tool("validate_and_deploy", {**base, "commit_sha": "main", "env": "TEST"})
        ).structured_content
        assert d["error"] == "bad_commit_sha"


async def test_personal_data_blocks_deploy(mcp, local_repo):
    path, sha = local_repo
    async with Client(mcp, raise_exceptions=True) as c:
        a = await _assess(c, path, name="PD", classes=["personal"])
        assert a["decision"] == "needs_clarification"
        d = (
            await c.call_tool(
                "validate_and_deploy",
                {"app_id": a["app_id"], "commit_sha": sha, "env": "TEST", "idempotency_key": "k"},
            )
        ).structured_content
        assert d["error"] == "not_allowed"


async def test_bad_repo_rejected(mcp, tmp_path):
    async with Client(mcp, raise_exceptions=True) as c:
        a = await _assess(c, str(tmp_path / "nope"))
        assert a["decision"] == "rejected" and "Не удалось получить код" in a["reason"]


# Starlette app auth


async def test_healthz_open_and_mcp_requires_token(tmp_path):
    s = Settings(
        _env_file=None,
        gateway_token="secret",
        database_url=f"sqlite:///{tmp_path}/a.sqlite3",
        workdir=str(tmp_path),
    )
    app = build_app(s)
    async with app.router.lifespan_context(app):
        tr = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=tr, base_url="http://t") as c:
            assert (await c.get("/healthz")).status_code == 200
            assert (await c.post("/mcp", json={})).status_code == 401
            r = await c.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401
            r = await c.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={
                    "Authorization": "Bearer secret",
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
            )
            assert r.status_code != 401


# worker


def test_tick_and_sweep(repo, monkeypatch):
    app = repo.create_application(
        owner="u",
        repo_url="https://github.com/o/r",
        app_name="D",
        purpose="p",
        data_classes=["none"],
        kind="static",
        decision="allowed",
        requirements_version="R1",
    )
    job, _ = repo.get_or_create_job(app.app_id, "a" * 40, "TEST", "k")
    ran = []
    monkeypatch.setattr(worker, "run_job", lambda jid, r, s, c: ran.append(jid))
    assert worker.tick(repo, settings=None, coolify=None, lease_seconds=60) is True
    assert ran == [job.job_id]
    assert worker.tick(repo, settings=None, coolify=None, lease_seconds=60) is False

    coolify = FakeCoolifyClient()
    uuid = coolify.create_application(None)
    repo.set_coolify_uuid(app.app_id, uuid)
    repo.update_job(job.job_id, state="succeeded", expires_at=utcnow() - timedelta(days=1))
    assert worker.sweep_expired(repo, coolify) == 1
    assert repo.get_job(job.job_id).state == "expired" and uuid not in coolify.apps
