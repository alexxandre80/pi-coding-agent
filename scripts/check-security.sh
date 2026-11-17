#!/bin/bash
# Checklist de sécurité : on TENTE ce que l'agent ne doit pas pouvoir faire, et on vérifie que ça échoue.
# À lancer sur l'hôte après l'installation, puis après chaque changement de configuration.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
C=coding-agent
X="docker exec $C sh -c"
FAIL=0
ok() { echo "  [OK]    $1"; }
ko() { echo "  [ÉCHEC] $1"; FAIL=1; }

docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "Le conteneur $C ne tourne pas."; exit 1; }
TS_IP="$(tailscale ip -4 2>/dev/null | head -1)"
LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
ROUTER="$(ip -4 route show default | awk '{print $3; exit}')"

echo "=== A. Identité et privilèges ==="
[ "$($X 'id -u')" != "0" ] && ok "utilisateur non-root" || ko "l'agent tourne en root"
[ "$($X 'grep CapEff /proc/self/status | cut -f2')" = "0000000000000000" ] && ok "aucune capacité Linux" || ko "capacités présentes"
[ "$($X 'grep NoNewPrivs /proc/self/status | cut -f2')" = "1" ] && ok "no-new-privileges" || ko "no-new-privileges inactif"
$X 'command -v sudo' >/dev/null 2>&1 && ko "sudo présent" || ok "pas de sudo"
$X 'touch /etc/x 2>/dev/null' && ko "écriture système possible" || ok "écriture système impossible"

echo "=== B. Fichiers de l'hôte invisibles ==="
for p in /var/run/docker.sock /run/docker.sock "$HOME" "$REPO/agent/agent.env" "$REPO/console/console.env" /etc/ssh/sshd_config; do
  $X "test -e '$p'" && ko "$p visible" || ok "$p absent"
done
$X 'test -w /home/agent/.config/opencode/opencode.json' && ko "config modifiable par l'agent" || ok "config en lecture seule"
$X 'test -w /home/agent/.config/opencode/regles.md' && ko "règles modifiables par l'agent" || ok "règles en lecture seule"

echo "=== C. Réseau : tout bloqué sauf Internet ==="
# Test TCP : "bloqué" = aucune réponse (pare-feu DROP). Un refus de connexion prouve que la cible est JOIGNABLE.
try() {
  if docker exec "$C" python3 -c "
import socket, sys
try:
    socket.create_connection(('$2', $3), 3); sys.exit(0)
except ConnectionRefusedError:
    sys.exit(0)
except OSError:
    sys.exit(1)" 2>/dev/null; then ko "$1 JOIGNABLE"; else ok "$1 bloqué"; fi
}
[ -n "$LAN_IP" ] && try "hôte via le réseau local ($LAN_IP:22)" "$LAN_IP" 22
[ -n "$ROUTER" ] && try "box / routeur ($ROUTER:80)" "$ROUTER" 80
[ -n "$TS_IP" ] && try "hôte via Tailscale ($TS_IP:8094, Console)" "$TS_IP" 8094
try "hôte via la passerelle Docker (172.30.0.1:22)" 172.30.0.1 22
try "hôte via docker0 (172.17.0.1:22)" 172.17.0.1 22
try "réseau Tailscale (100.100.100.100:53)" 100.100.100.100 53
$X 'curl -s -o /dev/null --max-time 6 -w "%{http_code}" https://openrouter.ai/api/v1/models' | grep -qE '^[1-5][0-9][0-9]$' \
  && ok "Internet joignable (attendu)" || ko "Internet injoignable"

echo "=== D. Accès entrants ==="
# Le pare-feu ne filtre que l'IPv4 : aucun port de l'agent ou d'un aperçu ne doit écouter en IPv6.
V6="$(ss -ltnH 2>/dev/null | awk '$4 ~ /^\[::\]:/ {printf "%s ", $4}')"
for port in $(docker ps --filter network=agent_net --format '{{.Ports}}' | grep -oE ':[0-9]+->' | grep -oE '[0-9]+' | sort -u); do
  echo "$V6" | grep -q "\]:$port " && ko "port $port publié en IPv6 (contourne le pare-feu) : préfixer par 0.0.0.0 dans le compose" \
    || ok "port $port non publié en IPv6"
done
code=$(curl -s -o /dev/null --max-time 4 -w '%{http_code}' http://127.0.0.1:4096/)
[ "$code" = "401" ] && ok "interface agent sans mot de passe -> 401" || ko "interface agent sans mot de passe -> $code"
if [ -n "$LAN_IP" ]; then
  echo "  [INFO]  À tester depuis un AUTRE appareil du réseau local (doit échouer) : http://$LAN_IP:4096 et :8094"
fi

echo
[ $FAIL = 0 ] && echo "Tous les contrôles sont passés." || { echo "Au moins un contrôle a échoué : ne pas utiliser l'agent avant correction."; exit 1; }
