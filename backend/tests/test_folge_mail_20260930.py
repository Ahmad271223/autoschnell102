# -*- coding: utf-8 -*-
"""Pruefliste 30.09.2026 (Folge-Mail Nr. 1-3): der Inhalt gehoert zum Versand-Schluessel.

  * derselbe Schluessel mit anderem Empfaenger/Text -> 409 (vorher: "bereits verschickt")
  * ohne Schluessel: aus dem Inhalt abgeleitet — dieselbe Mail ist EIN Versand, eine geaenderte ein neuer;
    der Anbieter-Schluessel traegt den Inhalt (vorher folge-<vertrag>-<art>: die zweite Mail mit anderem
    Empfaenger galt beim Anbieter als Wiederholung und wurde als "versendet" gemeldet, ohne rauszugehen)
  * die Vertragsfassung steckt im Inhalt: nach einer Neuerzeugung ist derselbe Text ein neuer Versand
"""
from test_rp_vertrag_20260922 import _erwarte, _modul, _vertrag, welt  # noqa: F401


def test_folge_mail_inhalt_gehoert_zum_schluessel(welt, monkeypatch):  # noqa: F811
    C = _modul("routes.contracts")
    w = welt
    import email_service
    import provider_fetch
    monkeypatch.setattr(provider_fetch, "MOCK_PROVIDER_FETCH", False)
    monkeypatch.setattr(email_service, "email_configured", lambda: True)
    raus = []

    async def _mit_beleg(to, subject, text, **kw):
        raus.append({"to": to, "text": text, "key": kw.get("idempotency_key")})
        return True, f"resend:{len(raus)}"
    monkeypatch.setattr(email_service, "send_email_mit_beleg", _mit_beleg)
    cid = f"cfm_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a)))

    def senden(**kw):
        return C.folge_mail_senden(cid, C.FolgeMailIn(art="nach_kauf", **kw), w.a)

    # --- mit Schluessel
    k = f"fm30-{w.s}"
    out = w.run(senden(recipient="kunde@rpv.test", message="Hallo {kunde_name}, danke.", idempotency_key=k))
    assert out["zustellung"] == "versendet" and len(raus) == 1
    # Wiederholung mit demselben Inhalt: ein Versand
    out = w.run(senden(recipient="kunde@rpv.test", message="Hallo {kunde_name}, danke.", idempotency_key=k))
    assert out["bereits_gesendet"] is True and len(raus) == 1
    # derselbe Schluessel, anderer Empfaenger bzw. anderer Text: 409, nichts geht raus
    e = w.run(_erwarte(409, senden(recipient="anderer@rpv.test", message="Hallo {kunde_name}, danke.", idempotency_key=k)))
    assert "anderen Nachricht" in e.detail
    w.run(_erwarte(409, senden(recipient="kunde@rpv.test", message="Ganz anderer Text.", idempotency_key=k)))
    assert len(raus) == 1

    # --- ohne Schluessel: aus dem Inhalt abgeleitet
    out = w.run(senden(recipient="kunde@rpv.test", message="Zweite Nachricht."))
    assert out["zustellung"] == "versendet" and len(raus) == 2
    out = w.run(senden(recipient="kunde@rpv.test", message="Zweite Nachricht."))          # Doppelklick
    assert out["bereits_gesendet"] is True and len(raus) == 2
    out = w.run(senden(recipient="zweiter@rpv.test", message="Zweite Nachricht."))        # anderer Empfaenger
    assert out.get("bereits_gesendet") is not True and out["zustellung"] == "versendet" and len(raus) == 3
    out = w.run(senden(recipient="kunde@rpv.test", message="Dritte Nachricht."))          # anderer Text
    assert out.get("bereits_gesendet") is not True and len(raus) == 4
    # jeder echte Versand hat beim Anbieter seinen eigenen Schluessel — nie mehr nur folge-<vertrag>-<art>
    schluessel = [r["key"] for r in raus]
    assert len(set(schluessel)) == 4 and all(s.startswith(f"folge-{cid}-") for s in schluessel)
    assert f"folge-{cid}-nach_kauf" not in schluessel
    # am Vertrag steht je Versand ein Eintrag mit Inhalts-Hash und Fassung
    status = w.run(w.db.generated_pdfs.find_one({"id": cid}, {"_id": 0, "send_status": 1}))["send_status"]
    assert len(status) == 4 and all(e["anfrage_hash"] and e["version"] == 1 and e["zustellung"] == "versendet" for e in status)
    assert sum(1 for e in status if e["idempotency_key"].startswith("auto-")) == 3

    # --- neue Vertragsfassung: derselbe Text ist ein neuer Versand
    w.run(w.db.generated_pdfs.update_one({"id": cid}, {"$set": {"version": 2}}))
    out = w.run(senden(recipient="kunde@rpv.test", message="Zweite Nachricht."))
    assert out.get("bereits_gesendet") is not True and len(raus) == 5
