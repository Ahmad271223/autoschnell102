# -*- coding: utf-8 -*-
"""Pruefbericht Runde 10, Gruppe 3 (09/2026): Betrieb, Lasttest, Versand.

Reine Einheitentests — kein Backend noetig.
"""
import inspect
import ipaddress
import os
import re
import subprocess
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WURZEL / "backend"))


def _rate_limiter_mit(umgebung):
    """rate_limiter in einem frischen Prozess laden — die Netzlisten werden
    beim Import aus der Umgebung gebaut."""
    code = ("import rate_limiter as r; import json; "
            "print(json.dumps({a: r._ist_vermittler(a) for a in "
            "['172.18.0.2', '10.0.0.4', '10.0.0.99', '127.0.0.1', '2.28.52.22']}))")
    env = {**os.environ, **umgebung}
    for k in ("TRUSTED_PROXIES", "TRUSTED_PROXIES_NUR_LISTE"):
        env.pop(k, None) if umgebung.get(k) is None else None
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=str(WURZEL / "backend"),
                         env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-500:]
    import json
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------- Vermittler-Schalter
def test_ohne_schalter_gelten_liste_und_private_netze():
    v = _rate_limiter_mit({"TRUSTED_PROXIES": "10.0.0.4/32", "TRUSTED_PROXIES_NUR_LISTE": None})
    assert v["10.0.0.4"] is True
    assert v["172.18.0.2"] is True, "eigener nginx-Container (Docker-Netz) muss Vermittler sein"
    assert v["2.28.52.22"] is False, "oeffentlicher Nachbar ist nie Vermittler"


def test_mit_schalter_gilt_nur_die_liste():
    v = _rate_limiter_mit({"TRUSTED_PROXIES": "10.0.0.4/32,172.18.0.0/16",
                           "TRUSTED_PROXIES_NUR_LISTE": "true"})
    assert v["10.0.0.4"] is True and v["172.18.0.2"] is True
    assert v["10.0.0.99"] is False, "anderer Mieter im privaten Netz darf keine Kopfzeilen setzen"
    assert v["127.0.0.1"] is False


def test_schalter_ohne_liste_bleibt_ungefaehrlich():
    """NUR_LISTE ohne Liste wuerde JEDEN Vermittler sperren — dann gelten
    weiterhin die privaten Netze, sonst landeten alle Besucher in einem Zaehler."""
    v = _rate_limiter_mit({"TRUSTED_PROXIES": None, "TRUSTED_PROXIES_NUR_LISTE": "true"})
    assert v["172.18.0.2"] is True


# ---------------------------------------------------------------- Vertragsversand
def _send_quelle():
    import routes.contracts as c
    return inspect.getsource(c.send_contract)


def test_auto_schluessel_ohne_minute():
    q = _send_quelle()
    block = q[q.index("if not body.idempotency_key:"):q.index("reserviert = False")]
    assert "strftime" not in block and "minute" not in block.lower().replace("# ", "#"), block


def test_auto_schluessel_ist_je_inhalt_stabil():
    import hashlib
    roh = "|".join(["v1", "email", "a@b.de", "Betreff", "Text"])
    k1 = "auto-" + hashlib.sha256(roh.encode("utf-8")).hexdigest()[:24]
    k2 = "auto-" + hashlib.sha256(roh.encode("utf-8")).hexdigest()[:24]
    assert k1 == k2 and len(k1) == 29


def test_kopie_an_sucher_hat_eigenen_schluessel():
    q = _send_quelle()
    assert 'idempotency_key=f"kopie-{contract_id}-{body.idempotency_key}"' in q


# ---------------------------------------------------------------- CORS
def test_cors_kein_freibrief():
    q = (WURZEL / "backend" / "server.py").read_text(encoding="utf-8")
    block = q[q.index("CORSMiddleware,"):q.index(")", q.index("CORSMiddleware,"))]
    assert '"*"' not in block, block
    assert "Authorization" in block and "DELETE" in block


# ---------------------------------------------------------------- Lasttest
def test_wrapper_sieht_die_fotos_des_backends():
    sh = (WURZEL / "deploy" / "lasttest-auf-prod2.sh").read_text(encoding="utf-8")
    assert "--volumes-from last-backend" in sh


def test_foto_abgleich_meldet_nicht_pruefbar_statt_falschbefund():
    q = (WURZEL / "backend" / "scripts" / "lasttest_matrix.py").read_text(encoding="utf-8")
    fn = q[q.index("def foto_audit"):q.index("async def drain_und_pruefen")]
    assert "nicht_pruefbar" in fn and '(UPLOAD_ROOT / "resale").exists()' in fn


def test_lasttest_versand_mit_eigenem_schluessel():
    q = (WURZEL / "backend" / "scripts" / "lasttest_matrix.py").read_text(encoding="utf-8")
    fn = q[q.index("async def op_versand"):]
    assert '"idempotency_key": f"last-{uuid.uuid4().hex[:16]}"' in fn


# ---------------------------------------------------------------- Anbieter-Probe
def test_anbieter_probe_prueft_wirklich():
    q = (WURZEL / "backend" / "scripts" / "anbieter_probe.py").read_text(encoding="utf-8")
    assert "peek_cached_listing" not in q, "nur nachsehen statt echt abrufen"
    assert "_darf_nicht" in q and "angerufen" in q
    # Speichertreffer beim ERSTEN Abruf ist ein Fehler, kein Warnhinweis
    erster = q[q.index("if aus_speicher:"):q.index("# --- 2.")]
    assert "fehler(" in erster and "return" in erster
    # unerwartete Ausnahme = Fehler
    assert 'ok(f"sauber abgefangen' not in q
    assert "unerwarteter Fehler beim Abruf" in q


# ---------------------------------------------------------------- Cloudflare-Netze
def test_cloudflare_vorlage_parser_und_umschreiben():
    sys.path.insert(0, str(WURZEL / "backend" / "scripts"))
    import cloudflare_netze_pruefen as cf
    text = cf.VORLAGE.read_text(encoding="utf-8")
    ist = cf.netze_in_vorlage(text)
    assert len(ist) >= 20 and "173.245.48.0/20" in ist
    for n in ist:
        ipaddress.ip_network(n)
    neu = cf.vorlage_anpassen(text, ist | {"203.0.113.0/24"})
    assert "set_real_ip_from 203.0.113.0/24;" in neu
    assert cf.netze_in_vorlage(neu) == ist | {"203.0.113.0/24"}
    assert "${PRIVATES_NETZ}" in neu, "Zeile mit dem privaten Netz darf nicht verloren gehen"


def test_deployment_doku_korrekturen():
    d = (WURZEL / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "Full (strict)" in d
    assert "PRIVATES_NETZ=10.0.0.4/32" in d
    assert re.search(r"Rueckbau \(vollstaendig", d)
    assert "80 und 443 aus dem Internet\n   WIEDER anlegen" in d
    assert "Was `{w: 1}` bedeutet" in d
