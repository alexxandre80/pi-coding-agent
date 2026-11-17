#!/bin/bash
# Prépare un projet existant pour l'agent : git sur la PROD, copie DEV dans le workspace.
# Usage : add-project.sh <nom> <dossier_prod> <service_systemd>
# Exemple : add-project.sh mon-projet /home/pi/mon-projet mon-projet.service
set -euo pipefail

NAME="${1:-}"; PROD="${2:-}"; SVC="${3:-}"
WORKSPACE_DIR="${WORKSPACE_DIR:-$HOME/workspace}"
[[ "$NAME" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "Usage : $0 <nom en minuscules-tirets> <dossier_prod> <service_systemd>"; exit 1; }
[ -d "$PROD" ] || { echo "Dossier PROD introuvable : $PROD"; exit 1; }
[ -n "$SVC" ] || { echo "Service systemd manquant"; exit 1; }

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"; mkdir -p "$BACKUP_DIR"
tar czf "$BACKUP_DIR/$NAME-$(date +%Y%m%d-%H%M%S)-avant-git.tar.gz" -C "$(dirname "$PROD")" "$(basename "$PROD")"
echo "Sauvegarde faite dans $BACKUP_DIR"

if [ ! -d "$PROD/.git" ]; then
  if [ ! -f "$PROD/.gitignore" ]; then
    printf '%s\n' 'data/' '*.db' '*.sqlite*' '.env*' '!.env.example' 'config.json' \
      'venv/' '.venv/' 'node_modules/' '__pycache__/' '*.pyc' 'logs/' '*.log' > "$PROD/.gitignore"
    echo "Créé $PROD/.gitignore : VÉRIFIE qu'il exclut bien tes données et tes secrets avant de continuer."
    read -r -p "Continuer ? [o/N] " a; [ "$a" = "o" ] || exit 1
  fi
  git -C "$PROD" init -q -b main
  git -C "$PROD" add -A
  git -C "$PROD" commit -q -m "État initial de la production"
fi
echo "Fichiers suivis par git (ce que l'agent verra) :"
git -C "$PROD" ls-files | sed 's/^/  /'

# Création du DEV : la seule fois où l'hôte lance git dans le workspace, AVANT que l'agent n'y ait accès.
if [ ! -d "$WORKSPACE_DIR/$NAME" ]; then
  git clone -q "$PROD" "$WORKSPACE_DIR/$NAME"
  git -C "$WORKSPACE_DIR/$NAME" remote remove origin
fi

cat <<EOF

Projet $NAME prêt. À faire maintenant :
  1. Mettre des DONNÉES FICTIVES dans $WORKSPACE_DIR/$NAME (jamais les vraies : l'agent les enverrait à l'IA).
  2. Écrire $WORKSPACE_DIR/$NAME/AGENTS.md (à quoi sert le projet, comment il est organisé, règles propres).
  3. Ajouter le projet dans console/projects.json :
     {"name": "$NAME", "prod": "$PROD", "service": "$SVC",
      "health_url": "http://127.0.0.1:PORT/", "dev_url": "http://IP_TAILSCALE:PORT_DEV/", "prod_url": "http://IP_TAILSCALE:PORT/"}
     puis : sudo systemctl restart dev-console
  4. Optionnel : un aperçu DEV (bloc à copier dans agent/docker-compose.yml).
EOF
