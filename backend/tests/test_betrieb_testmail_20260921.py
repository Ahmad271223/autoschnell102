# -*- coding: utf-8 -*-
"""Betrieb 21.09.2026 (Wunsch Ahmad): Meldungen pruefbar und robust.

  1. deploy/env_setzen.sh liess die .env nach jedem Aufruf fuer JEDEN
     Benutzer auf dem Server lesbar zurueck (Zwischendatei 644 + mv) —
     DEPLOYMENT.md verlangt 600. Jetzt umask 077 und chmod 600.
  2. Betrieb-Seite: "Meldungen gehen an: <adresse>" und ein Knopf
     "Testmail senden" (POST /api/admin/betrieb/testmail). Bisher war der
     einzige Test der Tagesbericht um 8 Uhr.
  3. Scheiterte der Tagesbericht, hielt die Tagessperre trotzdem 20 h — der
     naechste Versuch kam erst am naechsten Morgen. Jetzt wird die Sperre
     auf 45 min verkuerzt, hoechstens vier Versuche am Tag.
  4. ANBIETER_TAGESWARNUNG 500 -> 5000: bei 1.000–3.240 erwarteten
     Apify-Abrufen am Tag kam der Alarm (samt Mail) sonst jeden Tag.

In-Prozess: die Route wird direkt aufgerufen (Versand durch Attrappen
ersetzt), die Tagesbericht-Tests laufen gegen eine Wegwerf-Datenbank.
Kein Server noetig.
"""
import asyncio
import inspect
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import betriebsmeldung as BM  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SA = {"id": "sa_testmail_0921", "role": "admin", "is_super_admin": True,
      "active": True, "username": "chef_betrieb"}
_SH = shutil.which("sh")


# ============================================================ 1. env_setzen.sh
def _skript() -> str:
    return (WURZEL / "deploy" / "env_setzen.sh").read_text(encoding="utf-8")


def test_01_env_setzen_haelt_die_rechte_im_quelltext():
    code = "\n".join(z.split("#", 1)[0] for z in _skript().splitlines())
    assert "umask 077" in code, "Zwischendatei und Sicherung entstehen sonst mit 644"
    assert code.index("umask 077") < code.index('cp "$DATEI"'), \
        "umask muss VOR der Sicherung und der Zwischendatei stehen"
    assert code.index("umask 077") < code.index('> "$tmp"')
    assert 'chmod 600 "$DATEI"' in code, "eine schon offene .env wird sonst nicht repariert"
    assert code.index('mv "$tmp" "$DATEI"') < code.index('chmod 600 "$DATEI"')
    # POSIX-sh: keine Bash-Erweiterungen, kein GNU-only chmod --reference
    assert "--reference" not in code and "[[" not in code


def test_02_env_setzen_nennt_den_sicheren_neustart():
    kopf = _skript().split("set -eu", 1)[0]
    assert "touch deploy/drain/aktiv" in kopf and "sh deploy/freigeben.sh" in kopf
    assert "chmod 600" in kopf


def _lauf(tmp_path, *paare):
    env = dict(os.environ)
    env["ENV_DATEI"] = str(tmp_path / ".env").replace("\\", "/")
    return subprocess.run([_SH, str(WURZEL / "deploy" / "env_setzen.sh"), *paare],
                          env=env, cwd=str(tmp_path), capture_output=True,
                          text=True, timeout=60)


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
def test_03_env_setzen_setzt_werte_wie_bisher(tmp_path):
    datei = tmp_path / ".env"
    datei.write_text("A=1\nBETRIEB_MELDUNG_AN=alt@x.de\nB=2\nBETRIEB_MELDUNG_AN=dublette\n",
                     encoding="utf-8", newline="\n")
    r = _lauf(tmp_path, "BETRIEB_MELDUNG_AN=ahmadfkh006@gmail.com", "NEU=5")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Gesetzt in" in r.stdout and "BETRIEB_MELDUNG_AN=ahmadfkh006@gmail.com" in r.stdout
    zeilen = datei.read_text(encoding="utf-8").splitlines()
    assert zeilen == ["A=1", "BETRIEB_MELDUNG_AN=ahmadfkh006@gmail.com", "B=2", "NEU=5"], zeilen
    assert len(list(tmp_path.glob(".env.bak-*"))) == 1, "Sicherung wie bisher"
    assert not list(tmp_path.glob(".env.neu.*")), "keine Zwischendatei bleibt liegen"


