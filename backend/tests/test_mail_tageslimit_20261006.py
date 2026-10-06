# -*- coding: utf-8 -*-
"""Wunsch Ahmad 06.10.2026: "die SaaS muss jederzeit hunderte Mails rausschicken koennen — ohne Limit je
Stunde oder Minute; je Nutzer vielleicht 100 E-Mails am Tag".

  * keine Bremse mehr je 10 Minuten (vorher 300 je 10 Minuten, VERSAND_JE_KONTO_10MIN)
  * hoechstens VERSAND_JE_KONTO_TAG (Standard 100) E-Mails je Konto und Tag, deutsche Zeit —
    Vertragsversand und Folge-Mail zusammen
  * gezaehlt wird nur, was wirklich an den Mail-Dienst geht: Wiederholung desselben Versands,
    gescheiterter Versand und WhatsApp zaehlen nicht; ein unklarer Ausgang zaehlt (die Mail ist
    vielleicht raus)
  * ueber dem Limit: 429, nichts reserviert, nichts gesendet
"""
import inspect

from test_pruefung_20261004_versand import _mail, _mail_attrappe, _status  # noqa: F401
from test_rp_vertrag_20260922 import _erwarte, _modul, _vertrag, welt  # noqa: F401


def _zaehler(w, user):
    from provider_fetch import tagesschluessel
    doc = w.run(w.db.provider_budget.find_one({"_id": f"{tagesschluessel()}:mail:{user['id']}"}))
    return int((doc or {}).get("n") or 0)


def _neuer_vertrag(w, user, nr):
    cid = f"cml{nr}_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, user)))
    return cid


def test_01_keine_minuten_oder_stundenbremse_mehr():
    C = _modul("routes.contracts")
    assert not hasattr(C, "_versand_limiter") and not hasattr(C, "VERSAND_JE_KONTO_10MIN")
    assert C.VERSAND_JE_KONTO_TAG == 100
    import ast
    import textwrap
    # ohne Kommentare (ast.unparse): im Code steht keine Bremse je Minute/10 Minuten mehr
    q = "".join(ast.unparse(ast.parse(textwrap.dedent(inspect.getsource(f))))
                for f in (C.send_contract, C.folge_mail_senden))
    assert "_limiter.check(" not in q and "10 Minuten" not in q
    assert q.count("await _mail_tag_zaehlen(user)") == 2
    assert "Tageslimit" in C._mail_tageslimit_text() and "0 Uhr" in C._mail_tageslimit_text()


def test_02_tageslimit_je_konto(welt, monkeypatch):  # noqa: F811
    C = _modul("routes.contracts")
    w = welt
    monkeypatch.setattr(C, "VERSAND_JE_KONTO_TAG", 3)
    schluessel = _mail_attrappe(monkeypatch, [])
    for i in range(3):
        out = w.run(C.send_contract(_neuer_vertrag(w, w.a, i), _mail(C, w, f"k{i}-{w.s}"), w.a))
        assert out["zustellung"] == "versendet"
    assert _zaehler(w, w.a) == 3 and len(schluessel) == 3
    # die vierte E-Mail: 429, nichts reserviert, nichts gesendet, Zaehler bleibt bei 3
    cid = _neuer_vertrag(w, w.a, 3)
    e = w.run(_erwarte(429, C.send_contract(cid, _mail(C, w, f"k3-{w.s}"), w.a)))
    assert "Tageslimit" in e.detail and "3 E-Mails" in e.detail
    assert _status(w, cid) == [] and len(schluessel) == 3 and _zaehler(w, w.a) == 3
    # ein anderes Konto hat seinen eigenen Topf
    out = w.run(C.send_contract(_neuer_vertrag(w, w.b, 9), _mail(C, w, f"kb-{w.s}"), w.b))
    assert out["zustellung"] == "versendet" and _zaehler(w, w.b) == 1


