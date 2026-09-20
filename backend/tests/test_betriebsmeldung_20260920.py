# -*- coding: utf-8 -*-
"""Betriebsmeldungen per E-Mail (20.09.2026, Wunsch Ahmad).

Bis heute landete jeder Fehler NUR in der Datenbank — sichtbar auf der
Betriebs-Seite, aber niemand erfuhr davon. Ging Samstagnacht etwas kaputt,
wusste es bis zum naechsten Hinsehen keiner.

Zwei Meldungen an BETRIEB_MELDUNG_AN:
  * sofort bei jedem NEUEN Betriebsalarm (mit Sammelfrist),
  * einmal taeglich ein Bericht — AUCH wenn alles in Ordnung ist. Eine
    Plattform, die schweigt, ist von einer toten nicht zu unterscheiden.
"""
import asyncio
import inspect
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import betriebsmeldung as BM  # noqa: E402
import deps  # noqa: E402

MONGO_URL = __import__("os").environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------- Einstellungen
def test_01_ohne_adresse_ist_alles_aus(monkeypatch):
    monkeypatch.delenv("BETRIEB_MELDUNG_AN", raising=False)
    assert BM.empfaenger() == ""
    monkeypatch.setenv("BETRIEB_MELDUNG_AN", "  ahmadfkh006@gmail.com  ")
    assert BM.empfaenger() == "ahmadfkh006@gmail.com"


@pytest.mark.parametrize("wert,erwartet", [
    ("", 10), ("5", 5), ("0", 1), ("quatsch", 10), ("60", 60),
])
def test_02_sammelfrist(monkeypatch, wert, erwartet):
    monkeypatch.setenv("BETRIEB_MELDUNG_SOFORT_MIN", wert)
    assert BM.sammelfrist_minuten() == erwartet


@pytest.mark.parametrize("wert,erwartet", [
    ("", 8), ("6", 6), ("-1", -1), ("23", 23), ("99", 8), ("quatsch", 8),
])
def test_03_berichtsstunde(monkeypatch, wert, erwartet):
    monkeypatch.setenv("BETRIEB_TAGESBERICHT_STUNDE", wert)
    assert BM.bericht_stunde() == erwartet


# ---------------------------------------------------------- Alarm-Mail
def test_04_ein_alarm_steht_im_betreff():
    betreff, text = BM.alarm_text([{
        "typ": "vertrag_nach_abholung_offen", "ref": "c-123",
        "details": {"fehler": "PDF fehlgeschlagen"}, "anzahl": 1,
        "created_at": "2026-09-20T07:00:00+00:00"}], gesamt_offen=1)
    assert "vertrag_nach_abholung_offen" in betreff
    assert "c-123" in text and "PDF fehlgeschlagen" in text
    assert "Betrieb-Seite" in text


def test_05_viele_alarme_ergeben_EINE_mail():
    alarme = [{"typ": f"typ{i}", "ref": f"r{i}", "details": {}, "anzahl": 1,
               "created_at": _jetzt()} for i in range(12)]
    betreff, text = BM.alarm_text(alarme, gesamt_offen=12)
    assert betreff == "AutoSchnell: 12 neue Betriebsalarme"
    for i in range(12):
        assert f"typ{i}" in text


def test_06_bei_einem_sturm_bleibt_die_mail_lesbar():
    alarme = [{"typ": f"typ{i}", "ref": "", "details": {}, "anzahl": 1,
               "created_at": _jetzt()} for i in range(200)]
    _betreff, text = BM.alarm_text(alarme, gesamt_offen=200)
    assert f"und {200 - BM.MAX_EINZELN} weitere" in text
    assert len(text) < 20000, "eine Mail mit 200 Alarmen liest niemand"


def test_07_wiederholung_wird_genannt():
    _b, text = BM.alarm_text([{"typ": "x", "ref": "", "details": {},
                               "anzahl": 7, "created_at": _jetzt()}], 1)
    assert "7x aufgetreten" in text


# -------------------------------------------------------- Tagesbericht
def _daten(**ueber):
    basis = {"alarme_offen": 0, "alarme_neu": 0, "fehler": 0, "fehler_wege": [],
             "link_jobs_haengend": 0,
             "backup": {"alter_stunden": 5.0, "vollstaendig": True,
                        "offsite": True, "stichtagsgenau": True}}
    basis.update(ueber)
    return basis


def test_08_alles_gut_steht_im_betreff():
    betreff, text = BM.bericht_text(_daten(), "20.09.2026")
    assert "alles in Ordnung" in betreff
    assert "Alles in Ordnung" in text
    assert "20.09.2026" in betreff


def test_09_auffaelligkeiten_stehen_im_betreff():
    betreff, text = BM.bericht_text(
        _daten(alarme_offen=2, fehler=5,
               fehler_wege=[{"weg": "/api/contracts", "anzahl": 4}]), "20.09.")
    assert "Auffaelligkeiten" in betreff
    assert "Offene Betriebsalarme: 2" in text
    assert "/api/contracts" in text and "4x" in text


