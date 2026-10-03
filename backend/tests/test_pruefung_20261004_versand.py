# -*- coding: utf-8 -*-
"""Pruefung 04.10.2026 — Vertragsversand per E-Mail nie doppelt.

  Nr. 21  SMTP: nach Beginn der Uebergabe gibt nur eine AUSDRUECKLICHE Ablehnung
          den Schluessel frei; eine gescheiterte Verabschiedung (QUIT) nach der
          Abgabe gilt als abgegeben (vorher: SMTPException erbt von OSError ->
          "sicher abgelehnt" -> zweite Zustellung beim naechsten Versuch).
  Nr. 22  Unklarer Ausgang heisst "unklar", nicht "fehlgeschlagen"; der Eintrag
          bleibt stehen und wird unter DEMSELBEN Schluessel wieder aufgenommen
          (auch nach dem Neuladen der Seite). Derselbe Vertrag an dieselbe
          Adresse ein zweites Mal nur mit erneut=true. Resend 409 (Schluessel-
          Konflikt) ist unklar, kein SMTP-Rueckfall.
  Nr. 13  Datenbankfehler beim Vermerk nach erfolgtem Versand -> kein 500.
  Nr. 14  Ergebnis der Belegkopie im Versandverlauf.

Ohne Server: reine Funktionen und In-Prozess-Aufrufe gegen eine Wegwerf-DB.
"""
import asyncio
import smtplib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import email_service as E  # noqa: E402
from test_befunde_runde21_mail import _Antwort, _fake_httpx, _senden, ohne_warten  # noqa: E402,F401
from test_rp_vertrag_20260922 import _erwarte, _jetzt, _modul, _vertrag, welt  # noqa: E402,F401

LAEUFT = {"uebergabe_laeuft": True}


# ------------------------------------------------------------------ Nr. 21
def test_21_nach_uebergabebeginn_gibt_nur_ausdrueckliche_ablehnung_frei():
    # vor der Uebergabe: alles sicher (nichts abgegeben)
    assert E._sicher_nicht_zugestellt(OSError("keine Verbindung"), {}) is True
    assert E._sicher_nicht_zugestellt(TimeoutError(), {}) is True
    # ausdrueckliche Ablehnung waehrend der Uebergabe
    assert E._sicher_nicht_zugestellt(smtplib.SMTPRecipientsRefused({}), LAEUFT) is True
    assert E._sicher_nicht_zugestellt(smtplib.SMTPSenderRefused(550, b"nein", "a@b"), LAEUFT) is True
    assert E._sicher_nicht_zugestellt(smtplib.SMTPDataError(554, b"Spam"), LAEUFT) is True
    # alles andere waehrend der Uebergabe ist UNKLAR
    for fehler in (TimeoutError(), OSError("reset"), ConnectionResetError(),
                   smtplib.SMTPServerDisconnected("weg"),
                   smtplib.SMTPResponseException(421, b"bye"), RuntimeError("?")):
        assert E._sicher_nicht_zugestellt(fehler, LAEUFT) is False, fehler
    # nach der Abgabe: nie "nicht zugestellt"
    assert E._sicher_nicht_zugestellt(smtplib.SMTPDataError(554, b"x"),
                                      {"uebergabe_laeuft": True, "fertig": True}) is False


class _FakeSmtp:
    gesendet: list = []
    quit_fehler = None
    send_fehler = None

    def __init__(self, *a, **k):
        pass

    def starttls(self, **k):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        if _FakeSmtp.send_fehler:
            raise _FakeSmtp.send_fehler
        _FakeSmtp.gesendet.append(msg["To"])

    def quit(self):
        if _FakeSmtp.quit_fehler:
            raise _FakeSmtp.quit_fehler

    def close(self):
        pass


def _sync_argumente():
    return dict(to="v@example.test", subject="B", text="T", html=None, anhang=None, anhang_name="",
                reply_to=[], kopie=[], absender_name="Firma")


