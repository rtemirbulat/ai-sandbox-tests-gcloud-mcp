#!/usr/bin/env bash
# Create the sandbox-mcp VM in GCP (idempotent). Prints the public IP and the sslip.io domain.
set -euo pipefail
PROJECT="${PROJECT:-project-c6b97dc5-607e-455a-910}"
ACCOUNT="${ACCOUNT:-temirbulat_r@bi.group}"
ZONE="${ZONE:-europe-west3-a}"
NAME="${NAME:-sandbox-mcp}"
MACHINE="${MACHINE:-e2-medium}"
G="gcloud --project $PROJECT --account $ACCOUNT --quiet"

$G services enable compute.googleapis.com iap.googleapis.com

$G compute firewall-rules describe allow-https-mcp >/dev/null 2>&1 || \
  $G compute firewall-rules create allow-https-mcp \
    --allow tcp:80,tcp:443 --target-tags mcp --description "Caddy for Sandbox MCP"
$G compute firewall-rules describe allow-iap-ssh >/dev/null 2>&1 || \
  $G compute firewall-rules create allow-iap-ssh \
    --allow tcp:22 --source-ranges 35.235.240.0/20 --target-tags mcp --description "SSH via IAP"

STARTUP='#!/bin/bash
set -e
if ! command -v docker >/dev/null; then
  apt-get update && apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" > /etc/apt/sources.list.d/docker.list
  apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi'

$G compute instances describe "$NAME" --zone "$ZONE" >/dev/null 2>&1 || \
  $G compute instances create "$NAME" --zone "$ZONE" --machine-type "$MACHINE" \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --boot-disk-size 30GB --boot-disk-type pd-balanced --tags mcp \
    --metadata startup-script="$STARTUP"

IP=$($G compute instances describe "$NAME" --zone "$ZONE" \
      --format='get(networkInterfaces[0].accessConfigs[0].natIP)')
echo "VM $NAME in $ZONE: IP=$IP"
echo "MCP_DOMAIN=mcp.${IP//./-}.sslip.io"
