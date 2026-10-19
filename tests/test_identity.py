import httpx
import pytest
from mcp import Client

from sandbox_mcp.mcp.server import build_mcp
from sandbox_mcp.services.identity import IdentityResolver, StaticIdentityResolver
from sandbox_mcp.settings import Settings

pytestmark = pytest.mark.anyio


def _resolver(handler):
    return IdentityResolver("http://litellm:4000", transport=httpx.MockTransport(handler))


def test_resolver_reads_user_from_key_info():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.headers.get("authorization"))
        return httpx.Response(200, json={"info": {"user_id": "alice", "key_alias": "alice-key"}})

    r = _resolver(handler)
    assert r.resolve({"x-litellm-api-key": "Bearer sk-1"}) == "alice"
    assert r.resolve({"x-litellm-api-key": "sk-1"}) == "alice"  # cached, no 2nd call
    assert seen == ["Bearer sk-1"]


def test_resolver_unknown_without_key_or_on_error():
    def handler(req: httpx.Request):
        return httpx.Response(401, json={"error": "bad key"})

    r = _resolver(handler)
    assert r.resolve({}) == "unknown"
    assert r.resolve(None) == "unknown"
    assert r.resolve({"x-litellm-api-key": "Bearer sk-bad"}) == "unknown"


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