def test_10_sicherungsmaengel_stehen_drin():
    _b, text = BM.bericht_text(_daten(backup={
        "alter_stunden": 40.0, "vollstaendig": False, "offsite": False,
        "stichtagsgenau": False}), "20.09.")
    assert "UNVOLLSTAENDIG" in text and "OHNE Kopie auswaerts" in text
    assert "nicht stichtagsgenau" in text


def test_11_der_bericht_kommt_auch_wenn_nichts_passiert():
    """Der eigentliche Zweck: ein Lebenszeichen."""
    _b, text = BM.bericht_text(_daten(), "20.09.")
    assert "Bleibt diese Mail einmal aus" in text


# ------------------------------------------------- mit echter Datenbank
@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_bm_{uuid.uuid4().hex[:10]}"
    db = client[name]
    monkeypatch.setattr(deps, "db", db)
    monkeypatch.setenv("BETRIEB_MELDUNG_AN", "ahmadfkh006@gmail.com")
    gesendet = []

    async def _fake_send(to, betreff, text, *a, **k):
        gesendet.append({"to": to, "betreff": betreff, "text": text,
                         "key": k.get("idempotency_key")})
        return True

    import email_service
    monkeypatch.setattr(email_service, "send_email", _fake_send)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete,
                              gesendet=gesendet, fake=_fake_send)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


async def _alarm_anlegen(db, typ, ref=""):
    import betrieb
    await betrieb.alarm(db, typ, ref=ref, fehler="Testfall")


def test_12_neuer_alarm_wird_gemeldet_und_markiert(welt):
    db = welt.db

    async def lauf():
        await _alarm_anlegen(db, "backup_fehlgeschlagen", "zeitlimit")
        await _alarm_anlegen(db, "vertrag_nach_abholung_offen", "c-1")
        n = await BM.neue_alarme_melden(db)
        offen = await db.betriebsalarme.count_documents(
            {"offen": True, "gemeldet_am": {"$exists": False}})
        # Zweiter Lauf: nichts mehr zu melden.
        n2 = await BM.neue_alarme_melden(db)
        return n, offen, n2

    n, offen, n2 = welt.run(lauf())
    assert n == 2, "beide Alarme in EINER Mail"
    assert len(welt.gesendet) == 1
    assert welt.gesendet[0]["to"] == "ahmadfkh006@gmail.com"
    assert offen == 0, "gemeldete Alarme sind markiert"
    assert n2 == 0 and len(welt.gesendet) == 1, "kein zweites Mal dieselbe Mail"


def test_13_derselbe_alarm_meldet_sich_nicht_zweimal(welt):
    """betrieb.alarm zaehlt denselben (typ, ref) hoch, statt zu duplizieren —
    daraus darf keine zweite Mail werden."""
    db = welt.db

    async def lauf():
        await _alarm_anlegen(db, "datei_nicht_loeschbar", "k-1")
        await BM.neue_alarme_melden(db)
        await _alarm_anlegen(db, "datei_nicht_loeschbar", "k-1")   # nochmal
        await _alarm_anlegen(db, "datei_nicht_loeschbar", "k-1")   # und nochmal
        n = await BM.neue_alarme_melden(db)
        doc = await db.betriebsalarme.find_one({"typ": "datei_nicht_loeschbar"})
        return n, doc

    n, doc = welt.run(lauf())
    assert n == 0, "kein zweiter Alarm, nur ein hochgezaehlter"
    assert len(welt.gesendet) == 1
    assert doc["anzahl"] == 3, "hochgezaehlt wurde trotzdem"


def test_14_scheiterter_versand_wird_wiederholt(welt, monkeypatch):
    """Wichtig: die Markierung faellt NUR nach erfolgreichem Versand."""
    db = welt.db

    async def _kaputt(*a, **k):
        return False

    async def lauf():
        import email_service
        await _alarm_anlegen(db, "migration_fehlgeschlagen", "m-1")
        monkeypatch.setattr(email_service, "send_email", _kaputt)
        n1 = await BM.neue_alarme_melden(db)
        unmarkiert = await db.betriebsalarme.count_documents(
            {"gemeldet_am": {"$exists": False}})
        monkeypatch.setattr(email_service, "send_email", welt.fake)
        n2 = await BM.neue_alarme_melden(db)
        return n1, unmarkiert, n2

    n1, unmarkiert, n2 = welt.run(lauf())
    assert n1 == 0, "gescheiterter Versand meldet nichts als erledigt"
    assert unmarkiert == 1, "der Alarm bleibt offen fuer den naechsten Versuch"
    assert n2 == 1 and len(welt.gesendet) == 1


