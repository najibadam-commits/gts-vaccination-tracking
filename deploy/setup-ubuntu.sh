#!/usr/bin/env bash
# One-time setup of the GTS pipeline on a fresh Ubuntu 22.04 / 24.04 server
# (Oracle Cloud Always Free, an eHA server, any VPS). x86-64 or ARM.
#
#   curl -fsSL https://raw.githubusercontent.com/najibadam-commits/gts-vaccination-tracking/deploy/docker/deploy/setup-ubuntu.sh -o setup.sh
#   bash setup.sh
#
# It installs Docker, opens ports 80/443 in the OS firewall, clones the repo to
# /opt/gts, asks for a domain (optional) and an app password, and starts the
# app behind Caddy. Safe to re-run.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/najibadam-commits/gts-vaccination-tracking.git}"
BRANCH="${BRANCH:-deploy/docker}"
DIR="${DIR:-/opt/gts}"

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

say "Installing Docker (if missing)"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | $SUDO sh
fi
$SUDO systemctl enable --now docker
if [ -n "$SUDO" ] && ! id -nG "$USER" | grep -qw docker; then
  $SUDO usermod -aG docker "$USER"
fi
DOCKER="$SUDO docker"
$SUDO apt-get install -y -qq git >/dev/null

say "Opening ports 80 and 443 in the OS firewall"
# Oracle's Ubuntu images ship iptables rules that REJECT everything but SSH.
if command -v ufw >/dev/null 2>&1 && $SUDO ufw status | grep -q "Status: active"; then
  $SUDO ufw allow 80/tcp; $SUDO ufw allow 443/tcp; $SUDO ufw allow 443/udp
fi
for p in 80 443; do
  $SUDO iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null \
    || $SUDO iptables -I INPUT 5 -p tcp --dport "$p" -m state --state NEW -j ACCEPT
done
if command -v netfilter-persistent >/dev/null 2>&1; then
  $SUDO netfilter-persistent save >/dev/null
fi
echo "Remember: on Oracle Cloud also add ingress rules for TCP 80 and 443 in the"
echo "VCN Security List (console). See deploy/README.md, step 2."

say "Fetching the code into $DIR"
if [ -d "$DIR/.git" ]; then
  $SUDO git -C "$DIR" fetch --quiet origin
  $SUDO git -C "$DIR" checkout --quiet "$BRANCH"
  $SUDO git -C "$DIR" pull --quiet --ff-only origin "$BRANCH"
else
  $SUDO git clone --quiet --branch "$BRANCH" "$REPO_URL" "$DIR"
fi
$SUDO mkdir -p "$DIR/deploy/private"
$SUDO chmod 755 "$DIR/deploy/private"

if [ ! -f "$DIR/.env" ]; then
  say "Configuring"
  read -r -p "Domain for the app (blank = use the server IP over plain HTTP): " DOMAIN </dev/tty
  read -r -p "App username [gts]: " AUSER </dev/tty
  AUSER="${AUSER:-gts}"
  while :; do
    read -r -s -p "App password (min 10 characters): " PW </dev/tty; echo
    [ "${#PW}" -ge 10 ] && break
    echo "Too short."
  done
  HASH="$($DOCKER run --rm caddy:2 caddy hash-password --plaintext "$PW")"
  unset PW
  TOTAL_MB="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)"
  SMALL=""; [ "$TOTAL_MB" -lt 3800 ] && SMALL=1
  {
    echo "SITE_ADDRESS=${DOMAIN:-:80}"
    echo "AUTH_USER=$AUSER"
    echo "AUTH_HASH='$HASH'"
    echo "UPLOAD_MB=2000"
    echo "GTS_HOSTED=$SMALL"
  } | $SUDO tee "$DIR/.env" >/dev/null
  $SUDO chmod 600 "$DIR/.env"
fi

say "Building and starting (first build takes a few minutes)"
cd "$DIR"
$DOCKER compose up -d --build

say "Waiting for the app to report healthy"
for _ in $(seq 1 60); do
  state="$($DOCKER inspect -f '{{.State.Health.Status}}' "$($DOCKER compose ps -q app)" 2>/dev/null || true)"
  [ "$state" = "healthy" ] && break
  sleep 5
done
echo "app container: ${state:-unknown}"

ADDR="$($SUDO grep '^SITE_ADDRESS=' .env | cut -d= -f2)"
if [ "$ADDR" = ":80" ]; then
  IP="$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')"
  URL="http://$IP"
else
  URL="https://$ADDR"
fi
say "Done. Open $URL and sign in with the username and password you chose."
echo "Private templates (e.g. the PIR deck) go in $DIR/deploy/private/"
echo "Update later with:  bash $DIR/deploy/update.sh"