def test_21_gescheiterte_verabschiedung_nach_abgabe_ist_kein_fehler(monkeypatch):
    monkeypatch.setattr(E.smtplib, "SMTP", _FakeSmtp)
    monkeypatch.setattr(E, "SMTP_PORT", 587)
    _FakeSmtp.gesendet = []
    _FakeSmtp.send_fehler = None
    _FakeSmtp.quit_fehler = smtplib.SMTPResponseException(451, b"QUIT kaputt")
    fortschritt = {}
    E._send_sync(**_sync_argumente(), fortschritt=fortschritt)       # wirft NICHT
    assert fortschritt == {"uebergabe_laeuft": True, "fertig": True}
    assert _FakeSmtp.gesendet == ["v@example.test"]


def test_21_abriss_waehrend_der_abgabe_wird_unklar_gemeldet(monkeypatch):
    monkeypatch.setattr(E.smtplib, "SMTP", _FakeSmtp)
    monkeypatch.setattr(E, "SMTP_PORT", 587)
    monkeypatch.setattr(E, "email_configured", lambda: True)
    monkeypatch.setattr(E, "resend_aktiv", lambda: False)
    monkeypatch.setattr(E, "smtp_aktiv", lambda: True)
    _FakeSmtp.quit_fehler = None
    _FakeSmtp.send_fehler = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
    ok, beleg = asyncio.run(E.send_email_mit_beleg("v@example.test", "B", "T"))
    assert (ok, beleg) == (False, E.BELEG_UNKLAR)
    # ausdrueckliche Ablehnung bleibt "fehlgeschlagen"
    _FakeSmtp.send_fehler = smtplib.SMTPDataError(554, b"abgelehnt")
    ok, beleg = asyncio.run(E.send_email_mit_beleg("v@example.test", "B", "T"))
    assert (ok, beleg) == (False, "")
    _FakeSmtp.send_fehler = None


def test_21_der_alte_leere_test_prueft_jetzt_wirklich():
    q = Path(__file__).with_name("test_betriebssicherheit_20260920.py").read_text(encoding="utf-8")
    assert "is False or True" not in q


# ------------------------------------------------------------------ Nr. 22 (Dienst)
def _resend_mit(monkeypatch, fehler):
    gesendet = []
    monkeypatch.setattr(E, "email_configured", lambda: True)
    monkeypatch.setattr(E, "resend_aktiv", lambda: True)
    monkeypatch.setattr(E, "smtp_aktiv", lambda: True)
    monkeypatch.setattr(E, "_send_sync", lambda **k: gesendet.append(k["to"]))

    async def _resend(**k):
        raise fehler
    monkeypatch.setattr(E, "_send_resend", _resend)
    return gesendet


def test_22_resend_unklar_und_abgerissene_antwort_heissen_unklar(monkeypatch):
    import httpx
    for fehler in (E.ResendUnklar("Resend HTTP 503"), httpx.ReadTimeout("zu lange"),
                   httpx.RemoteProtocolError("abgerissen")):
        gesendet = _resend_mit(monkeypatch, fehler)
        ok, beleg = asyncio.run(E.send_email_mit_beleg("v@example.test", "B", "T",
                                                       idempotency_key="k22"))
        assert (ok, beleg) == (False, E.BELEG_UNKLAR), fehler
        assert gesendet == [], "nie ueber SMTP nachsenden"


def test_22_resend_nicht_erreichbar_ist_kein_unklar(monkeypatch):
    import httpx
    gesendet = _resend_mit(monkeypatch, httpx.ConnectError("keine Verbindung"))
    ok, beleg = asyncio.run(E.send_email_mit_beleg("v@example.test", "B", "T",
                                                   idempotency_key="k22b"))
    assert (ok, beleg) == (False, "") and gesendet == []


def test_22_resend_409_ist_unklar_gleichzeitig_wird_abgewartet(monkeypatch, ohne_warten):
    aufrufe = []
    _fake_httpx(monkeypatch, [_Antwort(409, {"name": "invalid_idempotent_request"})], aufrufe)
    with pytest.raises(E.ResendUnklar):
        _senden(idempotency_key="k409")
    assert len(aufrufe) == 1
    # die erste Abgabe laeuft noch -> warten, dann kommt ihre Antwort
    aufrufe = []
    _fake_httpx(monkeypatch, [_Antwort(409, {"name": "concurrent_idempotent_requests"}),
                              _Antwort(200, {"id": "erste"})], aufrufe)
    assert _senden(idempotency_key="k409b") == "erste"
    assert aufrufe == ["k409b", "k409b"]


