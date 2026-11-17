# pi-coding-agent

Un agent IA de code auto-hébergé sur un Raspberry Pi, piloté depuis un smartphone, avec des modèles d'IA **gratuits**.
Le Pi ne fait tourner que l'agent (lire et écrire des fichiers, lancer des commandes) : le calcul est fait par les API des fournisseurs.

Le cœur du projet n'est pas l'agent lui-même ([OpenCode](https://opencode.ai)), mais **l'environnement autour** :
une séparation DEV / PROD, une isolation stricte de l'agent et une console de déploiement pensée pour le téléphone.

## Ce que ça donne au quotidien

1. Sur le téléphone, ouvrir l'agent et demander une modification (« agrandis les boutons sur mobile »).
2. L'agent travaille sur une **copie DEV** du projet, avec des données fictives, et committe.
3. Vérifier le résultat sur l'**aperçu DEV**, une copie de l'appli qui tourne en permanence.
4. Dans la **Console DEV**, relire les fichiers modifiés et appuyer sur **Déployer**. Sauvegarde automatique, retour arrière en un clic.

## Architecture

```
Téléphone (Tailscale uniquement)
 ├─ OpenCode (agent IA) ─┐   conteneur : non-root, aucune capacité Linux,
 ├─ Aperçu DEV           │   réseau isolé avec accès Internet uniquement
 └─ Console DEV (hôte)   │
                         ▼
        ~/workspace/<projet>        DEV  : git, données FICTIVES, AGENTS.md
                         │   export en "git bundle" depuis le conteneur
                         ▼
        ~/<projet>                  PROD : git, vraies données, service systemd
```

| Dossier | Contenu |
|---|---|
| `agent/` | Image Docker (Debian + OpenCode en version figée), docker-compose, config et règles de l'agent |
| `firewall/` | Règles iptables qui isolent le réseau de l'agent, chargées **avant** Docker au démarrage |
| `console/` | Console DEV : petite appli Python sans dépendance (relire, déployer, annuler, quotas) |
| `scripts/` | Installation, ajout d'un projet, déploiement en ligne de commande, checklist de sécurité |

## Modèle de sécurité

L'agent exécute des commandes sur ta machine, sur instruction d'un modèle d'IA externe.
Le principe : **ce que l'agent ne voit pas, il ne peut pas le divulguer ni le casser.**

**Ce qui est protégé, et vérifié par `scripts/check-security.sh` :**
- Seul le workspace est monté. Le reste de la machine, les secrets des autres services, le socket Docker et la PROD sont invisibles.
- L'utilisateur est non-root, sans aucune capacité Linux, avec `no-new-privileges`. La config et les règles de l'agent sont en lecture seule.
- Réseau : l'agent atteint Internet, mais ni l'hôte, ni le réseau local, ni les appareils Tailscale, ni les autres conteneurs.
- Les interfaces ne sont accessibles que via Tailscale, avec un mot de passe obligatoire : le conteneur et la Console refusent de démarrer sans.
- Déploiement : l'hôte ne lance **jamais** git dans un dépôt contrôlé par l'agent, car une config git piégée (`core.fsmonitor`, hooks) pourrait exécuter du code sur l'hôte. Le DEV transite en *bundle*, qui n'est qu'un fichier de données.
- La Console déploie exactement la version relue. Si le service ne répond plus, elle revient automatiquement à la version précédente.

**Ce qui reste à ta charge (risques résiduels) :**
- **Les clés API de l'agent sont lisibles par l'agent.** Utilise des comptes gratuits, sans carte bancaire, et des clés révocables.
- **Tout ce qui est dans le workspace part chez les fournisseurs d'IA.** D'où les données fictives. Les offres gratuites peuvent conserver les échanges ou s'en servir pour l'entraînement : réserve ce projet à du code perso.
- **Injection de prompt** : une page ou un fichier piégé peut manipuler l'agent. Les commandes sensibles demandent ton accord, et le réseau limite la casse.
- **Le code écrit par l'agent** finit en PROD si tu le déploies. Relis au minimum la liste des fichiers modifiés.
- **La Console a des droits élevés sur l'hôte** : elle doit exporter le DEV depuis le conteneur (groupe `docker`) et redémarrer les services (`sudo systemctl restart`). C'est pourquoi elle n'écoute que sur l'IP Tailscale, exige un mot de passe, refuse les requêtes venant d'autres sites (jeton anti-CSRF), et reste injoignable depuis l'agent. Protège son mot de passe comme un accès administrateur.

## Prérequis

- Raspberry Pi 4 ou 5 (arm64) sous Raspberry Pi OS / Debian. Sur x86_64, voir le commentaire du `Dockerfile`.
- Docker avec le plugin compose v2, git, python3, iptables.
- [Tailscale](https://tailscale.com) sur la machine et sur le téléphone.
- Un utilisateur avec `sudo`. Pour la Console, `sudo` doit pouvoir redémarrer les services sans mot de passe, par exemple avec une règle limitée :
  `echo "$USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart *" | sudo tee /etc/sudoers.d/dev-console`
- Des clés API gratuites (voir plus bas).

## Installation

```bash
git clone https://github.com/<toi>/pi-coding-agent.git ~/pi-coding-agent
cd ~/pi-coding-agent
./scripts/install.sh
```

Puis, comme indiqué à la fin de l'installation :
1. remplir `agent/agent.env` (clés API et mot de passe de l'interface de l'agent) ;
2. remplir `console/console.env` (mot de passe de la Console) ;
3. lancer `cd agent && docker compose up -d`, puis `sudo systemctl start dev-console` ;
4. lancer `./scripts/check-security.sh`. **N'utilise pas l'agent tant qu'un contrôle échoue.**

## Ajouter un projet

```bash
./scripts/add-project.sh mon-projet ~/mon-projet mon-projet.service
```

Le script sauvegarde le projet, met git sur la PROD (après ta validation du `.gitignore`) et crée la copie DEV. Il te reste à :
- mettre des **données fictives** dans la copie DEV ;
- écrire un `AGENTS.md` qui décrit le projet à l'agent ;
- déclarer le projet dans `console/projects.json` (modèle : `projects.example.json`) ;
- si tu veux, ajouter un aperçu DEV (bloc commenté dans `agent/docker-compose.yml`).

Dans OpenCode, ouvre le projet par son chemin dans le conteneur : `/workspace/mon-projet`.

## Utiliser l'agent efficacement

- Commence en mode **Plan**, puis tape **`/critique`** : l'agent supprime les étapes inutiles et cite les fichiers réellement lus.
- Passe en mode **Build** pour qu'il exécute, puis tape **`/verifie`** : il prouve ce qu'il a fait avec git.
- **Ne crois jamais l'agent sur parole.** Il peut annoncer un commit qui n'existe pas (c'est arrivé pendant les tests). Ce qui fait foi : l'aperçu DEV et la Console.
- Les règles communes à tous les projets sont dans `agent/config/regles.md`. Celles d'un projet vont dans son `AGENTS.md`.

## Les modèles gratuits : la réalité (novembre 2026)

Un agent de code est le pire cas pour les offres gratuites. Chaque requête embarque 7 000 à 30 000 tokens de contexte, et une tâche en demande 10 à 30.

| Fournisseur | Constat |
|---|---|
| OpenRouter (modèles `:free`) | **Moteur principal.** Environ 50 requêtes par jour sans crédit, soit 3 à 5 petites tâches |
| Groq | Très rapide, mais limité à **8 000 tokens par minute** pour tous ses modèles, et OpenCode en envoie environ 7 000 dès le premier message. Utilisable seulement pour les petites tâches internes (titres de session) |
| Gemini, Mistral, Cerebras | Pas d'offre gratuite utilisable pour l'API dans nos essais (indisponible, ou carte bancaire exigée) |

Les identifiants de modèles gratuits changent souvent : ajuste les listes `whitelist` dans `agent/config/opencode.json`.
La Console affiche la consommation réelle par modèle, lue dans l'historique de l'agent.

## Points techniques à connaître

- OpenCode 1.19.x attend l'**ancien format de permissions** (`"permission": {...}`). Le format `permissions` / `policies` décrit dans la doc en ligne est refusé par cette version.
- Si l'agent affiche « inotify ... No space left on device », ce n'est pas le disque : la limite système `fs.inotify.max_user_watches` est saturée, souvent par un autre service qui surveille beaucoup de fichiers. Il faut la relever, par exemple à 262144 dans `/etc/sysctl.d/`.
- Le pare-feu cible le sous-réseau `172.30.0.0/24` et le pont `br-agent` : change les deux ensemble si ce sous-réseau est déjà pris.

## Transparence

Ce projet a été conçu et écrit avec l'assistance d'une IA (Claude, d'Anthropic), puis testé, relu et validé sur un Raspberry Pi 4 réel.
Les choix de sécurité ont été vérifiés par des tests exécutés, pas seulement par relecture.

## Licence

MIT, voir [LICENSE](LICENSE).
