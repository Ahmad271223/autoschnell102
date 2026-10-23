# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026, Reparaturwelle A7b (22.09.2026): Deploy-Skripte,
nginx-Vorlagen, Dockerfile, Dokumentation.

  DP-14 / K-20  Sicherheits-Kopfzeilen EINMAL in deploy/sicherheitskopf.inc,
                in beiden Vorlagen ueberall eingebunden, payment=()
  DP-12 / SK-15 zertifikat-erneuern.sh: hinter dem LB nichts tun, Trap und
                Zeitlimit zwischen stop und start
  DP-05         lasttest-auf-prod2.sh: Drain, SECONDARY-Pruefung, Speicher, --cpus
  AL-05         Sperrabfrage "python migrationen.py --sperre-gehalten"
                (Ablauf des Rollouts: tests/test_rollout.py)
  Doku          DP-10, DO-14, DO-16, DO-17, DO-19, DO-20, DO-21, DO-23, DO-24,
                SK-10, SK-16, T-11, T-17, T-24
"""
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[2]
BACKEND = WURZEL / "backend"
_SH = shutil.which("sh")


def _lies(*teile):
    return (WURZEL.joinpath(*teile)).read_text(encoding="utf-8")


def _ohne_kommentare(text):
    return "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))


def _bloecke(text, kopf):
    """Alle Bloecke, die mit `kopf` beginnen (Klammern gezaehlt)."""
    ergebnis = []
    start = 0
    while True:
        i = text.find(kopf, start)
        if i < 0:
            return ergebnis
        tiefe = 0
        for j in range(i, len(text)):
            if text[j] == "{":
                tiefe += 1
            elif text[j] == "}":
                tiefe -= 1
                if tiefe == 0:
                    ergebnis.append(text[i:j + 1])
                    start = j
                    break
        else:
            raise AssertionError(f"Block nicht geschlossen: {kopf}")


# ============================================================ DP-14 / K-20
INCLUDE = "include /etc/nginx/sicherheitskopf.inc;"
VORLAGEN = ("default.conf.template", "hinter-loadbalancer.conf.template")


def test_dp14_kopfzeilen_liegen_einmal_in_der_include_datei():
    inc = _ohne_kommentare(_lies("deploy", "sicherheitskopf.inc"))
    zeilen = [z.strip() for z in inc.splitlines() if z.strip()]
    assert all(z.startswith("add_header ") and z.endswith(" always;") for z in zeilen), zeilen
    namen = [z.split()[1] for z in zeilen]
    assert namen == ["Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options",
                     "Referrer-Policy", "Permissions-Policy", "Content-Security-Policy"], namen
    pp = next(z for z in zeilen if "Permissions-Policy" in z)
    assert "payment=()" in pp and "payment=(self)" not in pp, "K-20: Stripe ist weg"
    assert "frame-ancestors 'none'" in inc
    compose = _lies("docker-compose.yml")
    assert "./deploy/sicherheitskopf.inc:/etc/nginx/sicherheitskopf.inc:ro" in compose, \
        "ohne den Mount findet nginx die Datei nicht und startet nicht"


@pytest.mark.parametrize("name", VORLAGEN)
def test_dp14_jede_location_mit_eigenem_add_header_bindet_die_kopfzeilen_ein(name):
    t = _ohne_kommentare(_lies("deploy", name))
    assert "add_header Strict-Transport-Security" not in t, "steht nur noch in der Include-Datei"
    assert "payment=(self)" not in t
    # Der bediente server-Block (der mit den Kopfzeilen) enthaelt den include
    # auf oberster Ebene ...
    server = [b for b in _bloecke(t, "server {") if INCLUDE in b]
    assert len(server) == 1, "genau ein server-Block traegt die Kopfzeilen"
    aussen = re.sub(r"location[^{]*\{.*?\n  \}", "", server[0], flags=re.S)
    assert INCLUDE in aussen, "include gehoert in den server-Block selbst"
    # ... und JEDE Location mit eigenem add_header bindet sie erneut ein,
    # sonst gehen sie dort verloren (nginx vererbt add_header nicht).
    orte = [b for b in _bloecke(server[0], "location ") if "add_header" in b]
    assert orte, "mindestens /static/ setzt Cache-Control selbst"
    for ort in orte:
        assert INCLUDE in ort, ort.splitlines()[0]
    # Lasttest-Regel bleibt: kein "always" an der Jahres-Cache-Zeile.
    for zeile in t.splitlines():
        if "add_header Cache-Control" in zeile and "max-age=31536000" in zeile:
            assert "always" not in zeile, zeile


def test_dp14_rollout_zaehlt_die_include_datei_zum_vorlagenstand():
    s = _ohne_kommentare(_lies("deploy", "rollout.sh"))
    assert "SICHERHEITSKOPF=deploy/sicherheitskopf.inc" in s
    fn = s[s.index("vorlagen_stand() {"):s.index("VORLAGE_VORHER=")]
    assert '"$SICHERHEITSKOPF"' in fn and "md5sum" in fn


# ============================================================ DP-12 / SK-15
def test_dp12_sk15_zertifikat_skript_ist_hinter_dem_lb_wirkungslos_und_laesst_den_proxy_nie_aus():
    roh = _lies("deploy", "zertifikat-erneuern.sh")
    s = _ohne_kommentare(roh)
    lb = "grep -q '^PROXY_TEMPLATE=hinter-loadbalancer'"
    assert lb in s and "exit 3" in s[s.index(lb):s.index(lb) + 400]
    stop = s.index("docker compose stop proxy")
    assert s.index(lb) < stop, "Pruefung VOR dem Stopp des Proxys"
    # Trap direkt nach dem Stopp, certbot mit Zeitlimit, Trap vor dem regulaeren Start abgeraeumt
    nach_stop = s[stop:]
    assert re.search(r"^trap 'proxy_wieder_starten' EXIT$", nach_stop, re.M), "EXIT-Trap fehlt"
    assert "INT TERM HUP" in nach_stop
    assert "timeout 300 docker run --rm --name" in nach_stop
    assert nach_stop.index("trap '") < nach_stop.index("timeout 300 docker run")
    assert "trap - EXIT INT TERM HUP" in nach_stop
    assert nach_stop.index("timeout 300 docker run") < nach_stop.index("trap - EXIT INT TERM HUP") \
        < nach_stop.rindex("proxy_wieder_starten")
    fn = s[s.index("proxy_wieder_starten() {"):s.index("trap 'proxy_wieder_starten' EXIT")]
    assert 'docker rm -f "$CERTBOT_NAME"' in fn and "docker compose start proxy" in fn, \
        "erst certbot wegraeumen (Port 80), dann den Proxy starten"
    assert "-eq 124" in s, "Zeitlimit-Code von timeout wird erklaert"


# ============================================================ DP-05
def test_dp05_lasttest_nimmt_den_server_aus_der_rotation_und_prueft_die_vorbedingungen():
    roh = _lies("deploy", "lasttest-auf-prod2.sh")
    s = _ohne_kommentare(roh)
    # Fortsetzungszeilen ("\" am Zeilenende) zu einer Zeile zusammenziehen
    runs = [z for z in s.replace("\\\n", " ").splitlines()
            if z.lstrip().startswith("docker run -d --name last-")]
    assert len(runs) == 2 and all("--cpus 1.5" in z for z in runs), runs
    assert "free -m" in s and "-lt 8192" in s
    assert "rs.hello()" in s and "replicaSet=" in s and "SECONDARY" in s
    # Drain wie rollout.sh — nur hinter dem Load Balancer, nur eigener Marker wird entfernt
    assert "grep -q '^PROXY_TEMPLATE=hinter-loadbalancer'" in s
    assert "touch deploy/drain/aktiv" in s and "rm -f deploy/drain/aktiv" in s
    assert "touch /tmp/drain" in s and "rm -f /tmp/drain" in s
    assert 'sleep "$WARTE_LB"' in s
    assert "DRAIN_VON_UNS=1" in s and '[ "$DRAIN_VON_UNS" = 1 ] || return 0' in s
    aufr = s[s.index("aufraeumen() {"):s.index("trap aufraeumen EXIT INT TERM")]
    assert "drain_aufheben" in aufr, "Aufraeumen hebt den Drain wieder auf"
    assert s.index("touch deploy/drain/aktiv") < s.index('echo "== 1/4')
    # Alte Zusagen bleiben (test_befunde_runde10_nachpruefung2)
    assert "MOCK_PROVIDER_FETCH=true" in s and "APP_ENV=production" not in s
    assert not re.search(r"docker run[^\n]* -p ", s)


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
@pytest.mark.parametrize("name", ["zertifikat-erneuern.sh", "lasttest-auf-prod2.sh", "rollout.sh"])
def test_skripte_sind_posix_sh(name):
    r = subprocess.run([_SH, "-n", str(WURZEL / "deploy" / name)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ============================================================ AL-05 (Sperrabfrage)
def test_al05_sperrabfrage_meldet_gehalten_frei_und_stoerung():
    pymongo = pytest.importorskip("pymongo")
    url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    try:
        client = pymongo.MongoClient(url, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Mongo nicht erreichbar: {exc}")
    name = f"autoschnell_al05_{uuid.uuid4().hex[:8]}"
    db = client[name]
    env = dict(os.environ, MONGO_URL=url, DB_NAME=name, PYTHONIOENCODING="utf-8")
    env.pop("APP_ENV", None)

    def lauf():
        return subprocess.run([sys.executable, "migrationen.py", "--sperre-gehalten"],
                              cwd=str(BACKEND), env=env, capture_output=True, text=True,
                              timeout=120)
    try:
        r = lauf()
        assert r.returncode == 1 and "Migrationssperre: frei" in r.stdout, r.stdout + r.stderr
        db.job_locks.insert_one({"name": "migration", "owner": "test-1",
                                 "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5)})
        r = lauf()
        assert r.returncode == 0 and "gehalten von test-1" in r.stdout, r.stdout + r.stderr
        db.job_locks.update_one({"name": "migration"},
                                {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
        r = lauf()
        assert r.returncode == 1, "abgelaufene Sperre gilt als frei"
        # Nichts veraendert, keine Migration gelaufen
        assert db.system_flags.find_one({"_id": "schema"}) is None
    finally:
        client.drop_database(name)


# ============================================================ Dokumentation
def test_doku_deployment_nachgefuehrt():
    d = _lies("DEPLOYMENT.md")
    # DO-17
    assert "Anzahl CPU-Kerne des Servers." not in d
    assert "(BACKEND_MEM_LIMIT − 500 MB) / 400 MB" in d and "`RESEND_PROZESSE` und `APIFY_MAX_PARALLEL`" in d
    # DP-10
    assert "backup-<tag>" in d and "backup.server" in d
    # DO-14
    assert "-mtime +30" not in d and "tail -n +15 | xargs -r rm -rf" in d
    assert "ausser Haus die letzten 14 Sicherungen" in d
    # DO-21
    assert "je IP 300 Bilder/Minute" not in d and "`BILD_PROXY_LIMIT` (Standard 3000)" in d
    # DO-23
    assert "nächsten Vergleich des Links wird es erneut versucht" not in d
    assert "**Hängender Job**" in d and "db.job_locks.find(" in d and "db.link_jobs.deleteOne(" in d
    # SK-10
    stelle = d[d.index("## Stimmige Datensicherung ohne Replica Set"):d.index("## E-Mail-Versand über Resend")]
    assert "BACKUP_WARTUNG=true" in stelle and "sh deploy/env_setzen.sh BACKUP_WARTUNG=true" in stelle
    assert "Worker schreiben weiter" in stelle
    # SK-16
    tabelle = d[d.index("| `--dry-run` |"):d.index("**Wartungsmodus:**")]
    for flag in ("--notfall-inkonsistent-akzeptieren", "--alt-backup-ohne-indexdaten",
                 "--zusaetzliche-behalten", "--vorher-aufbewahrung TAGE"):
        assert f"| `{flag}` |" in tabelle, flag
    assert "Ohne die Option bleiben sie unverändert" not in tabelle
    assert "Standardverhalten" in tabelle.split("| `--exakt` |")[1].split("\n")[0]
    # AL-05 / DP-14
    assert "docker compose run -T --rm --no-deps backend python migrationen.py" in d
    assert "deploy/sicherheitskopf.inc" in d


def test_doku_docs_und_readmes_nachgefuehrt():
    ka = _lies("docs", "kleinanzeigen-abruf.md")
    assert "Stand 22.09.2026" in ka and "Stand 29.08.2026" not in ka
    assert "(Standard\n3)" not in ka and "(3)" not in ka and "Limit 3" not in ka
    assert "MAX_CONCURRENT_KLEINANZEIGEN` (Standard\n2)" in ka
    assert "**API-Dienst (Runde 29" in ka and "kleinanzeigen-agent.de" in ka
    assert "IP des API-Dienstes" in ka
    st = _lies("docs", "STAGING-CHECKLISTE.md")
    assert "gedrosselt auf 3" not in st and "Standard 2 gleichzeitig" in st
    assert "`GET /api/ready` ohne Anmeldung" in st and "BETRIEB_MELDUNG_AN" in st
    assert "und `/api/admin/monitoring`\n  (Alarm bei" not in st
    readme = _lies("README.md")
    assert "Here are your Instructions" not in readme
    for verweis in ("DEPLOYMENT.md", "frontend/README.md", "GO-LIVE-CHECKLISTE.md"):
        assert verweis in readme, verweis
    prd = _lies("memory", "PRD.md").splitlines()[:4]
    assert any("Historisch, Stand 02/2026" in z for z in prd)
    skips = _lies("docs", "tests", "UEBERSPRUNGENE_TESTS.md")
    assert "Stand: 22.09.2026" in skips and "Stand: 10.09.2026" not in skips


def test_t17_t24_frontend_doku_und_stack_rauchtest():
    cfg = _lies("frontend", "playwright.config.js")
    assert "APP_FASSUNG=1700000000-aaaaaaa REACT_APP_BACKEND_URL= yarn build" in cfg
    assert "E2E_STACK=1 E2E_BASE_URL=https://localhost yarn e2e" in cfg
    readme = _lies("frontend", "README.md")
    assert "APP_FASSUNG=1700000000-aaaaaaa REACT_APP_BACKEND_URL= yarn build" in readme
    assert "E2E_STACK=1 E2E_BASE_URL=https://localhost yarn e2e" in readme
    ci = _lies(".github", "workflows", "ci.yml")
    assert "APP_FASSUNG=1700000000-aaaaaaa REACT_APP_BACKEND_URL= CI=true yarn build" in ci, \
        "die Doku nennt denselben Stempel wie die CI"
    stack = _lies("frontend", "e2e", "stack.spec.js")
    assert 'const h = require("./helpers");' in stack
    assert "const BENUTZER = h.SUPER_ADMIN.username;" in stack
    assert "const PASSWORT = h.SUPER_ADMIN.password;" in stack
    assert "process.env.E2E_SUPER_ADMIN_USERNAME" not in stack
    helpers = _lies("frontend", "e2e", "helpers.js")
    assert "SUPER_ADMIN," in helpers.split("module.exports = {")[1], "helpers.js exportiert SUPER_ADMIN"