@pytest.mark.skipif(not _SH or os.name == "nt",
                    reason="Dateirechte nur unter Linux/macOS pruefbar")
def test_04_env_setzen_laesst_die_env_nur_fuer_den_besitzer_lesbar(tmp_path):
    datei = tmp_path / ".env"
    datei.write_text("A=1\n", encoding="utf-8")
    # Stand nach einem aelteren Aufruf: fuer alle lesbar
    datei.chmod(0o644)
    r = _lauf(tmp_path, "BETRIEB_MELDUNG_AN=ahmadfkh006@gmail.com")
    assert r.returncode == 0, r.stdout + r.stderr
    assert stat.S_IMODE(datei.stat().st_mode) == 0o600, oct(datei.stat().st_mode)
    for sicherung in tmp_path.glob(".env.bak-*"):
        assert stat.S_IMODE(sicherung.stat().st_mode) & 0o077 == 0, \
            f"Sicherung {sicherung.name} ist fuer andere lesbar"


# ============================================================ 2. Testmail
@pytest.fixture
def testmail(monkeypatch):
    """Route direkt aufrufen; Versand und Einrichtung per Attrappe."""
    import email_service
    import provider_fetch
    import routes.admin as A
    monkeypatch.setitem(A._betrieb_testmail, "zuletzt", None)
    monkeypatch.setattr(provider_fetch, "MOCK_PROVIDER_FETCH", False)
    monkeypatch.setenv("BETRIEB_MELDUNG_AN", "ahmadfkh006@gmail.com")
    monkeypatch.setattr(email_service, "email_configured", lambda: True)
    gesendet = []

    async def _ok(to, betreff, text, *a, **k):
        gesendet.append({"to": to, "betreff": betreff, "text": text,
                         "key": k.get("idempotency_key")})
        return True, "resend:re_123"

    monkeypatch.setattr(email_service, "send_email_mit_beleg", _ok)

    def aufrufen():
        return asyncio.run(A.admin_betrieb_testmail(admin=SA))

    return SimpleNamespace(A=A, mail=email_service, pf=provider_fetch,
                           gesendet=gesendet, aufrufen=aufrufen)


def _fehler(t) -> HTTPException:
    with pytest.raises(HTTPException) as e:
        t.aufrufen()
    return e.value


def test_10_nur_super_admin():
    import routes.admin as A
    from deps import current_super_admin
    route = next(r for r in A.router.routes
                 if getattr(r, "path", "") == "/admin/betrieb/testmail")
    assert route.methods == {"POST"}
    assert any(d.call is current_super_admin for d in route.dependant.dependencies), \
        "dieselbe Sperre wie die Betrieb-Seite"


def test_11_ohne_adresse_400(testmail, monkeypatch):
    monkeypatch.delenv("BETRIEB_MELDUNG_AN", raising=False)
    e = _fehler(testmail)
    assert e.status_code == 400
    assert e.detail == "Keine Adresse eingetragen (BETRIEB_MELDUNG_AN)"
    assert not testmail.gesendet


@pytest.mark.parametrize("wert", ["a@x.de, b@y.de", '"ahmad@x.de"', "kein-at-zeichen"])
def test_12_ungueltige_adresse_400(testmail, monkeypatch, wert):
    monkeypatch.setenv("BETRIEB_MELDUNG_AN", wert)
    e = _fehler(testmail)
    assert e.status_code == 400 and "keine gültige E-Mail-Adresse" in e.detail
    assert not testmail.gesendet


def test_13_ohne_versandweg_503(testmail, monkeypatch):
    monkeypatch.setattr(testmail.mail, "email_configured", lambda: False)
    e = _fehler(testmail)
    assert e.status_code == 503 and "RESEND_API_KEY" in e.detail
    assert not testmail.gesendet
    # Ein 503 verbraucht die Minute nicht — nach dem Eintragen sofort testbar
    assert testmail.A._betrieb_testmail["zuletzt"] is None


