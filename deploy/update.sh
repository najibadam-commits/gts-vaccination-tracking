#!/usr/bin/env bash
# Pull the latest code for the deployed branch and rebuild. Downtime is the
# few seconds it takes to swap containers.
set -euo pipefail
DIR="${DIR:-/opt/gts}"
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
cd "$DIR"
$SUDO git pull --ff-only
$SUDO docker compose up -d --build
$SUDO docker image prune -f >/dev/null
$SUDO docker compose ps