def test_03_wiederholung_und_fehlschlag_zaehlen_nicht_unklar_schon(welt, monkeypatch):  # noqa: F811
    C = _modul("routes.contracts")
    w = welt
    import email_service
    monkeypatch.setattr(C, "VERSAND_JE_KONTO_TAG", 2)
    _mail_attrappe(monkeypatch, [(False, ""), (True, "resend:ok"), (False, email_service.BELEG_UNKLAR)])
    # 1) gescheitert (sicher nicht zugestellt) -> zaehlt nicht
    c1 = _neuer_vertrag(w, w.a, 1)
    w.run(_erwarte(502, C.send_contract(c1, _mail(C, w, f"f-{w.s}"), w.a)))
    assert _zaehler(w, w.a) == 0
    # 2) versendet -> 1; dieselbe Anfrage noch einmal (Doppelklick) -> zaehlt nicht
    out = w.run(C.send_contract(c1, _mail(C, w, f"f-{w.s}"), w.a))
    assert out["zustellung"] == "versendet" and _zaehler(w, w.a) == 1
    out = w.run(C.send_contract(c1, _mail(C, w, f"f-{w.s}"), w.a))
    assert out.get("bereits_gesendet") is True and _zaehler(w, w.a) == 1
    # 3) unklar -> zaehlt (die Mail ist vielleicht raus); damit ist das Limit 2 erreicht
    c2 = _neuer_vertrag(w, w.a, 2)
    w.run(_erwarte(502, C.send_contract(c2, _mail(C, w, f"u-{w.s}"), w.a)))
    assert _zaehler(w, w.a) == 2
    # die Wiederaufnahme desselben unklaren Versands zaehlt nicht noch einmal — und ist erlaubt
    out = w.run(C.send_contract(c2, _mail(C, w, f"u2-{w.s}"), w.a))
    assert out["zustellung"] == "versendet" and _zaehler(w, w.a) == 2
    # eine NEUE Mail ist ueber dem Limit
    w.run(_erwarte(429, C.send_contract(_neuer_vertrag(w, w.a, 3), _mail(C, w, f"n-{w.s}"), w.a)))


def test_04_whatsapp_zaehlt_nicht(welt, monkeypatch):  # noqa: F811
    C = _modul("routes.contracts")
    w = welt
    monkeypatch.setattr(C, "VERSAND_JE_KONTO_TAG", 1)
    _mail_attrappe(monkeypatch, [])
    w.run(C.send_contract(_neuer_vertrag(w, w.a, 1), _mail(C, w, f"m-{w.s}"), w.a))
    assert _zaehler(w, w.a) == 1
    wa = C.SendIn(channel="whatsapp", recipient="0170 1234567", message="Hier der Vertrag.",
                  idempotency_key=f"wa-{w.s}")
    out = w.run(C.send_contract(_neuer_vertrag(w, w.a, 2), wa, w.a))
    assert out["status"] == "ok" and _zaehler(w, w.a) == 1


def test_05_folge_mail_zaehlt_mit(welt, monkeypatch):  # noqa: F811
    C = _modul("routes.contracts")
    w = welt
    monkeypatch.setattr(C, "VERSAND_JE_KONTO_TAG", 2)
    _mail_attrappe(monkeypatch, [(True, "resend:1"), (False, ""), (True, "resend:2")])
    cid = _neuer_vertrag(w, w.a, 1)
    w.run(C.send_contract(cid, _mail(C, w, f"v-{w.s}"), w.a))
    body = C.FolgeMailIn(art="nach_kauf", recipient="kunde@rpv.test", idempotency_key=f"fm-{w.s}")
    # gescheiterte Folge-Mail gibt ihren Platz zurueck
    w.run(_erwarte(502, C.folge_mail_senden(cid, body, w.a)))
    assert _zaehler(w, w.a) == 1
    out = w.run(C.folge_mail_senden(cid, body, w.a))
    assert out["zustellung"] == "versendet" and _zaehler(w, w.a) == 2
    # Limit erreicht: die naechste Folge-Mail -> 429, Reservierung wieder weg
    andere = C.FolgeMailIn(art="nach_kauf", recipient="zweiter@rpv.test", idempotency_key=f"fm2-{w.s}")
    e = w.run(_erwarte(429, C.folge_mail_senden(cid, andere, w.a)))
    assert "Tageslimit" in e.detail
    keys = [x.get("idempotency_key") for x in _status(w, cid)]
    assert f"fm2-{w.s}" not in keys and _zaehler(w, w.a) == 2


