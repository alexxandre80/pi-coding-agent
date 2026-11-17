#!/bin/sh
# Isolation réseau du conteneur coding-agent (sous-réseau 172.30.0.0/24, pont br-agent).
# Idempotent : chaque règle n'est ajoutée que si elle est absente.
# Retour arrière : /usr/local/sbin/agent-firewall.sh --remove
IPT=/usr/sbin/iptables
SUB=172.30.0.0/24
PRIV="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16"

$IPT -N DOCKER-USER 2>/dev/null || true

if [ "$1" = "--remove" ]; then
  for n in $PRIV; do $IPT -D DOCKER-USER -s $SUB -d $n -j DROP 2>/dev/null; done
  $IPT -D DOCKER-USER -s $SUB -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN 2>/dev/null
  $IPT -D DOCKER-USER -d $SUB ! -i tailscale0 -m conntrack --ctstate NEW -j DROP 2>/dev/null
  $IPT -D INPUT -i br-agent -j DROP 2>/dev/null
  $IPT -D INPUT -i br-agent -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null
  echo "regles agent supprimees"; exit 0
fi

ins() { chain=$1; shift; $IPT -C "$chain" "$@" 2>/dev/null || $IPT -I "$chain" 1 "$@"; }

# Insérées en tête, dans l'ordre inverse (la dernière insérée est la première évaluée).
# 1) Connexions entrantes vers l'agent : uniquement depuis Tailscale (pas le LAN)
ins DOCKER-USER -d $SUB ! -i tailscale0 -m conntrack --ctstate NEW -j DROP
# 2) L'agent ne peut joindre aucun réseau privé (LAN, autres conteneurs, appareils Tailscale)
for n in $PRIV; do ins DOCKER-USER -s $SUB -d $n -j DROP; done
# 3) Les réponses aux connexions déjà établies passent
ins DOCKER-USER -s $SUB -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN

# 4) L'agent ne peut joindre aucun service de la Pi elle-même
ins INPUT -i br-agent -j DROP
ins INPUT -i br-agent -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT

echo "regles agent actives"