def test_15_ohne_adresse_geht_keine_mail(welt, monkeypatch):
    db = welt.db
    monkeypatch.delenv("BETRIEB_MELDUNG_AN", raising=False)

    async def lauf():
        await _alarm_anlegen(db, "irgendwas", "x")
        return await BM.neue_alarme_melden(db), await BM.tagesbericht_senden(db)

    n, bericht = welt.run(lauf())
    assert n == 0 and bericht is False and not welt.gesendet


def test_16_tagesbericht_mit_echten_zahlen(welt):
    db = welt.db

    async def lauf():
        await _alarm_anlegen(db, "backup_fehlgeschlagen", "x")
        await db.error_logs.insert_many([
            {"id": "e1", "path": "/api/contracts", "created_at": _jetzt()},
            {"id": "e2", "path": "/api/contracts", "created_at": _jetzt()},
            {"id": "e3", "path": "/api/resale/1/photos", "created_at": _jetzt()},
            # aelter als 24 h — zaehlt nicht mit
            {"id": "alt", "path": "/api/alt",
             "created_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()},
        ])
        daten = await BM.tagesbericht_daten(db)
        ok = await BM.tagesbericht_senden(db, "20.09.2026")
        return daten, ok

    daten, ok = welt.run(lauf())
    assert daten["alarme_offen"] == 1
    assert daten["fehler"] == 3, "nur die letzten 24 Stunden"
    assert daten["fehler_wege"][0] == {"weg": "/api/contracts", "anzahl": 2}
    assert ok is True and len(welt.gesendet) == 1
    assert "Auffaelligkeiten" in welt.gesendet[0]["betreff"]
    assert welt.gesendet[0]["key"] == "tagesbericht-20.09.2026"


# ------------------------------------------------------------ Aufbau
def test_17_nur_ein_prozess_verschickt():
    """Zwei Server x vier Prozesse — ohne Sperre kaeme jede Meldung achtmal."""
    q = inspect.getsource(BM.run_betriebsmeldung_forever)
    assert 'acquire(db, "betriebsmeldung"' in q
    assert 'acquire(db, f"tagesbericht-{tag}"' in q
    assert "ttl_seconds=20 * 3600" in q, "Tagessperre wie bei der Sicherung"


def test_18_ohne_adresse_beendet_sich_der_dienst_NICHT():
    """Der Stolperstein beim Bauen: /api/ready wertet einen beendeten
    Hintergrundjob als FEHLER — der Server flaege aus dem Lastverteiler,
    nur weil niemand Meldungen haben will."""
    # Nur ausgefuehrte Zeilen — der Kommentar nennt das alte "return"
    # absichtlich beim Namen, damit der Grund nicht verlorengeht.
    q = "\n".join(z.split("#", 1)[0]
                  for z in inspect.getsource(BM.run_betriebsmeldung_forever).splitlines())
    stelle = q.split("if not empfaenger():")[1][:700]
    assert "return" not in stelle.split("while True:")[0], \
        "kein return — sonst 503"
    assert "while True:" in stelle
    import server
    s = inspect.getsource(server)
    assert "if empfaenger():" in s, \
        "server.py startet den Dienst ohne Adresse gar nicht erst"


def test_19_die_einstellungen_erreichen_den_container():
    from tests.test_haertung_20260919 import _compose_umgebung
    umgebung = _compose_umgebung()
    for name in ("BETRIEB_MELDUNG_AN", "BETRIEB_MELDUNG_SOFORT_MIN",
                 "BETRIEB_TAGESBERICHT_STUNDE"):
        assert name in umgebung, f"{name} erreicht den Container nicht"
    beispiel = (BACKEND.parent / ".env.example").read_text(encoding="utf-8")
    assert "BETRIEB_MELDUNG_AN=" in beispiel


def test_20_eine_meldung_stoert_den_betrieb_nie():
    """Alle Einstiegspunkte fangen ab — ein Mailproblem darf die Plattform
    nicht anhalten."""
    for funktion in (BM.neue_alarme_melden, BM.tagesbericht_senden):
        q = inspect.getsource(funktion)
        assert "except Exception:" in q and "log.exception" in q, funktion.__name__


def test_21_zeitpunkte_sind_lesbar():
    """Der Rohwert 2026-09-20T11:40:37.754523+00:00 ist fuer einen Bericht
    unbrauchbar — abgeschnitten ("...11:40:37.7545…") erst recht."""
    assert BM._zeitpunkt("2026-09-20T11:40:37.754523+00:00").endswith(("11:40", "13:40"))
    assert len(BM._zeitpunkt("2026-09-20T11:40:37.754523+00:00")) == 16
    # Kaputte Werte duerfen die Mail nicht sprengen:
    assert BM._zeitpunkt("kaputt") == "kaputt"
    assert BM._zeitpunkt(None) == ""
    _b, text = BM.alarm_text([{"typ": "x", "ref": "", "details": {}, "anzahl": 1,
                               "created_at": "2026-09-20T11:40:37.754523+00:00"}], 1)
    assert "…" not in text.split("seit:")[1].split("\n")[0]