def test_14_versand_ueber_den_betriebsweg(testmail):
    r = testmail.aufrufen()
    assert r == {"ok": True, "an": "ahmadfkh006@gmail.com", "zustellung": "resend",
                 "beleg": "resend:re_123"}
    assert len(testmail.gesendet) == 1
    m = testmail.gesendet[0]
    assert m["to"] == "ahmadfkh006@gmail.com"
    assert "Testmail" in m["betreff"]
    assert "ahmadfkh006@gmail.com" in m["text"] and "chef_betrieb" in m["text"]
    assert "Tagesbericht" in m["text"]
    # Eigener Schluessel je Klick (Resend-Wiederholungen sicher, neuer Klick = neue Mail)
    assert m["key"].startswith("betrieb-testmail-") and len(m["key"]) > 20


def test_15_hoechstens_eine_je_minute(testmail):
    testmail.aufrufen()
    e = _fehler(testmail)
    assert e.status_code == 429 and "pro Minute" in e.detail
    assert len(testmail.gesendet) == 1, "kein zweiter Versand"
    # Eine Minute spaeter geht es wieder
    testmail.A._betrieb_testmail["zuletzt"] -= testmail.A.BETRIEB_TESTMAIL_ABSTAND_S + 1
    testmail.aufrufen()
    assert len(testmail.gesendet) == 2


def test_16_ablehnung_kommt_als_ok_false_mit_grund(testmail, monkeypatch):
    """Pruefung 21.09.2026 (Betrieb): KEIN 502 mehr — Cloudflare ersetzt 502/504
    vom Server durch eine eigene Fehlerseite, der Grund kam nie im Browser an.
    Jetzt 200 mit {ok: false, grund}."""
    async def _abgelehnt(to, betreff, text, *a, **k):
        testmail.mail.log.error("email_service: Resend lehnt ab (HTTP %s): %s", 403,
                                '{"message":"The gmail.com domain is not verified"}')
        return False, ""

    monkeypatch.setattr(testmail.mail, "send_email_mit_beleg", _abgelehnt)
    r = testmail.aufrufen()                  # keine HTTPException
    assert r["ok"] is False and r["an"] == "ahmadfkh006@gmail.com"
    assert r["zustellung"] == "abgelehnt"
    assert "nicht zugestellt" in r["grund"] and "domain is not verified" in r["grund"]
    assert "HTTP 403" in r["grund"] and "grep email_service" in r["grund"]
    # Der Mitschreiber haengt danach nicht mehr am Logger
    assert not [h for h in testmail.mail.log.handlers if isinstance(h, BM._Mitschrift)]


def test_17_ablehnung_ohne_protokollzeile_hat_trotzdem_einen_grund(testmail, monkeypatch):
    async def _stumm(*a, **k):
        return False, ""

    monkeypatch.setattr(testmail.mail, "send_email_mit_beleg", _stumm)
    r = testmail.aufrufen()
    assert r["ok"] is False and "nicht angenommen" in r["grund"]


def test_17b_kein_502_oder_504_im_quelltext_der_route():
    import routes.admin as A
    q = inspect.getsource(A.admin_betrieb_testmail)
    for status in ("502", "504"):
        assert f"HTTPException({status}" not in q, f"Cloudflare ersetzt {status} — Grund ginge verloren"


def test_17c_haengender_anbieter_endet_nach_der_frist(testmail, monkeypatch):
    """Pruefung 21.09.2026 (Betrieb): ein nicht erreichbarer Resend hielt die
    Anfrage bis zu ~3 Minuten offen (axios bricht nach 60 s ab, Cloudflare nach
    ~100 s). Jetzt hoechstens BETRIEB_TESTMAIL_MAX_S, dann eine klare Meldung."""
    import time
    abgebrochen = []

    async def _haengt(*a, **k):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            abgebrochen.append(True)
            raise
        return True, "resend:zu_spaet"

    monkeypatch.setattr(testmail.mail, "send_email_mit_beleg", _haengt)
    monkeypatch.setattr(testmail.A, "BETRIEB_TESTMAIL_MAX_S", 0.2)
    t0 = time.monotonic()
    r = testmail.aufrufen()
    assert time.monotonic() - t0 < 5, "die Frist muss den Versand wirklich abbrechen"
    assert r["ok"] is False and r["zustellung"] == "unklar"
    assert "nicht innerhalb von" in r["grund"] and "Ausgang unklar" in r["grund"]
    assert "grep email_service" in r["grund"]
    assert abgebrochen, "der haengende Versand wird abgebrochen, nicht nur verlassen"
    assert not [h for h in testmail.mail.log.handlers if isinstance(h, BM._Mitschrift)], \
        "auch nach dem Abbruch haengt der Mitschreiber nicht mehr am Logger"


