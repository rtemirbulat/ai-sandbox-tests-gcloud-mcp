"""Resolve the calling user from the LiteLLM virtual key forwarded by the gateway.

LiteLLM forwards the caller's ``Authorization`` header (configured via ``extra_headers``); it
carries the LiteLLM virtual key. The key is a secret, so we treat it as the identity and verify
it against LiteLLM's ``/key/info`` endpoint (a key can read its own info). Results are cached.
"""

import logging
import time
from collections.abc import Mapping

import httpx

log = logging.getLogger(__name__)
KEY_HEADER = "authorization"
UNKNOWN = "unknown"


class IdentityResolver:
    def __init__(
        self,
        litellm_url: str,
        *,
        cache_seconds: int = 300,
        transport: httpx.BaseTransport | None = None,
    ):
        self._c = httpx.Client(base_url=litellm_url.rstrip("/"), timeout=10, transport=transport)
        self._ttl = cache_seconds
        self._cache: dict[str, tuple[float, str]] = {}

    def resolve(self, headers: Mapping[str, str] | None) -> str:
        key = (headers or {}).get(KEY_HEADER, "").strip()
        if key.lower().startswith("bearer "):
            key = key[7:].strip()
        if not key:
            log.info("no %s header; header names: %s", KEY_HEADER, sorted((headers or {}).keys()))
            return UNKNOWN
        now = time.time()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        actor = self._lookup(key)
        self._cache[key] = (now + self._ttl, actor)
        return actor

    def _lookup(self, key: str) -> str:
        try:
            r = self._c.get("/key/info", headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as e:
            log.warning("key/info failed: %s", type(e).__name__)
            return UNKNOWN
        if r.status_code != 200:
            log.warning("key/info returned %s", r.status_code)
            return UNKNOWN
        info = r.json().get("info") or {}
        return info.get("user_id") or info.get("key_alias") or UNKNOWN


class StaticIdentityResolver:
    """For tests and local runs without LiteLLM."""

    def __init__(self, actor: str = UNKNOWN):
        self.actor = actor

    def resolve(self, headers: Mapping[str, str] | None) -> str:
        return self.actor
