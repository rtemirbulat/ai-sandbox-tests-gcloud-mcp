"""Resolve the calling user from a sandbox user token forwarded by LiteLLM.

Why a separate token: LiteLLM authenticates the caller with its virtual key but deliberately
scrubs that key (and anything shaped like it) from headers forwarded upstream, so the MCP
server can never see who holds the key. The sandbox therefore issues its own per-user token
(``sandbox-mcp token <user>``), the client sends it as ``X-Sandbox-Token`` next to the LiteLLM
key, LiteLLM forwards it (``extra_headers``), and we map its hash to the user.
"""

import hashlib
import logging
import secrets
from collections.abc import Mapping

from sandbox_mcp.db.repo import Repository

log = logging.getLogger(__name__)
TOKEN_HEADER = "x-sandbox-token"
UNKNOWN = "unknown"


def new_token() -> str:
    # 64 hex chars: deliberately not "sk-" shaped so the gateway forwards it.
    return secrets.token_hex(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()


class DbIdentityResolver:
    def __init__(self, repo: Repository):
        self._repo = repo

    def resolve(self, headers: Mapping[str, str] | None) -> str:
        token = (headers or {}).get(TOKEN_HEADER, "").strip()
        if not token:
            return UNKNOWN
        user = self._repo.user_for_token_hash(token_hash(token))
        if user is None:
            log.warning("unknown sandbox token presented")
            return UNKNOWN
        return user


class StaticIdentityResolver:
    """For tests and local runs."""

    def __init__(self, actor: str = UNKNOWN):
        self.actor = actor

    def resolve(self, headers: Mapping[str, str] | None) -> str:
        return self.actor