def test_17d_frist_liegt_unter_browser_und_cloudflare():
    import routes.admin as A
    assert A.BETRIEB_TESTMAIL_MAX_S == 45
    assert A.BETRIEB_TESTMAIL_MAX_S < 60, "axios bricht nach 60 s ab"
    q = inspect.getsource(A.admin_betrieb_testmail)
    assert "asyncio.wait_for(" in q and "timeout=BETRIEB_TESTMAIL_MAX_S" in q
    assert "except asyncio.TimeoutError" in q


def test_18_fremde_mailfehler_landen_nicht_in_der_antwort(testmail, monkeypatch):
    """Gleichzeitige Vertragsmails anderer Nutzer schreiben ueber denselben
    Logger — deren Zeilen (mit Kundenadressen) gehoeren nicht in die Antwort."""
    import routes.admin as A

    async def lauf():
        los = asyncio.Event()

        async def fremd():            # eigene Aufgabe, VOR dem Testmail-Aufruf gestartet
            await los.wait()
            testmail.mail.log.error("email_service: Versand an kunde@beispiel.de "
                                    "fehlgeschlagen: fremder Fehler")

        async def _abgelehnt(to, betreff, text, *a, **k):
            los.set()
            await asyncio.sleep(0.05)   # die fremde Aufgabe schreibt jetzt
            testmail.mail.log.error("email_service: Resend lehnt ab (HTTP 422): eigener Grund")
            return False, ""

        monkeypatch.setattr(testmail.mail, "send_email_mit_beleg", _abgelehnt)
        andere = asyncio.ensure_future(fremd())
        await asyncio.sleep(0)
        erg = await A.admin_betrieb_testmail(admin=SA)
        await andere
        return erg

    r = asyncio.run(lauf())
    assert r["ok"] is False and "eigener Grund" in r["grund"]
    assert "kunde@beispiel.de" not in r["grund"] and "fremder Fehler" not in r["grund"]


def test_19_testbetrieb_verschickt_nichts(testmail, monkeypatch):
    monkeypatch.setattr(testmail.pf, "MOCK_PROVIDER_FETCH", True)
    monkeypatch.setattr(testmail.mail, "email_configured", lambda: False)
    r = testmail.aufrufen()
    assert r == {"ok": True, "an": "ahmadfkh006@gmail.com", "zustellung": "mock"}
    assert not testmail.gesendet


def test_20_mailtext_nennt_empfaenger_und_berichtszeit(monkeypatch):
    monkeypatch.setenv("BETRIEB_TAGESBERICHT_STUNDE", "7")
    betreff, text = BM.testmail_text("x@y.de", von="chef", server="prod2")
    assert betreff == "AutoSchnell: Testmail der Betriebsmeldungen"
    assert "x@y.de" in text and "von chef" in text and "Server: prod2" in text
    assert "07:00 Uhr" in text and "Spam" in text
    monkeypatch.setenv("BETRIEB_TAGESBERICHT_STUNDE", "-1")
    assert "abgeschaltet" in BM.testmail_text("x@y.de")[1]


def test_21_oberflaeche_zeigt_empfaenger_und_knopf():
    q = (WURZEL / "frontend" / "src" / "pages" / "admin_v2" / "Betrieb.jsx").read_text(
        encoding="utf-8")
    assert "Meldungen gehen an:" in q and "Testmail senden" in q
    assert 'api.post("/admin/betrieb/testmail")' in q
    assert 'data-testid="alarm-empfaenger-fehlt"' in q, "der gelbe Hinweis bleibt"
    # Pruefung 21.09.2026 (Betrieb): ok:false (200) zeigt den Grund rot an
    assert "r.data?.ok === false" in q and "toast.error(r.data.grund" in q
    assert q.index("r.data?.ok === false") < q.index('r.data?.zustellung === "mock"')


