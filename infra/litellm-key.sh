#!/usr/bin/env bash
# Create a LiteLLM virtual key that may call only the sandbox_mcp MCP server.
# usage: infra/litellm-key.sh <litellm_base_url> <master_key> <user_alias>
set -euo pipefail
BASE="${1:?base url}"; MASTER="${2:?master key}"; ALIAS="${3:?user alias}"
curl -sS -X POST "$BASE/key/generate" \
  -H "Authorization: Bearer $MASTER" -H "Content-Type: application/json" \
  -d "{\"key_alias\": \"$ALIAS\", \"user_id\": \"$ALIAS\", \"mcp_servers\": [\"sandbox_mcp\"], \"metadata\": {\"user\": \"$ALIAS\"}}"
echo
