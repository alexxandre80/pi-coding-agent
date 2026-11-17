#!/usr/bin/env python3
"""Console DEV : relire et deployer les modifications de l'agent (DEV -> PROD), suivre les quotas.

Python standard library uniquement. Tourne sur l'hote (utilisateur normal, membre du groupe docker, sudo sans mot de passe pour systemctl),
ecoute UNIQUEMENT sur l'IP Tailscale. Configuration : console.env (voir console.env.example).
Securite :
- Authentification HTTP Basic (mot de passe dans console.env, chmod 600) + jeton anti-CSRF + controle d'origine.
- Aucune commande git n'est lancee par l'hote dans le depot DEV : le DEV est exporte en bundle depuis le conteneur.
- On deploie exactement la version relue (empreinte du commit verifiee au moment du clic).
- Si le service ne repart pas après déploiement, retour automatique à la version précédente.
"""
import base64
import hmac
import html
import json
import os
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parent
HISTORY = BASE / "history.json"
LOCK = threading.Lock()
CSRF = secrets.token_urlsafe(32)


def load_env(path):
    env = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


CONF = load_env(BASE / "console.env")
PASSWORD = CONF.get("DEVCONSOLE_PASSWORD", "")
BIND = CONF.get("DEVCONSOLE_BIND", "")                      # IP Tailscale de la Pi (tailscale ip -4)
PORT = int(CONF.get("DEVCONSOLE_PORT", "8094"))
AGENT_DIR = Path(CONF.get("AGENT_DIR", str(BASE.parent / "agent")))
WORKSPACE_DIR = Path(CONF.get("WORKSPACE_DIR", str(Path.home() / "workspace")))
BACKUPS = Path(CONF.get("BACKUP_DIR", str(Path.home() / "backups")))
AGENT_CONTAINER = CONF.get("AGENT_CONTAINER", "coding-agent")
OPENROUTER_FREE_PER_DAY = int(CONF.get("OPENROUTER_FREE_PER_DAY", "50"))
STATE_DB = AGENT_DIR / "state" / "opencode.db"
PROJECTS = {p["name"]: p for p in json.loads((BASE / "projects.json").read_text(encoding="utf-8"))}


# ---------------------------------------------------------------- utilitaires
def run(cmd, timeout=90):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "delai depasse"


def git(prod, *args, timeout=60):
    return run(["git", "-C", prod, *args], timeout=timeout)


def esc(s):
    return html.escape(str(s), quote=True)


def now_str():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def read_history():
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def add_history(entry):
    hist = read_history()
    entry["id"] = len(hist) + 1
    entry["date"] = now_str()
    hist.append(entry)
    tmp = HISTORY.with_suffix(".tmp")
    tmp.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(HISTORY)
    return entry["id"]


BACKUP_KEEP = 10
BACKUP_EXCLUDES = ("venv", ".venv", "node_modules", "__pycache__", ".cache", "logs")