def test_22_card_reicht_data_testid_weiter():
    """Pruefung 21.09.2026 (Betrieb): Card verwarf weitere Props —
    data-testid="alarm-empfaenger" (und aeltere) standen nie im DOM."""
    q = (WURZEL / "frontend" / "src" / "pages" / "admin_v2" / "_ui.jsx").read_text(
        encoding="utf-8")
    kopf = re.search(r"export function Card\(\{([^}]*)\}\)", q)
    assert kopf and "...rest" in kopf.group(1), "Card nimmt die uebrigen Props nicht an"
    rumpf = q[kopf.end():q.index("export function StatCard")]
    assert "{...rest}" in rumpf, "Card reicht sie nicht an das div weiter"
    # className/style der Karte bleiben massgeblich: ...rest steht davor
    assert rumpf.index("{...rest}") < rumpf.index("className=") < rumpf.index("style=")


# ============================================================ 1b. Betriebsdoku/Rollout
def _abschnitt(doku: str, titel: str) -> str:
    start = doku.index(titel)
    ende = doku.find("\n### ", start + len(titel))
    return doku[start:ende if ende > 0 else len(doku)]


def _ausfuehrbar(skript: str) -> str:
    return "\n".join(z for z in skript.splitlines() if not z.lstrip().startswith("#"))


WAECHTER = "grep -q '^COMPOSE_FILE=' .env || export COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.replica.yml"


def test_05_doku_nennt_chmod_fuer_alte_server_und_compose_waechter():
    """Pruefung 21.09.2026 (Betrieb): (a) auf dem Server liegt bis zum git pull
    noch das alte env_setzen.sh — die .env blieb beim ersten Aufruf nach Doku
    offen; (b) der Neustart-Block ohne COMPOSE_FILE-Pruefung warf das
    Replikat-Mitglied aus rs0, wenn der Eintrag in der .env fehlt."""
    doku = (WURZEL / "DEPLOYMENT.md").read_text(encoding="utf-8")
    for titel in ("### Betriebsmeldungen per E-Mail", "### Tageslimit je Konto und Werte in der .env setzen"):
        teil = _abschnitt(doku, titel)
        assert "Server mit Stand vor dem 21.09.2026" in teil, titel
        assert "chmod 600 .env .env.bak-*" in teil, titel
        assert WAECHTER in teil, titel
        neustarts = [z for z in teil.splitlines() if z.strip().startswith("docker compose up -d")]
        assert neustarts, f"{titel}: kein Neustart-Block"
        for zeile in neustarts:
            assert teil.index(WAECHTER) < teil.index(zeile), f"{titel}: Waechter nach dem Neustart"
    betrieb = _abschnitt(doku, "### Betriebsmeldungen per E-Mail")
    assert "502 „Testmail" not in betrieb, "die Antwort ist kein 502 mehr"
    assert '"ok": false' in betrieb and "45 s" in betrieb


def test_06_env_setzen_kopf_nennt_den_compose_waechter():
    kopf = _skript().split("set -eu", 1)[0]
    assert WAECHTER in kopf
    assert kopf.index(WAECHTER) < kopf.index("docker compose up -d")
    assert "chmod 600 .env .env.bak-*" in kopf


def test_07_rollout_schliesst_die_env_nach_dem_pull():
    roh = (WURZEL / "deploy" / "rollout.sh").read_text(encoding="utf-8")
    s = _ausfuehrbar(roh)
    zeile = "chmod 600 .env .env.bak-* 2>/dev/null || true"
    assert zeile in s, "Rollout setzt die Rechte der .env nicht"
    assert s.index("git pull --ff-only") < s.index(zeile) < s.index("up -d --build"), \
        "nach dem Pull (neuer Stand), vor dem Neustart"
    assert "[[" not in s and "--reference" not in s, "POSIX-sh"


@pytest.mark.skipif(not _SH or os.name == "nt",
                    reason="Dateirechte nur unter Linux/macOS pruefbar")
