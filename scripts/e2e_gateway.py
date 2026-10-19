"""End-to-end check through LiteLLM MCP Gateway.

usage: .venv/bin/python scripts/e2e_gateway.py <gateway_mcp_url> <virtual_key> [repo_url] [git_ref]

Example: scripts/e2e_gateway.py http://localhost:4000/sandbox_mcp/mcp sk-... \
         https://github.com/rtemirbulat/ai-sandbox-tests-gcloud-mcp main
"""

import json
import sys
import time
import uuid

import anyio
from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client


def _out(res):
    if res.structured_content is not None:
        return res.structured_content
    return json.loads(res.content[0].text)


async def main() -> int:
    url, key = sys.argv[1], sys.argv[2]
    repo = sys.argv[3] if len(sys.argv) > 3 else "https://github.com/github/choosealicense.com"
    ref = sys.argv[4] if len(sys.argv) > 4 else "gh-pages"
    headers = {"x-litellm-api-key": f"Bearer {key}", "Authorization": f"Bearer {key}"}
    http = create_mcp_http_client(headers=headers)
    async with streamable_http_client(url, http_client=http) as t, Client(t) as c:
        if True:
            tools = [t.name for t in (await c.list_tools()).tools]
            print("tools:", tools)
            name = lambda n: next(x for x in tools if x.endswith(n))  # noqa: E731
            a = _out(
                await c.call_tool(
                    name("assess_application"),
                    {
                        "repo_url": repo,
                        "git_ref": ref,
                        "app_name": "E2E demo",
                        "purpose": "сквозная проверка песочницы",
                        "data_classes": ["none"],
                    },
                )
            )
            print("assess:", json.dumps(a, ensure_ascii=False)[:400])
            if a.get("decision") != "allowed":
                return 1
            d = _out(
                await c.call_tool(
                    name("validate_and_deploy"),
                    {
                        "app_id": a["app_id"],
                        "commit_sha": a["commit_sha"],
                        "env": "TEST",
                        "idempotency_key": str(uuid.uuid4()),
                    },
                )
            )
            print("deploy:", d)
            deadline = time.time() + 600
            while time.time() < deadline:
                s = _out(await c.call_tool(name("status"), {"job_id": d["job_id"]}))
                print(f"status: {s['state']}/{s['stage']}")
                if s["state"] in ("succeeded", "blocked", "failed"):
                    print(json.dumps(s, ensure_ascii=False, indent=1))
                    return 0 if s["state"] == "succeeded" else 2
                await anyio.sleep(5)
    print("timeout")
    return 3


if __name__ == "__main__":
    sys.exit(anyio.run(main))