# ------------------------------------------------------------------ Nr. 22 (Route)
def _mail_attrappe(monkeypatch, ergebnisse):
    import email_service
    import provider_fetch
    monkeypatch.setattr(provider_fetch, "MOCK_PROVIDER_FETCH", False)
    monkeypatch.setattr(email_service, "email_configured", lambda: True)
    schluessel = []

    async def _mit_beleg(to, subject, text, anhang=None, anhang_name="", **kw):
        schluessel.append(kw.get("idempotency_key"))
        return ergebnisse.pop(0) if ergebnisse else (True, "resend:ok")

    async def _kopie(*a, **k):
        return False
    monkeypatch.setattr(email_service, "send_email_mit_beleg", _mit_beleg)
    monkeypatch.setattr(email_service, "send_email", _kopie)
    return schluessel


def _mail(C, w, key, **kw):
    return C.SendIn(**{"channel": "email", "recipient": "kunde@pv.test", "subject": "Vertrag",
                       "message": "Hier der Vertrag.", "idempotency_key": key, **kw})


def _status(w, cid):
    return w.run(w.db.generated_pdfs.find_one({"id": cid}))["send_status"]


def test_22_zweiter_versand_nach_dem_neuladen_nur_mit_bestaetigung(welt, monkeypatch):
    C = _modul("routes.contracts")
    w = welt
    schluessel = _mail_attrappe(monkeypatch, [])
    cid = f"cv22_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a)))
    out = w.run(C.send_contract(cid, _mail(C, w, f"erst-{w.s}"), w.a))
    assert out["zustellung"] == "versendet"
    # Seite neu geladen -> neuer Schluessel, gleiche Adresse (Gross/Klein egal)
    e = w.run(_erwarte(409, C.send_contract(
        cid, _mail(C, w, f"reload-{w.s}", recipient="Kunde@PV.test"), w.a)))
    assert e.detail["code"] == "bereits_versendet" and "noch einmal" in e.detail["msg"]
    assert len(schluessel) == 1 and len(_status(w, cid)) == 1, "nichts reserviert, nichts gesendet"
    # bewusst noch einmal
    out = w.run(C.send_contract(cid, _mail(C, w, f"reload-{w.s}", erneut=True), w.a))
    assert out["zustellung"] == "versendet" and len(schluessel) == 2
    assert _status(w, cid)[-1]["erneut"] is True
    # andere Adresse: keine Rueckfrage
    out = w.run(C.send_contract(cid, _mail(C, w, f"anders-{w.s}", recipient="b@pv.test"), w.a))
    assert out["zustellung"] == "versendet"
    # Nr. 14: das Ergebnis der Belegkopie steht im Verlauf
    assert _status(w, cid)[-1]["kopie"] == "fehlgeschlagen"


def test_22_neue_fassung_darf_ohne_rueckfrage(welt, monkeypatch):
    C = _modul("routes.contracts")
    w = welt
    _mail_attrappe(monkeypatch, [])
    cid = f"cv22f_{w.s}"
    alt = {"idempotency_key": "alt", "channel": "email", "recipient": "kunde@pv.test",
           "sent_at": _jetzt(-3600), "zustellung": "versendet", "anfrage_hash": "x", "version": 1}
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a, version=2, send_status=[alt])))
    out = w.run(C.send_contract(cid, _mail(C, w, f"neu-{w.s}"), w.a))
    assert out["zustellung"] == "versendet"


