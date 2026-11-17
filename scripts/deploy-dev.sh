#!/bin/bash
# Promotion DEV -> PROD en ligne de commande (alternative à la Console DEV).
# Usage : deploy-dev.sh <projet>   (le projet doit être déclaré dans console/projects.json)
# Sécurité : l'hôte ne lance JAMAIS git dans le dépôt DEV (un dépôt contrôlé par l'agent pourrait
# exécuter du code via sa config git). Le DEV est exporté en bundle depuis le conteneur, puis importé
# dans le dépôt PROD, dont la config est de confiance.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
P="${1:-}"
WORKSPACE_DIR="${WORKSPACE_DIR:-$HOME/workspace}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
read -r PROD SVC < <(python3 - "$REPO/console/projects.json" "$P" <<'EOF'
import json, sys
for p in json.load(open(sys.argv[1])):
    if p["name"] == sys.argv[2]:
        print(p["prod"], p["service"]); break
EOF
) || true
[ -n "${PROD:-}" ] || { echo "Usage : $0 <projet déclaré dans console/projects.json>"; exit 1; }

docker exec coding-agent sh -c "mkdir -p /workspace/.bundles && git -C /workspace/$P bundle create -q /workspace/.bundles/$P.bundle main"
git -C "$PROD" fetch -q "$WORKSPACE_DIR/.bundles/$P.bundle" "+main:refs/heads/dev-incoming"

if [ -z "$(git -C "$PROD" log --oneline main..dev-incoming)" ]; then
  echo "Rien de nouveau à déployer pour $P."; exit 0
fi
echo "=== Commits à déployer ==="; git -C "$PROD" --no-pager log --oneline main..dev-incoming
echo; echo "=== Fichiers modifiés ==="; git -C "$PROD" --no-pager diff --stat main dev-incoming; echo
read -r -p "Voir le diff complet ? [o/N] " a
[ "$a" = "o" ] && git -C "$PROD" --no-pager diff main dev-incoming
read -r -p "Déployer en PRODUCTION ? [o/N] " a
[ "$a" = "o" ] || { echo "Annulé, rien n'a changé."; exit 0; }

mkdir -p "$BACKUP_DIR"
tar czf "$BACKUP_DIR/$P-$(date +%Y%m%d-%H%M%S).tar.gz" --exclude=venv --exclude=.venv --exclude=node_modules \
  --exclude=__pycache__ --exclude=.cache --exclude=logs -C "$(dirname "$PROD")" "$(basename "$PROD")"
ls -1t "$BACKUP_DIR/$P"-2*.tar.gz 2>/dev/null | tail -n +11 | xargs -r rm -f   # garde les 10 plus récentes
git -C "$PROD" merge --ff-only -q dev-incoming || { echo "ÉCHEC : la PROD a été modifiée à la main. Rien n'a été déployé."; exit 1; }
sudo systemctl restart "$SVC"; sleep 2
echo "Service $SVC : $(systemctl is-active "$SVC")"
echo "Retour arrière si besoin : git -C $PROD reset --hard ORIG_HEAD && sudo systemctl restart $SVC"
