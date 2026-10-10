# -*- coding: utf-8 -*-
"""Wunsch Ahmad 02.10.2026:
  * Absender der E-Mails ist NUR der Firmenname (kein "über AutoSchnell" mehr) — email_service._absender.
  * Farbe der App je Konto: PUT /auth/akzentfarbe (Chef/Sucher/Betreiber), PUT /driver/me {akzentfarbe}
    (Fahrer); erlaubt sind konfig.AKZENTFARBEN, /auth/me bzw. /driver/me liefern den Wert mit."""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_befunde_runde17_termine import _jetzt, _module, welt  # noqa: E402,F401

FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"


def test_01_absender_nur_firma(monkeypatch):
    ES = _module("email_service")
    monkeypatch.setattr(ES, "MAIL_ABSENDER_NAME", "AutoSchnell", raising=False)
    monkeypatch.setattr(ES, "MAIL_FROM", "AutoSchnell <vertrag@autoschnell.de>", raising=False)
    assert ES._absender("Autohaus Muster", kodiert=False) == "Autohaus Muster <vertrag@autoschnell.de>"
    assert "über" not in ES._absender("Autohaus Muster")
    assert ES._absender("", kodiert=False) == "AutoSchnell <vertrag@autoschnell.de>", "ohne Firma: die Marke"
    assert ES._absender(None, kodiert=False) == "AutoSchnell <vertrag@autoschnell.de>"


def test_02_farben_liste_und_pruefung():
    K = _module("konfig")
    assert K.AKZENTFARBEN[0] == "standard" and len(K.AKZENTFARBEN) == 11, "Standard + zehn Farben"
    for f in ("rot", "lila", "gruen", "blau", "schwarz", "orange", "petrol", "pink", "gold", "indigo"):
        assert f in K.AKZENTFARBEN
    assert K.akzentfarbe_pruefen(" Lila ") == "lila"
    with pytest.raises(ValueError):
        K.akzentfarbe_pruefen("neon")
    # dieselben Schluessel in der Oberflaeche
    js = (FRONTEND / "lib" / "akzent.js").read_text(encoding="utf-8")
    for f in K.AKZENTFARBEN:
        assert f'key: "{f}"' in js, f


def test_03_konto_farbe_chef_und_sucher(welt):
    AUTH = _module("routes.auth")
    w, db = welt.w, welt.db
    nutzer = {"id": f"u_akz_{w.s}", "dealer_id": w.dealer_id, "role": "sucher", "active": True}

    async def lauf():
        await db.users.insert_one({**nutzer, "email": f"akz{w.s}@t.invalid", "created_at": _jetzt()})
        erg = await AUTH.akzentfarbe_setzen(AUTH.AkzentIn(farbe="Lila"), user=nutzer)
        doc = await db.users.find_one({"id": nutzer["id"]}, {"_id": 0, "akzentfarbe": 1})
        try:
            await AUTH.akzentfarbe_setzen(AUTH.AkzentIn(farbe="neon"), user=nutzer)
            fehler = None
        except HTTPException as exc:
            fehler = exc.status_code
        doc2 = await db.users.find_one({"id": nutzer["id"]}, {"_id": 0, "akzentfarbe": 1})
        zurueck = await AUTH.akzentfarbe_setzen(AUTH.AkzentIn(farbe="standard"), user=nutzer)
        return erg, doc, fehler, doc2, zurueck

    erg, doc, fehler, doc2, zurueck = welt.run(lauf())
    assert erg == {"akzentfarbe": "lila"} and doc["akzentfarbe"] == "lila"
    assert fehler == 400 and doc2["akzentfarbe"] == "lila", "ungueltige Farbe aendert nichts"
    assert zurueck == {"akzentfarbe": "standard"}
    # /auth/me liefert das Nutzerdokument (current_user ohne Geheimnisse) — akzentfarbe ist Teil davon
    DEPS = _module("deps")
    import inspect
    q = inspect.getsource(DEPS.current_user)
    assert '"password_hash": 0' in q and "akzentfarbe" not in q, "nicht ausgeblendet"


def test_04_fahrer_farbe(welt):
    DR = _module("routes.drivers")
    w, db = welt.w, welt.db

    async def lauf():
        await w.fahrer_anlegen(db)
        vorher = await DR.driver_me(w.driver)
        erg = await DR.driver_update_me(DR.DriverProfileUpdate(akzentfarbe="gruen"), driver=w.driver)
        doc = await db.driver_accounts.find_one({"id": w.driver["id"]}, {"_id": 0, "akzentfarbe": 1, "display_name": 1})
        try:
            await DR.driver_update_me(DR.DriverProfileUpdate(akzentfarbe="neon"), driver=w.driver)
            fehler = None
        except HTTPException as exc:
            fehler = exc.status_code
        return vorher, erg, doc, fehler

    vorher, erg, doc, fehler = welt.run(lauf())
    assert vorher["akzentfarbe"] == "standard"
    assert erg["akzentfarbe"] == "gruen" and doc["akzentfarbe"] == "gruen"
    assert doc["display_name"] == w.driver["display_name"], "Name unangetastet"
    assert fehler == 400


def test_05_oberflaeche_verdrahtet():
    toggle = (FRONTEND / "components" / "ThemeToggle.jsx").read_text(encoding="utf-8")
    assert "applyStoredAkzent(theme)" in toggle, "Designwechsel wendet die Farbe im passenden Design neu an"
    einst = (FRONTEND / "pages" / "app" / "Einstellungen.jsx").read_text(encoding="utf-8")
    assert 'id: "farbe"' in einst and 'api.put("/auth/akzentfarbe"' in einst
    fahrer = (FRONTEND / "pages" / "driver" / "DriverSettings.jsx").read_text(encoding="utf-8")
    assert 'driverApi.put("/driver/me", { akzentfarbe: f })' in fahrer
    for ctx in ("AuthContext.jsx", "DriverContext.jsx"):
        assert "akzentVomKonto(" in (FRONTEND / "context" / ctx).read_text(encoding="utf-8"), ctx
    css = (FRONTEND / "index.css").read_text(encoding="utf-8")
    assert "var(--knopf-primaer, #0a84ff)" in css and "var(--accent-red);" in css