def test_06_mock_versand_und_limit_null_zaehlen_nicht(welt, monkeypatch):  # noqa: F811
    C = _modul("routes.contracts")
    w = welt
    import provider_fetch
    monkeypatch.setattr(provider_fetch, "MOCK_PROVIDER_FETCH", True)
    assert w.run(C._mail_tag_zaehlen(w.a)) is None and _zaehler(w, w.a) == 0
    monkeypatch.setattr(provider_fetch, "MOCK_PROVIDER_FETCH", False)
    monkeypatch.setattr(C, "VERSAND_JE_KONTO_TAG", 0)
    assert w.run(C._mail_tag_zaehlen(w.a)) is None and _zaehler(w, w.a) == 0
    # mit Limit: ein Zaehler je Konto und Tag (deutsche Zeit), laeuft nach zwei Tagen ab (TTL)
    monkeypatch.setattr(C, "VERSAND_JE_KONTO_TAG", 5)
    s = w.run(C._mail_tag_zaehlen(w.a))
    from provider_fetch import tagesschluessel
    assert s == f"{tagesschluessel()}:mail:{w.a['id']}" and _zaehler(w, w.a) == 1
    assert w.run(w.db.provider_budget.find_one({"_id": s}))["ablauf"]
    w.run(C._mail_tag_zurueck(s))
    w.run(C._mail_tag_zurueck(s))                       # nie unter 0
    assert _zaehler(w, w.a) == 0


# ------------------------------------------------------------------ gemeinsamer Takt (alle Prozesse)
def _takt_db(monkeypatch):
    import uuid

    import deps
    from motor.motor_asyncio import AsyncIOMotorClient
    from test_rp_vertrag_20260922 import MONGO_URL
    name = f"autoschnell_takt_{uuid.uuid4().hex[:8]}"

    async def _mit(coro_fabrik):
        client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        monkeypatch.setattr(deps, "db", client[name])
        try:
            return await coro_fabrik()
        finally:
            await client.drop_database(name)
            client.close()
    return _mit


def test_07_ein_prozess_bekommt_das_ganze_konto_limit(monkeypatch):
    """Vorher: 1,25 Mails je Sekunde je Prozess (10 / 8) — 10 Mails aus einem Prozess brauchten 8 s.
    Jetzt teilen sich alle Prozesse EINEN Zaehler: 10 Mails bei 20/s brauchen ~0,5 s."""
    import asyncio

    import email_service as E
    mit_db = _takt_db(monkeypatch)

    async def lauf():
        takt = E._GemeinsamerTakt(20.0, E._Takt(20.0 / 8))
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await asyncio.gather(*[takt.platz() for _ in range(10)])
        return loop.time() - t0
    dauer = asyncio.run(mit_db(lauf))
    assert 0.35 <= dauer <= 1.5, f"10 Plaetze bei 20/s dauerten {dauer:.2f}s (Anteil je Prozess waeren 4 s)"


def test_08_zwei_prozesse_zusammen_nie_schneller_als_das_konto(monkeypatch):
    import asyncio

    import email_service as E
    mit_db = _takt_db(monkeypatch)

    async def lauf():
        a = E._GemeinsamerTakt(20.0, E._Takt(10.0))
        b = E._GemeinsamerTakt(20.0, E._Takt(10.0))     # zweiter "Prozess", derselbe Zaehler
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await asyncio.gather(*[t.platz() for t in (a, b) for _ in range(10)])
        return loop.time() - t0
    dauer = asyncio.run(mit_db(lauf))
    assert dauer >= 0.85, f"20 Plaetze bei 20/s gesamt in {dauer:.2f}s — schneller als das Konto erlaubt"


def test_09_datenbank_weg_dann_anteil_je_prozess(monkeypatch):
    import asyncio

    import email_service as E
    rueckfall = []

    class _Rueckfall:
        abstand = 0.8

        async def platz(self):
            rueckfall.append(1)

    takt = E._GemeinsamerTakt(10.0, _Rueckfall())

    async def _kaputt():
        raise RuntimeError("Datenbank weg")
    monkeypatch.setattr(takt, "_platz_holen", _kaputt)
    asyncio.run(takt.platz())
    asyncio.run(takt.platz())
    assert rueckfall == [1, 1]
    # abschaltbar: dann nur der Anteil je Prozess
    monkeypatch.setattr(E, "RESEND_TAKT_GEMEINSAM", False)

    async def bauen():
        E._resend_takt = None
        E._resend_takt_loop = None
        return E._takt()
    assert isinstance(asyncio.run(bauen()), E._Takt)


def test_10_verstellte_uhr_blockiert_nicht(monkeypatch):
    import asyncio
    import time

    import deps
    import email_service as E
    mit_db = _takt_db(monkeypatch)

    async def lauf():
        await deps.db.mail_takt.insert_one({"_id": "resend", "naechste": time.time() + 86400})
        takt = E._GemeinsamerTakt(10.0, E._Takt(1.25))
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await takt.platz()
        return loop.time() - t0
    assert asyncio.run(mit_db(lauf)) < 1.0
