# Image de l'agent de code. Prévue pour Raspberry Pi (arm64).
# Sur une machine x86_64 : remplacer "linux-arm64" par "linux-x64" ci-dessous.
FROM debian:trixie-slim

ARG OPENCODE_VERSION=1.19.5

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git python3 ripgrep curl less procps \
 && rm -rf /var/lib/apt/lists/*

# Binaire OpenCode officiel, version figée (pas de mise à jour automatique)
RUN mkdir /tmp/oc \
 && curl -fsSL -o /tmp/oc.tgz "https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-arm64.tar.gz" \
 && tar xzf /tmp/oc.tgz -C /tmp/oc \
 && install -m 755 "$(find /tmp/oc -type f -name opencode | head -1)" /usr/local/bin/opencode \
 && rm -rf /tmp/oc /tmp/oc.tgz \
 && opencode --version

# Utilisateur non-root, même UID que l'utilisateur de la machine pour les fichiers du workspace
ARG AGENT_UID=1000
RUN useradd -m -u ${AGENT_UID} -s /bin/bash agent \
 && mkdir -p /workspace /home/agent/.config/opencode /home/agent/.local/share/opencode \
 && chown -R agent:agent /home/agent /workspace

USER agent
WORKDIR /workspace
ENV GIT_AUTHOR_NAME=agent-dev GIT_AUTHOR_EMAIL=agent@pi-dev \
    GIT_COMMITTER_NAME=agent-dev GIT_COMMITTER_EMAIL=agent@pi-dev

# Refuse de démarrer sans mot de passe : l'interface ne doit jamais être ouverte sans authentification
CMD ["sh", "-c", "test -n \"$OPENCODE_SERVER_PASSWORD\" || { echo 'ERREUR: OPENCODE_SERVER_PASSWORD vide, demarrage refuse'; exit 1; }; exec opencode web --hostname 0.0.0.0 --port 4096"]
