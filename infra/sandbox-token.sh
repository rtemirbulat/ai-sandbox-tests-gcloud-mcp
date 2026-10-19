#!/usr/bin/env bash
# Issue a sandbox identity token for a user on the VM (printed once; store it in a password manager).
# usage: infra/sandbox-token.sh <user>
set -euo pipefail
PROJECT="${PROJECT:-project-c6b97dc5-607e-455a-910}"
ACCOUNT="${ACCOUNT:-temirbulat_r@bi.group}"
ZONE="${ZONE:-europe-west3-a}"
NAME="${NAME:-sandbox-mcp}"
USER_ID="${1:?user id}"
gcloud --project "$PROJECT" --account "$ACCOUNT" --quiet compute ssh "$NAME" --zone "$ZONE" --tunnel-through-iap \
  --command "cd /opt/sandbox-mcp && sudo docker compose -f deploy/compose.yaml --env-file deploy/.env exec -T sandbox-mcp sandbox-mcp token '$USER_ID'" 2>/dev/null | tail -1