def test_08_rollout_setzt_die_rechte_wirklich(tmp_path):
    from test_rollout import _skript_lauf
    verz = tmp_path / "checkout"
    (verz / "deploy" / "drain").mkdir(parents=True)
    alt = verz / ".env.bak-20260916-101010"
    alt.write_text("A=1\n", encoding="utf-8")
    alt.chmod(0o644)
    # _skript_lauf schreibt die .env neu (644 nach umask 022)
    rc, out, _marker, _aufrufe = _skript_lauf(tmp_path, "rollout.sh")
    assert rc == 0, out
    assert stat.S_IMODE((verz / ".env").stat().st_mode) == 0o600
    assert stat.S_IMODE(alt.stat().st_mode) == 0o600


# ============================================================ 3. Tagesbericht
@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_bm0921_{uuid.uuid4().hex[:10]}"
    db = client[name]
    # Wie beim Serverstart: ohne den eindeutigen Index legte acquire() per
    # upsert einfach eine ZWEITE Sperre desselben Namens an.
    from job_lock import ensure_lock_index
    loop.run_until_complete(ensure_lock_index(db))
    monkeypatch.setenv("BETRIEB_MELDUNG_AN", "ahmadfkh006@gmail.com")
    monkeypatch.setenv("BETRIEB_TAGESBERICHT_STUNDE", "8")
    versuche = []
    antworten = []           # False/True je Versuch, danach True

    async def _send(to, betreff, text, *a, **k):
        versuche.append(k.get("idempotency_key"))
        return antworten.pop(0) if antworten else True

    import email_service
    monkeypatch.setattr(email_service, "send_email", _send)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete,
                              versuche=versuche, antworten=antworten)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


NAME = "tagesbericht-2026-09-21"


async def _sperre(db):
    doc = await db.job_locks.find_one({"name": NAME})
    ablauf = doc["expires_at"]
    if ablauf.tzinfo is None:
        ablauf = ablauf.replace(tzinfo=timezone.utc)
    return doc, (ablauf - datetime.now(timezone.utc)).total_seconds()


async def _ablaufen_lassen(db):
    await db.job_locks.update_one(
        {"name": NAME},
        {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})


def test_30_fehlschlag_verkuerzt_die_tagessperre(welt):
    from job_lock import acquire
    welt.antworten.extend([False])

    async def lauf():
        token = await acquire(welt.db, NAME, ttl_seconds=20 * 3600)
        ok = await BM.tagesbericht_mit_wiederholung(welt.db, NAME, token, "21.09.2026")
        doc, rest = await _sperre(welt.db)
        gleich_nochmal = await acquire(welt.db, NAME, ttl_seconds=20 * 3600)
        return ok, doc, rest, gleich_nochmal

    ok, doc, rest, gleich_nochmal = welt.run(lauf())
    assert ok is False
    assert doc["versuche"] == 1
    assert 30 * 60 <= rest <= BM.TAGESBERICHT_WIEDERHOLUNG_MIN * 60 + 5, \
        f"Sperre haelt {rest:.0f} s statt ~45 min"
    assert gleich_nochmal is None, "nicht sofort erneut (kein Sturm alle 10 min)"


def test_31_spaeterer_versuch_schickt_mit_neuem_schluessel_und_haelt_dann_20h(welt):
    from job_lock import acquire
    welt.antworten.extend([False])

    async def lauf():
        t1 = await acquire(welt.db, NAME, ttl_seconds=20 * 3600)
        await BM.tagesbericht_mit_wiederholung(welt.db, NAME, t1, "21.09.2026")
        await _ablaufen_lassen(welt.db)          # 45 min spaeter
        t2 = await acquire(welt.db, NAME, ttl_seconds=20 * 3600)
        ok = await BM.tagesbericht_mit_wiederholung(welt.db, NAME, t2, "21.09.2026")
        doc, rest = await _sperre(welt.db)
        return t2, ok, doc, rest

    t2, ok, doc, rest = welt.run(lauf())
    assert t2, "nach Ablauf der kurzen Sperre darf ein Prozess es erneut versuchen"
    assert ok is True and doc["versuche"] == 2
    assert welt.versuche == ["tagesbericht-21.09.2026", "tagesbericht-21.09.2026-v2"], \
        "jeder Versuch mit eigenem Idempotency-Key"
    assert rest > 19 * 3600, "nach Erfolg: genau ein Bericht je Tag (20-h-Sperre)"