def test_22_unklar_bleibt_stehen_und_wird_unter_demselben_schluessel_wiederholt(welt, monkeypatch):
    C = _modul("routes.contracts")
    w = welt
    import email_service
    schluessel = _mail_attrappe(monkeypatch, [(False, email_service.BELEG_UNKLAR)])
    cid = f"cv22u_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a)))
    e = w.run(_erwarte(502, C.send_contract(cid, _mail(C, w, f"erst-{w.s}"), w.a)))
    assert e.detail["code"] == "versand_unklar" and "nicht doppelt" in e.detail["msg"]
    eintraege = _status(w, cid)
    assert len(eintraege) == 1 and eintraege[0]["zustellung"] == "unklar", "Eintrag bleibt"
    # Neu laden, gleicher Inhalt: Wiederaufnahme unter dem ERSTEN Schluessel
    out = w.run(C.send_contract(cid, _mail(C, w, f"reload-{w.s}"), w.a))
    assert out["zustellung"] == "versendet"
    assert schluessel == [f"vertrag-{cid}-erst-{w.s}"] * 2, "Resend erkennt den Schluessel"
    assert [x["zustellung"] for x in _status(w, cid)] == ["versendet"]


def test_22_unklar_mit_anderem_text_nur_mit_bestaetigung(welt, monkeypatch):
    C = _modul("routes.contracts")
    w = welt
    schluessel = _mail_attrappe(monkeypatch, [])
    cid = f"cv22a_{w.s}"
    alt = {"idempotency_key": "alt", "channel": "email", "recipient": "kunde@pv.test",
           "sent_at": _jetzt(-600), "zustellung": "unklar", "anfrage_hash": "anderer-text",
           "version": 1}
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a, send_status=[alt])))
    e = w.run(_erwarte(409, C.send_contract(cid, _mail(C, w, f"neu-{w.s}"), w.a)))
    assert e.detail["code"] == "frueherer_versand_unklar" and schluessel == []
    out = w.run(C.send_contract(cid, _mail(C, w, f"neu-{w.s}", erneut=True), w.a))
    assert out["zustellung"] == "versendet" and out.get("frueherer_versand_unklar") is True
    assert {x["idempotency_key"]: x["zustellung"] for x in _status(w, cid)} == {
        "alt": "abgeloest", f"neu-{w.s}": "versendet"}


def test_22_unklar_aelter_als_ein_tag_nur_mit_bestaetigung(welt, monkeypatch):
    C = _modul("routes.contracts")
    w = welt
    schluessel = _mail_attrappe(monkeypatch, [])
    cid = f"cv22t_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a)))
    body = _mail(C, w, f"erst-{w.s}")
    alt = {"idempotency_key": f"erst-{w.s}", "channel": "email", "recipient": "kunde@pv.test",
           "subject": "Vertrag", "sent_at": _jetzt(-25 * 3600), "zustellung": "unklar",
           "anfrage_hash": C._versand_anfrage_hash({"version": 1}, body), "version": 1}
    w.run(w.db.generated_pdfs.update_one({"id": cid}, {"$set": {"send_status": [alt]}}))
    e = w.run(_erwarte(409, C.send_contract(cid, _mail(C, w, f"reload-{w.s}"), w.a)))
    assert e.detail["code"] == "frueherer_versand_unklar" and schluessel == []
    out = w.run(C.send_contract(cid, _mail(C, w, f"reload-{w.s}", erneut=True), w.a))
    assert out["zustellung"] == "versendet"


def test_13_vermerk_scheitert_an_der_datenbank_kein_500(welt, monkeypatch):
    C = _modul("routes.contracts")
    w = welt
    _mail_attrappe(monkeypatch, [])
    cid = f"cv13_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a)))

    def _kaputt(*a, **k):
        raise RuntimeError("Datenbank weg")
    monkeypatch.setattr(C, "_abschluss", _kaputt)
    out = w.run(C.send_contract(cid, _mail(C, w, f"k13-{w.s}"), w.a))
    assert out["zustellung"] == "versendet" and out["status_vermerk"] == "nicht_gespeichert"
    # der reservierte Eintrag bleibt "laeuft" -> spaeter Wiederaufnahme unter demselben Schluessel
    assert [x["zustellung"] for x in _status(w, cid)] == ["laeuft"]
