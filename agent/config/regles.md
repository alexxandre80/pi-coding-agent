# Règles de travail (toutes les sessions, tous les projets)

## Avant de proposer quoi que ce soit
- Lis les fichiers concernés AVANT de proposer un plan. Ne devine jamais un nom de fichier, de classe, de fonction ou de sélecteur : cite ce que tu as réellement lu (fichier:ligne).
- S'il te manque une information, pose la question au lieu de supposer.

## Le plan
- Court : uniquement les étapes nécessaires à la demande. Chaque étape = un fichier précis modifié, et pourquoi.
- Interdit sans demande explicite : créer un README ou de la documentation, ajouter une dépendance, refactoriser du code non concerné, renommer des choses, ajouter des tests de principe.
- N'inclus aucune étape que tu ne peux pas faire toi-même (ouvrir un navigateur, tester sur un téléphone) : c'est l'utilisateur qui teste, via l'aperçu DEV.

## Pendant le travail
- Ne lance pas le serveur de l'application : l'aperçu DEV tourne déjà en permanence et se met à jour tout seul.
- Après une modification, vérifie avec `git diff` que seuls les fichiers prévus ont changé.

## Honnêteté
- N'annonce jamais une action comme faite sans l'avoir vérifiée (`git log`, `git diff`, lecture du fichier).
- Si une commande ou un appel échoue (limite, quota, erreur), rapporte le message d'erreur exact, sans le réinterpréter.

## Commits
- Un commit par changement logique, message en français, à l'impératif.
- Ne committe jamais le dossier data/ ni aucun secret.

## Production
- Le déploiement en production est fait par l'utilisateur via la Console DEV, jamais par toi.
