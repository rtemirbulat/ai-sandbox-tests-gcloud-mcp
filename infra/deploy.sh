#!/usr/bin/env bash
# Ship the code to the VM and (re)start the compose stack. deploy/.env must exist locally
# (it is copied to the VM, never committed).
set -euo pipefail
PROJECT="${PROJECT:-project-c6b97dc5-607e-455a-910}"
ACCOUNT="${ACCOUNT:-temirbulat_r@bi.group}"
ZONE="${ZONE:-europe-west3-a}"
NAME="${NAME:-sandbox-mcp}"
G="gcloud --project $PROJECT --account $ACCOUNT --quiet"
cd "$(dirname "$0")/.."
[ -f deploy/.env ] || { echo "deploy/.env missing (see deploy/.env.example)"; exit 1; }

COPYFILE_DISABLE=1 tar czf /tmp/sandbox-mcp.tgz --no-xattrs --exclude .venv --exclude .git --exclude '__pycache__' \
  Dockerfile pyproject.toml src deploy
$G compute scp /tmp/sandbox-mcp.tgz "$NAME":/tmp/sandbox-mcp.tgz --zone "$ZONE" --tunnel-through-iap
$G compute ssh "$NAME" --zone "$ZONE" --tunnel-through-iap --command '
set -e
until sudo docker compose version >/dev/null 2>&1; do echo "waiting for docker compose install..."; sleep 5; done
sudo mkdir -p /opt/sandbox-mcp
sudo tar xzf /tmp/sandbox-mcp.tgz -C /opt/sandbox-mcp
cd /opt/sandbox-mcp
sudo docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build
sudo docker compose -f deploy/compose.yaml --env-file deploy/.env ps
'