def test_32_nach_dem_letzten_versuch_ist_fuer_heute_schluss(welt):
    from job_lock import acquire
    welt.antworten.extend([False] * 10)

    async def lauf():
        ergebnisse = []
        for _ in range(BM.TAGESBERICHT_MAX_VERSUCHE):
            token = await acquire(welt.db, NAME, ttl_seconds=20 * 3600)
            ergebnisse.append(await BM.tagesbericht_mit_wiederholung(
                welt.db, NAME, token, "21.09.2026"))
            doc, rest = await _sperre(welt.db)
            if rest < 3600:
                await _ablaufen_lassen(welt.db)
        doc, rest = await _sperre(welt.db)
        return ergebnisse, doc, rest

    ergebnisse, doc, rest = welt.run(lauf())
    assert ergebnisse == [False] * BM.TAGESBERICHT_MAX_VERSUCHE
    assert len(welt.versuche) == BM.TAGESBERICHT_MAX_VERSUCHE
    assert doc["versuche"] == BM.TAGESBERICHT_MAX_VERSUCHE
    assert rest > 19 * 3600, "nach dem letzten Versuch haelt die Tagessperre wieder 20 h"


def test_33_erfolg_beim_ersten_mal_bleibt_wie_bisher(welt):
    from job_lock import acquire

    async def lauf():
        token = await acquire(welt.db, NAME, ttl_seconds=20 * 3600)
        ok = await BM.tagesbericht_mit_wiederholung(welt.db, NAME, token, "21.09.2026")
        doc, rest = await _sperre(welt.db)
        return ok, doc, rest

    ok, doc, rest = welt.run(lauf())
    assert ok is True and rest > 19 * 3600
    assert welt.versuche == ["tagesbericht-21.09.2026"], "Schluessel des ersten Versuchs unveraendert"


def test_34_die_schleife_nutzt_die_wiederholung():
    q = inspect.getsource(BM.run_betriebsmeldung_forever)
    assert 'acquire(db, f"tagesbericht-{tag}"' in q and "ttl_seconds=20 * 3600" in q
    assert "tagesbericht_mit_wiederholung(" in q
    assert "await tagesbericht_senden(" not in q, \
        "direkt senden hiesse wieder: Fehlschlag sperrt den ganzen Tag"
    assert "except Exception:" in inspect.getsource(BM.tagesbericht_mit_wiederholung)


# ============================================================ 4. Tageswarnung
def test_40_tageswarnung_passt_zum_erwarteten_verbrauch():
    compose = (WURZEL / "docker-compose.yml").read_text(encoding="utf-8")
    beispiel = (WURZEL / ".env.example").read_text(encoding="utf-8")
    c = re.search(r"ANBIETER_TAGESWARNUNG=\$\{ANBIETER_TAGESWARNUNG:-(\d+)\}", compose)
    b = re.search(r"^ANBIETER_TAGESWARNUNG=(\d+)", beispiel, re.M)
    assert c and b
    assert int(c.group(1)) == int(b.group(1)) == 5000, (c.group(1), b.group(1))
    # Nr. 72: dieselbe Zahl auch im Code-Standard und im env-Erzeuger
    code = (WURZEL / "backend" / "provider_fetch.py").read_text(encoding="utf-8")
    assert 'os.environ.get("ANBIETER_TAGESWARNUNG", "5000")' in code
    erzeuger = (WURZEL / "backend" / "scripts" / "env_erzeugen.py").read_text(encoding="utf-8")
    assert '"ANBIETER_TAGESWARNUNG=5000",' in erzeuger
    assert int(c.group(1)) > 3240, "sonst kommt der Alarm an jedem normalen Arbeitstag"


def test_41_mitschrift_ohne_testmail_sammelt_nichts():
    """Ausserhalb eines Testmail-Aufrufs ist der Mitschreiber stumm."""
    h = BM._Mitschrift(logging.WARNING)
    rec = logging.LogRecord("autohandel", logging.ERROR, __file__, 1,
                            "email_service: irgendwas", None, None)
    h.emit(rec)          # darf nicht werfen, sammelt nichts
    assert BM._mitschrift.get() is None
