#!/bin/bash
# Installation sur la machine hôte (Raspberry Pi OS / Debian), avec un utilisateur normal ayant sudo.
# Idempotent : ne réécrit jamais un fichier de secrets déjà présent.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE_DIR="${WORKSPACE_DIR:-$HOME/workspace}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Prérequis manquant : $1"; exit 1; }; }
need docker; need git; need python3; need tailscale; need curl
[ -x /usr/sbin/iptables ] || { echo "Prérequis manquant : iptables"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Prérequis manquant : docker compose (plugin v2)"; exit 1; }
TS_IP="$(tailscale ip -4 2>/dev/null | head -1)"
[ -n "$TS_IP" ] || { echo "Tailscale n'est pas connecté (tailscale ip -4 est vide)"; exit 1; }

echo "== Dossiers"
mkdir -p "$WORKSPACE_DIR" "$BACKUP_DIR"

echo "== Agent"
cd "$REPO/agent"
[ -f .env ] || printf 'WORKSPACE_DIR=%s\nAGENT_UID=%s\nAGENT_GID=%s\nOPENCODE_PORT=4096\n' "$WORKSPACE_DIR" "$(id -u)" "$(id -g)" > .env
if [ ! -f agent.env ]; then (umask 077; cp agent.env.example agent.env); fi
chmod 600 agent.env
mkdir -p state && chmod 700 state

echo "== Pare-feu de l'agent (avant Docker au démarrage)"
sudo install -m 755 -o root -g root "$REPO/firewall/agent-firewall.sh" /usr/local/sbin/agent-firewall.sh
sudo install -m 644 -o root -g root "$REPO/firewall/agent-firewall.service" /etc/systemd/system/agent-firewall.service
sudo systemctl daemon-reload
sudo systemctl enable --now agent-firewall.service

echo "== Console DEV"
cd "$REPO/console"
if [ ! -f console.env ]; then
  (umask 077; sed -e "s|^DEVCONSOLE_BIND=.*|DEVCONSOLE_BIND=$TS_IP|" \
                  -e "s|^AGENT_DIR=.*|AGENT_DIR=$REPO/agent|" \
                  -e "s|^WORKSPACE_DIR=.*|WORKSPACE_DIR=$WORKSPACE_DIR|" \
                  -e "s|^BACKUP_DIR=.*|BACKUP_DIR=$BACKUP_DIR|" console.env.example > console.env)
fi
chmod 600 console.env
[ -f projects.json ] || echo "[]" > projects.json
sed -e "s|__USER__|$USER|g" -e "s|__DIR__|$REPO/console|g" dev-console.service \
  | sudo tee /etc/systemd/system/dev-console.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable dev-console.service

echo "== Image de l'agent (quelques minutes)"
cd "$REPO/agent" && docker compose build

cat <<EOF

Installation terminée. Il reste à faire, dans l'ordre :
  1. Remplir les secrets de l'agent :      nano $REPO/agent/agent.env
  2. Choisir le mot de passe de la Console : nano $REPO/console/console.env
  3. Démarrer :  cd $REPO/agent && docker compose up -d
                 sudo systemctl start dev-console
  4. Vérifier :  $REPO/scripts/check-security.sh
  5. Ajouter un projet : $REPO/scripts/add-project.sh <nom> <dossier_prod> <service_systemd>

Interface de l'agent : http://$TS_IP:4096   (identifiant : opencode)
Console DEV         : http://$TS_IP:8094
EOF
