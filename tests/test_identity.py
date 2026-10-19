import pytest
from mcp import Client

from sandbox_mcp.mcp.server import build_mcp
from sandbox_mcp.services.identity import DbIdentityResolver, StaticIdentityResolver, new_token
from sandbox_mcp.settings import Settings

pytestmark = pytest.mark.anyio


def test_db_resolver_maps_token_to_user(repo):
    token = repo.create_user_token("alice")
    assert len(token) == 64 and not token.startswith("sk-")
    r = DbIdentityResolver(repo)
    assert r.resolve({"x-sandbox-token": token}) == "alice"
    assert r.resolve({"x-sandbox-token": f" {token} "}) == "alice"
    assert r.resolve({"x-sandbox-token": new_token()}) == "unknown"
    assert r.resolve({}) == "unknown"
    assert r.resolve(None) == "unknown"


def test_user_may_have_several_tokens(repo):
    t1 = repo.create_user_token("bob")
    t2 = repo.create_user_token("bob")
    r = DbIdentityResolver(repo)
    assert t1 != t2 and r.resolve({"x-sandbox-token": t1}) == r.resolve({"x-sandbox-token": t2})


async def test_status_hidden_from_other_owner(repo, tmp_path, monkeypatch, local_repo):
    path, sha = local_repo
    monkeypatch.setattr("sandbox_mcp.mcp.server.validate_github_url", lambda u: u)
    s = Settings(_env_file=None, gateway_token="t", workdir=str(tmp_path / "w"))
    alice = build_mcp(repo, s, resolver=StaticIdentityResolver("alice"))
    bob = build_mcp(repo, s, resolver=StaticIdentityResolver("bob"))
    async with Client(alice, raise_exceptions=True) as c:
        a = (
            await c.call_tool(
                "assess_application",
                {
                    "repo_url": path,
                    "git_ref": "main",
                    "app_name": "A",
                    "purpose": "p",
                    "data_classes": ["none"],
                },
            )
        ).structured_content
        d = (
            await c.call_tool(
                "validate_and_deploy",
                {"app_id": a["app_id"], "commit_sha": sha, "env": "TEST", "idempotency_key": "k"},
            )
        ).structured_content
    assert repo.get_application(a["app_id"]).owner == "alice"
    async with Client(bob, raise_exceptions=True) as c:
        s1 = (await c.call_tool("status", {"job_id": d["job_id"]})).structured_content
        assert s1["error"] == "not_found"
        s2 = (
            await c.call_tool(
                "validate_and_deploy",
                {"app_id": a["app_id"], "commit_sha": sha, "env": "TEST", "idempotency_key": "k2"},
            )
        ).structured_content
        assert s2["error"] == "not_found"
    async with Client(alice, raise_exceptions=True) as c:
        s3 = (await c.call_tool("status", {"job_id": d["job_id"]})).structured_content
        assert s3["state"] == "queued"