def prune_backups(name):
    """Garde les BACKUP_KEEP sauvegardes les plus recentes du projet (fichiers <projet>-2*.tar.gz)."""
    files = sorted(BACKUPS.glob(f"{name}-2*.tar.gz"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[BACKUP_KEEP:]:
        f.unlink(missing_ok=True)


def service_state(p):
    return run(["systemctl", "is-active", p["service"]], timeout=10)[1] or "inconnu"


def http_ok(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return 200 <= r.status < 400
    except Exception:
        return False


# ---------------------------------------------------------------- DEV -> PROD
def refresh_incoming(p):
    """Exporte le DEV en bundle depuis le conteneur puis l'importe dans la PROD (ref dev-incoming)."""
    name = p["name"]
    code, out = run(["docker", "exec", AGENT_CONTAINER, "sh", "-c",
                     f"mkdir -p /workspace/.bundles && git -C /workspace/{name} bundle create -q /workspace/.bundles/{name}.bundle main"])
    if code:
        return f"Export du DEV impossible (agent arrêté ?) : {out[-200:]}"
    code, out = git(p["prod"], "fetch", "-q", str(WORKSPACE_DIR / ".bundles" / f"{name}.bundle"), "+main:refs/heads/dev-incoming")
    if code:
        return f"Import du DEV impossible : {out[-200:]}"
    return None


def prod_dirty(p):
    code, out = git(p["prod"], "status", "--porcelain")
    return out if (code == 0 and out) else ""


def pending(p):
    code, out = git(p["prod"], "log", "--format=%h%x09%ad%x09%s", "--date=format:%d/%m %H:%M", "main..dev-incoming")
    commits = [l.split("\t", 2) for l in out.splitlines()] if code == 0 and out else []
    return commits


def deploy(p, expected):
    with LOCK:
        prod, svc = p["prod"], p["service"]
        dirty = prod_dirty(p)
        if dirty:
            return add_history({"project": p["name"], "action": "déploiement", "ok": False,
                                "msg": "Refusé : la PROD a ete modifiee à la main. Rien n'a changé."})
        code, head_incoming = git(prod, "rev-parse", "dev-incoming")
        if code or head_incoming != expected:
            return add_history({"project": p["name"], "action": "déploiement", "ok": False,
                                "msg": "Refusé : le DEV a changé depuis ta relecture. Relis la nouvelle version."})
        old = git(prod, "rev-parse", "HEAD")[1]
        BACKUPS.mkdir(exist_ok=True)
        archive = BACKUPS / f"{p['name']}-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"
        prod_path = Path(prod)
        run(["tar", "czf", str(archive), *[f"--exclude={x}" for x in BACKUP_EXCLUDES],
             "-C", str(prod_path.parent), prod_path.name], timeout=300)
        prune_backups(p["name"])
        code, out = git(prod, "merge", "--ff-only", "-q", expected)
        if code:
            return add_history({"project": p["name"], "action": "déploiement", "ok": False,
                                "msg": f"Refusé : fusion impossible ({out[-150:]}). Rien n'a changé."})
        run(["sudo", "-n", "systemctl", "restart", svc], timeout=60)
        time.sleep(3)
        healthy = service_state(p) == "active" and (not p.get("health_url") or http_ok(p["health_url"]))
        if healthy:
            return add_history({"project": p["name"], "action": "déploiement", "ok": True, "from": old, "to": expected,
                                "msg": f"Déployé. Service actif. Sauvegarde : {archive.name}"})
        git(prod, "reset", "--hard", "-q", old)
        run(["sudo", "-n", "systemctl", "restart", svc], timeout=60)
        time.sleep(3)
        return add_history({"project": p["name"], "action": "déploiement", "ok": False,
                            "msg": f"Le service ne répondait plus après déploiement : retour automatique à la version précédente "
                                   f"(service {service_state(p)}). Sauvegarde : {archive.name}"})


def rollback(p):
    with LOCK:
        prod = p["prod"]
        head = git(prod, "rev-parse", "HEAD")[1]
        last = next((h for h in reversed(read_history())
                     if h.get("project") == p["name"] and h.get("action") == "déploiement" and h.get("ok")), None)
        if not last or last.get("to") != head:
            return add_history({"project": p["name"], "action": "annulation", "ok": False,
                                "msg": "Rien à annuler : la PROD n'est pas sur le dernier déploiement de la console."})
        if prod_dirty(p):
            return add_history({"project": p["name"], "action": "annulation", "ok": False,
                                "msg": "Refusé : la PROD a ete modifiee à la main."})
        git(prod, "reset", "--hard", "-q", last["from"])
        run(["sudo", "-n", "systemctl", "restart", p["service"]], timeout=60)
        time.sleep(3)
        st = service_state(p)
        return add_history({"project": p["name"], "action": "annulation", "ok": st == "active", "from": head, "to": last["from"],
                            "msg": f"Version précédente restaurée. Service : {st}."})


# ---------------------------------------------------------------- quotas
def usage_stats():
    """Requetes et tokens par fournisseur/modele, aujourd'hui (UTC, reference OpenRouter) et sur 7 jours."""
    out = {"today": {}, "week": {}, "error": None}
    try:
        c = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=5)
        start_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
        week = (time.time() - 7 * 86400) * 1000
        for t, d in c.execute("select time_created, data from message where time_created >= ?", (week,)):
            try:
                j = json.loads(d)
            except json.JSONDecodeError:
                continue
            if j.get("role") != "assistant":
                continue
            key = f"{j.get('providerID', '?')}/{j.get('modelID', '?')}"
            tok = (j.get("tokens") or {}).get("total") or 0
            for bucket, ok in (("week", True), ("today", t >= start_utc)):
                if ok:
                    b = out[bucket].setdefault(key, [0, 0, 0])
                    b[0] += 1
                    b[1] += tok
                    b[2] += 1 if j.get("error") else 0
        c.close()
    except Exception as e:
        out["error"] = str(e)
    return out


def openrouter_key_info():
    key = load_env(AGENT_DIR / "agent.env").get("OPENROUTER_API_KEY")
    if not key:
        return None
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.load(r).get("data", {})
    except Exception as e:
        return {"error": str(e)}


def groq_limits():
    key = load_env(AGENT_DIR / "agent.env").get("GROQ_API_KEY")
    if not key:
        return None
    body = json.dumps({"model": "openai/gpt-oss-20b", "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1}).encode()
    req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            h = r.headers
    except urllib.error.HTTPError as e:
        h = e.headers
    except Exception as e:
        return {"error": str(e)}
    return {k: h.get(f"x-ratelimit-{k}") for k in
            ("limit-requests", "remaining-requests", "reset-requests", "limit-tokens", "remaining-tokens", "reset-tokens")}


# ---------------------------------------------------------------- HTML
CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#14171a;--muted:#55606b;--line:#d9dee3;--accent:#1f5fbf;--accent-fg:#fff;
--ok:#1d7a3a;--ko:#b3261e;--warn:#8a5a00;--add:#e6f4ea;--del:#fdecea;--focus:#ffbf47}
@media (prefers-color-scheme:dark){:root{--bg:#111417;--card:#1b1f24;--fg:#e8eaed;--muted:#a3adb8;--line:#2f353c;
--accent:#6ea8ff;--accent-fg:#0b1220;--ok:#6fd38c;--ko:#ff8a80;--warn:#ffcc66;--add:#123321;--del:#3a1714}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
a{color:var(--accent)}main{max-width:900px;margin:0 auto;padding:16px}
.skip{position:absolute;left:-999px}.skip:focus{left:8px;top:8px;background:var(--card);padding:8px;z-index:9}
header{background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px}
header h1{margin:0;font-size:1.25rem}header nav a{margin-right:16px;display:inline-block;padding:8px 0;min-height:44px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:0 0 16px}
h2{font-size:1.15rem;margin:0 0 8px}h3{font-size:1rem;margin:16px 0 8px}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:600;font-size:.9rem;border:1px solid currentColor}
.ok{color:var(--ok)}.ko{color:var(--ko)}.warn{color:var(--warn)}.muted{color:var(--muted)}
.btn{display:inline-flex;align-items:center;justify-content:center;min-height:48px;min-width:48px;padding:10px 18px;
border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:var(--accent-fg);font-weight:600;
font-size:1rem;text-decoration:none;cursor:pointer;margin:4px 8px 4px 0}
.btn.sec{background:transparent;color:var(--accent)}.btn.danger{background:var(--ko);border-color:var(--ko);color:#fff}
:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
table{width:100%;border-collapse:collapse;font-size:.95rem}th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
.tablewrap{overflow-x:auto}
pre.diff{overflow-x:auto;font:13px/1.45 ui-monospace,monospace;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px;margin:0}
pre.diff span{display:block;white-space:pre}.da{background:var(--add)}.dd{background:var(--del)}.dh{color:var(--accent);font-weight:600}
label.check{display:flex;gap:10px;align-items:flex-start;min-height:48px;padding:8px 0}label.check input{width:24px;height:24px;flex:none}
.msg{border-left:5px solid currentColor;padding:12px;background:var(--card);margin-bottom:16px;border-radius:6px}
details summary{min-height:44px;display:flex;align-items:center;cursor:pointer;font-weight:600}
"""


def page(title, body):
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} - Console DEV</title>
<style>{CSS}</style></head><body><a class="skip" href="#contenu">Aller au contenu</a>
<header><h1>Console DEV</h1><nav aria-label="Navigation principale"><a href="/">Projets</a><a href="/quotas">Quotas</a>
<a href="/historique">Historique</a></nav></header><main id="contenu">{body}</main></body></html>"""


def state_badge(st):
    cls = "ok" if st == "active" else "ko"
    label = "en ligne" if st == "active" else f"arrêté ({st})"
    return f'<span class="badge {cls}">PROD {esc(label)}</span>'


def render_index():
    parts = []
    for p in PROJECTS.values():
        err = refresh_incoming(p)
        commits = pending(p)
        n = len(commits)
        pend = (f'<p class="warn"><strong>{n} modification{"s" if n > 1 else ""} en attente</strong></p>' if n
                else '<p class="muted">Aucune modification en attente.</p>')
        if err:
            pend = f'<p class="ko">{esc(err)}</p>'
        parts.append(f"""<section class="card" aria-labelledby="p-{esc(p['name'])}">
<h2 id="p-{esc(p['name'])}">{esc(p['name'])}</h2><p>{state_badge(service_state(p))}</p>{pend}
<p><a class="btn" href="/projet/{esc(p['name'])}">Relire et déployer</a>
<a class="btn sec" href="{esc(p['dev_url'])}" target="_blank" rel="noopener">Aperçu DEV</a>
<a class="btn sec" href="{esc(p['prod_url'])}" target="_blank" rel="noopener">PROD</a></p></section>""")
    parts.append(quota_summary())
    return page("Projets", "".join(parts))


def render_diff(text):
    lines = []
    for l in text.splitlines()[:4000]:
        cls = "dh" if l.startswith(("@@", "diff --git")) else "da" if l.startswith("+") and not l.startswith("+++") \
            else "dd" if l.startswith("-") and not l.startswith("---") else ""
        lines.append(f'<span class="{cls}">{esc(l) or " "}</span>')
    return f'<pre class="diff" tabindex="0" aria-label="Code modifié">{"".join(lines)}</pre>'


def render_project(p, result_id=None):
    out = []
    if result_id:
        h = next((x for x in read_history() if x.get("id") == result_id), None)
        if h:
            cls = "ok" if h["ok"] else "ko"
            out.append(f'<div class="msg {cls}" role="status"><strong>{"Réussi" if h["ok"] else "Échec"} :</strong> {esc(h["msg"])}</div>')
    err = refresh_incoming(p)
    out.append(f'<h2>{esc(p["name"])}</h2><p>{state_badge(service_state(p))}</p>')
    if err:
        out.append(f'<p class="ko">{esc(err)}</p>')
    dirty = prod_dirty(p)
    if dirty:
        out.append(f'<div class="msg ko"><strong>Attention :</strong> la PROD contient des modifications faites à la main. '
                   f'Le déploiement est bloqué pour ne pas les ecraser.<pre>{esc(dirty)}</pre></div>')
    commits = pending(p)
    if not commits:
        out.append('<section class="card"><p>Aucune modification en attente : la PROD est à jour avec le DEV.</p></section>')
    else:
        rows = "".join(f"<tr><td>{esc(c[1]) if len(c) > 1 else ''}</td><td>{esc(c[2]) if len(c) > 2 else ''}</td>"
                       f"<td><code>{esc(c[0])}</code></td></tr>" for c in commits)
        stat = git(p["prod"], "diff", "--stat", "main", "dev-incoming")[1]
        diff = git(p["prod"], "diff", "main", "dev-incoming")[1]
        expected = git(p["prod"], "rev-parse", "dev-incoming")[1]
        out.append(f"""<section class="card"><h3>Modifications en attente</h3><div class="tablewrap"><table>
<thead><tr><th scope="col">Date</th><th scope="col">Description</th><th scope="col">Ref</th></tr></thead><tbody>{rows}</tbody></table></div>
<h3>Fichiers touchés</h3><pre class="diff">{esc(stat)}</pre>
<details><summary>Voir le code modifié</summary>{render_diff(diff)}</details>
<p class="muted">Vérifie aussi le rendu sur l'<a href="{esc(p['dev_url'])}" target="_blank" rel="noopener">aperçu DEV</a>.</p>
<form method="post" action="/deployer"><input type="hidden" name="csrf" value="{CSRF}">
<input type="hidden" name="project" value="{esc(p['name'])}"><input type="hidden" name="expected" value="{esc(expected)}">
<label class="check"><input type="checkbox" name="ok" required> <span>J'ai vérifié l'aperçu DEV et la liste des fichiers touchés.</span></label>
<button class="btn" type="submit" {"disabled" if dirty else ""}>Déployer en PROD</button></form></section>""")
    last = next((h for h in reversed(read_history())
                 if h.get("project") == p["name"] and h.get("action") == "déploiement" and h.get("ok")), None)
    if last and last.get("to") == git(p["prod"], "rev-parse", "HEAD")[1]:
        out.append(f"""<section class="card"><h3>Annuler le dernier déploiement</h3>
<p>Déployé le {esc(last['date'])}. L'annulation remet la PROD dans l'état précédent et redémarre le service.
Les modifications restent dans le DEV.</p>
<form method="post" action="/annuler" onsubmit="return confirm('Annuler le dernier déploiement de {esc(p['name'])} ?')">
<input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="project" value="{esc(p['name'])}">
<button class="btn danger" type="submit">Annuler le dernier déploiement</button></form></section>""")
    return page(p["name"], "".join(out))


def fmt_tokens(n):
    return f"{n / 1000:.1f} k" if n >= 1000 else str(n)


def quota_summary():
    u = usage_stats()
    if u["error"]:
        return f'<section class="card"><h2>Quotas</h2><p class="ko">Lecture impossible : {esc(u["error"])}</p></section>'
    or_today = sum(v[0] for k, v in u["today"].items() if k.startswith("openrouter/"))
    left = max(0, OPENROUTER_FREE_PER_DAY - or_today)
    cls = "ok" if left > 20 else "warn" if left > 5 else "ko"
    return f"""<section class="card"><h2>Quotas du jour</h2>
<p>OpenRouter (modèles gratuits) : <strong class="{cls}">environ {left} requêtes restantes</strong> sur {OPENROUTER_FREE_PER_DAY}
<span class="muted">(remise à zéro à 1h du matin, heure de Paris en hiver)</span></p>
<p><a class="btn sec" href="/quotas">Détail des quotas</a></p></section>"""


def render_quotas(check_groq=False):
    u = usage_stats()
    out = ["<h2>Quotas</h2>"]
    if u["error"]:
        out.append(f'<p class="ko">Lecture de l\'historique OpenCode impossible : {esc(u["error"])}</p>')

    def table(bucket, title):
        rows = sorted(u[bucket].items(), key=lambda kv: -kv[1][0])
        if not rows:
            return f"<h3>{title}</h3><p class='muted'>Aucune requête.</p>"
        body = "".join(f"<tr><td>{esc(k)}</td><td>{v[0]}</td><td>{fmt_tokens(v[1])}</td><td>{v[2]}</td></tr>" for k, v in rows)
        return (f"<h3>{title}</h3><div class='tablewrap'><table><thead><tr><th scope='col'>Modèle</th><th scope='col'>Requêtes</th>"
                f"<th scope='col'>Tokens</th><th scope='col'>Erreurs</th></tr></thead><tbody>{body}</tbody></table></div>")

    out.append(f'<section class="card"><p class="muted">Ta consommation réelle, lue dans l\'historique de l\'agent. '
               f'Une requête correspond a une étape de l\'agent.</p>{table("today", "Aujourd&#39;hui (depuis minuit UTC)")}'
               f'{table("week", "7 derniers jours")}</section>')
    k = openrouter_key_info()
    if k is None:
        out.append('<section class="card"><h3>OpenRouter</h3><p class="muted">Pas de cle configuree.</p></section>')
    elif "error" in k:
        out.append(f'<section class="card"><h3>OpenRouter</h3><p class="ko">{esc(k["error"])}</p></section>')
    else:
        free = "oui" if k.get("is_free_tier") else "non"
        out.append(f"""<section class="card"><h3>OpenRouter</h3><ul>
<li>Compte gratuit, sans crédit : <strong>{free}</strong>. Limite : environ {OPENROUTER_FREE_PER_DAY} requêtes par jour sur les modèles gratuits, 20 par minute.</li>
<li>Dépense : <strong>{esc(k.get('usage', 0))} $</strong> (doit rester à 0).</li></ul></section>""")
    if check_groq:
        g = groq_limits()
        if not g or "error" in (g or {}):
            out.append(f'<section class="card"><h3>Groq</h3><p class="ko">{esc((g or {}).get("error", "Pas de cle"))}</p></section>')
        else:
            out.append(f"""<section class="card"><h3>Groq (mesurée à l'instant)</h3><ul>
<li>Requetes restantes aujourd'hui : <strong>{esc(g['remaining-requests'])}</strong> / {esc(g['limit-requests'])}
(remise à zéro dans {esc(g['reset-requests'])})</li>
<li>Tokens par minute : <strong>{esc(g['remaining-tokens'])}</strong> / {esc(g['limit-tokens'])}.
Cette limite est trop basse pour l'agent : Groq sert seulement aux petites tâches.</li></ul></section>""")
    else:
        out.append('<section class="card"><h3>Groq</h3><p>Mesurer coûte une mini requête.</p>'
                   '<p><a class="btn sec" href="/quotas?groq=1">Mesurer Groq maintenant</a></p></section>')
    return page("Quotas", "".join(out))


def render_history():
    rows = "".join(f"<tr><td>{esc(h['date'])}</td><td>{esc(h['project'])}</td><td>{esc(h['action'])}</td>"
                   f"<td class='{'ok' if h['ok'] else 'ko'}'>{'Réussi' if h['ok'] else 'Échec'}</td><td>{esc(h['msg'])}</td></tr>"
                   for h in reversed(read_history()[-100:]))
    body = (f"<h2>Historique</h2><section class='card'><div class='tablewrap'><table><thead><tr><th scope='col'>Date</th>"
            f"<th scope='col'>Projet</th><th scope='col'>Action</th><th scope='col'>Résultat</th><th scope='col'>Détail</th></tr>"
            f"</thead><tbody>{rows}</tbody></table></div></section>") if rows else "<h2>Historique</h2><p>Aucune action pour l'instant.</p>"
    return page("Historique", body)


# ---------------------------------------------------------------- serveur
class Handler(BaseHTTPRequestHandler):
    server_version = "ConsoleDEV"

    def log_message(self, fmt, *args):
        pass

    def authed(self):
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                _, pw = base64.b64decode(h[6:]).decode("utf-8").split(":", 1)
                return hmac.compare_digest(pw, PASSWORD)
            except Exception:
                return False
        return False

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        # same-origin (et pas no-referrer) : sinon Chrome envoie "Origin: null" sur nos propres formulaires
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def guard(self):
        if not self.authed():
            self.send(401, "Authentification requise", "text/plain; charset=utf-8", {"WWW-Authenticate": 'Basic realm="Console DEV"'})
            return False
        return True

    def do_GET(self):
        if not self.guard():
            return
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/":
                return self.send(200, render_index())
            if u.path == "/quotas":
                return self.send(200, render_quotas(q.get("groq") == ["1"]))
            if u.path == "/historique":
                return self.send(200, render_history())
            if u.path.startswith("/projet/"):
                p = PROJECTS.get(u.path[len("/projet/"):])
                if p:
                    rid = q.get("r", [""])[0]
                    return self.send(200, render_project(p, int(rid) if rid.isdigit() else None))
            self.send(404, page("Introuvable", "<p>Page introuvable.</p>"))
        except Exception as e:
            self.send(500, page("Erreur", f"<p class='ko'>Erreur : {esc(e)}</p>"))

    def do_POST(self):
        if not self.guard():
            return
        # Navigateurs récents : Sec-Fetch-Site fait foi. Sinon, on compare l'Origin quand elle est réelle.
        site = self.headers.get("Sec-Fetch-Site")
        origin = self.headers.get("Origin")
        if site is not None:
            if site not in ("same-origin", "none"):
                return self.send(403, f"Origine refusée (site={site})", "text/plain; charset=utf-8")
        elif origin and origin != "null" and urlparse(origin).netloc != self.headers.get("Host"):
            return self.send(403, "Origine refusée (origin)", "text/plain; charset=utf-8")
        n = min(int(self.headers.get("Content-Length", "0") or 0), 10000)
        form = parse_qs(self.rfile.read(n).decode("utf-8"))
        f = {k: v[0] for k, v in form.items()}
        if not hmac.compare_digest(f.get("csrf", ""), CSRF):
            return self.send(403, page("Refusé", "<p>Jeton expiré (la console a redémarré). <a href='/'>Recharge la page</a>.</p>"))
        p = PROJECTS.get(f.get("project", ""))
        if not p:
            return self.send(400, "Projet inconnu", "text/plain; charset=utf-8")
        if self.path == "/deployer":
            rid = deploy(p, f.get("expected", ""))
        elif self.path == "/annuler":
            rid = rollback(p)
        else:
            return self.send(404, "Introuvable", "text/plain; charset=utf-8")
        self.send(303, "", extra={"Location": f"/projet/{p['name']}?r={rid}"})


if __name__ == "__main__":
    if len(PASSWORD) < 10:
        raise SystemExit("ERREUR : DEVCONSOLE_PASSWORD absent ou trop court (10 caracteres minimum) dans console.env")
    if not BIND or BIND in ("0.0.0.0", "::"):
        raise SystemExit("ERREUR : DEVCONSOLE_BIND doit etre l'IP Tailscale de la machine (jamais 0.0.0.0)")
    print(f"Console DEV sur http://{BIND}:{PORT}")
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
