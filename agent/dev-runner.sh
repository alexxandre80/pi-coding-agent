#!/bin/sh
# Aperçu DEV permanent : lance l'appli Python et la relance toute seule dès que son fichier principal change.
# APP_MAIN : fichier à lancer (défaut : server.py). Les fichiers statiques (CSS, JS) sont relus à chaque requête.
APP_MAIN="${APP_MAIN:-server.py}"
cd /app || exit 1
while true; do
  python3 "$APP_MAIN" &
  PID=$!
  M=$(stat -c %Y "$APP_MAIN")
  while kill -0 $PID 2>/dev/null && [ "$(stat -c %Y "$APP_MAIN")" = "$M" ]; do sleep 2; done
  kill $PID 2>/dev/null; wait $PID 2>/dev/null
  echo "$APP_MAIN modifié ou arrêté, redémarrage..."
  sleep 1
done
