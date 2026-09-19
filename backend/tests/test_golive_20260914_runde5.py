# -*- coding: utf-8 -*-
"""Go-Live 14.09.2026, Runde 5 (Wuensche Ahmad):

- Kontonummer + Passwort muessen nach dem Anlegen IMMER klappen — alle vier
  Kontoarten (Firma/Chef, Sucher, Zwischenhaendler, Fahrer) mit einem
  20-Zeichen-Passwort, in allen ueblichen Schreibweisen der Kennung
- Zwischenhaendler bekommen einen Kaeufer-Code ("6FE7K2M") statt einer Nummer
  aus der Reihe; Anmeldung klein geschrieben / mit Trennern
- Betreiber-Diagnose GET /admin/konten/pruefen (Kontoart, Anmeldeseite,
  aktiv, Passwort, Anmeldesperre)
- "Passwort setzen" meldet nur dann eine aufgehobene Sperre, wenn eine bestand
- Freigaben (Liste, Zaehler, Freigabe) nur fuer den Chef — Sucher 403
- Sucher "loescht" ein Fahrzeug nur bei sich (POST /vehicles/{id}/entfernen)

HTTP-Teile brauchen RUNDE14_HTTP=1 und ein laufendes Backend (TEST_BASE_URL);
die Regel-Tests laufen immer in-process.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import konten as K  # noqa: E402  (tests/konten.py — HTTP-Helfer)
import routes.admin as ADMIN  # noqa: E402  (nur am Modulanfang importieren)

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
http = pytest.mark.skipif(not HTTP, reason="RUNDE14_HTTP=1 nicht gesetzt")
API = K.API
SUF = "f" + uuid.uuid4().hex[:7]   # nie nur Ziffern: SUF steckt im Passwort, ein reiner Zahlen-Suffix als Nachname/Kontodaten haette es abgelehnt (CI 16.09.2026)
PW20 = f"Rz7{SUF}Kq4Lm9Xw2"          # genau 20 Zeichen, Ziffern enthalten
PW20B = f"Vb3{SUF}Tn8Hd5Yc6"
assert len(PW20) == 20 and len(PW20B) == 20


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


def _get(pfad, headers=None, **kw):
    return requests.get(f"{API}{pfad}", headers=headers, timeout=60, **kw)


def _post(pfad, json=None, headers=None):
    return requests.post(f"{API}{pfad}", json=json, headers=headers, timeout=60)


# ============================================================ Regeln (in-process)
def test_01_kaeufer_code_regeln():
    from kontonummer import (KAEUFER_MUSTER, anmeldekennung, ist_kaeufer_code,
                             kaeufer_code_erzeugen, kaeufer_normalisieren,
                             kennung_normalisieren, normalisieren)
    for roh, erw in ((" 6fe7k2m ", "6FE7K2M"), ("6FE-7K2M", "6FE7K2M"), ("6FE 7K2M", "6FE7K2M"),
                     ("6fe.7k2m", "6FE7K2M"), ("6FE–7K2M", "6FE7K2M"), ("ABCDEF", "ABCDEF"),
                     ("10023", None), ("10023-2", None), ("ABCDE", None), ("ABCDEFGHJK", None),
                     ("6FE7K2O", None), ("6FE7K21", None), ("2345678", None), ("", None),
                     (None, None), ("ci-superadmin", None)):
        assert kaeufer_normalisieren(roh) == erw, (roh, kaeufer_normalisieren(roh), erw)
    assert kennung_normalisieren("10023-2") == "10023-2"
    assert kennung_normalisieren(" 6fe7k2m ") == "6FE7K2M"
    assert kennung_normalisieren("ci-superadmin") is None
    # Limiter-/Audit-Schluessel: Code kanonisch, Benutzername klein
    assert anmeldekennung(" 6fe7k2m ") == "6FE7K2M"
    assert anmeldekennung("Ci-SuperAdmin") == "ci-superadmin"
    # Erzeugte Codes: eindeutig, nie eine Nummer der Reihe, 7 Zeichen
    codes = {kaeufer_code_erzeugen() for _ in range(3000)}
    assert len(codes) == 3000
    for c in codes:
        assert len(c) == 7 and ist_kaeufer_code(c) and KAEUFER_MUSTER.match(c)
        assert normalisieren(c) is None and not c.isdigit()
        assert not set(c) & set("IO01")


def test_02_sperre_rueckmeldung(monkeypatch):
    """_konto_sperre_aufheben sagt, ob wirklich eine Sperre bestand."""
    import rate_limiter as RL

    class _Limiter:
        def __init__(self, stand):
            self._stand, self.resets = stand, []

        async def stand(self, key):
            return self._stand

        async def reset(self, key):
            self.resets.append(key)

    monkeypatch.setattr(RL, "_LOGIN_KONTO_LIMIT", 30)
    # keine Fehlversuche -> keine Sperre, nichts geloescht
    lim = _Limiter(0)
    monkeypatch.setattr(RL, "login_konto_limiter", lim)
    erg = asyncio.run(ADMIN._konto_sperre_aufheben({"kontonummer": "10099"}))
    assert erg == {"sperre_aufgehoben": False, "fehlversuche": 0} and lim.resets == ["10099"]
    # 3 Fehlversuche -> geloescht, aber keine Sperre
    monkeypatch.setattr(RL, "login_konto_limiter", _Limiter(3))
    erg = asyncio.run(ADMIN._konto_sperre_aufheben({"kontonummer": "10099"}))
    assert erg == {"sperre_aufgehoben": False, "fehlversuche": 3}
    # Grenze erreicht -> Sperre aufgehoben
    monkeypatch.setattr(RL, "login_konto_limiter", _Limiter(30))
    erg = asyncio.run(ADMIN._konto_sperre_aufheben({"kontonummer": "6FE7K2M"}))
    assert erg == {"sperre_aufgehoben": True, "fehlversuche": 30}
    # Kaeufer-Code in beliebiger Schreibweise -> kanonischer Schluessel
    lim = _Limiter(0)
    monkeypatch.setattr(RL, "login_konto_limiter", lim)
    asyncio.run(ADMIN._konto_sperre_aufheben({"kontonummer": "6fe7k2m"}))
    assert lim.resets == ["6FE7K2M"]
    # ohne Nummer: nichts zu tun
    assert asyncio.run(ADMIN._konto_sperre_aufheben({})) == {"sperre_aufgehoben": False,
                                                             "fehlversuche": 0}


# ============================================================ HTTP-Welt
@pytest.fixture(scope="module")
def welt():
    if not HTTP:
        pytest.skip("RUNDE14_HTTP=1 nicht gesetzt")
    dbx = K._db()
    z = {"vehicle_ids": [], "termin_ids": []}
    try:
        # Firma + Chef (20-Zeichen-Passwort)
        r = K.registrieren(json={"company_name": f"Runde5 Firma {SUF}", "password": PW20,
                                 "contact_person": "Chef", "email": f"chef_{SUF}@e2etest-mail.de"})
        assert r.status_code == 200, f"Firma: {r.status_code} {r.text[:300]}"
        z["C"] = _kopf(r.json()["token"])
        me = _get("/auth/me", z["C"]).json()["user"]
        z["chef_id"], z["dealer_id"], z["chef_nr"] = me["id"], me["dealer_id"], me["kontonummer"]
        # Sucher
        r = K.sucher_als_chef_anlegen(chef=z["C"], json={"password": PW20, "first_name": "Su",
                                                         "last_name": SUF})
        assert r.status_code == 200, f"Sucher: {r.status_code} {r.text[:300]}"
        z["sucher_id"], z["sucher_nr"] = r.json()["sucher_id"], r.json()["kontonummer"]
        r = K.anmelden(z["sucher_nr"], PW20, "auth")
        assert r.status_code == 200, f"Sucher-Login: {r.status_code} {r.text[:300]}"
        z["S"] = _kopf(r.json()["token"])
        # Fahrer
        r = K.fahrer_registrieren(json={"display_name": f"Runde5 Fahrer {SUF}", "password": PW20})
        assert r.status_code == 200, f"Fahrer: {r.status_code} {r.text[:300]}"
        z["fahrer_id"], z["fahrer_nr"] = r.json()["driver"]["id"], r.json()["driver"]["kontonummer"]
        # Zwischenhaendler (Kaeufer-Code)
        r = K.kaeufer_registrieren(json={"company_name": f"Runde5 Kaeufer {SUF}",
                                         "contact_name": "Kontakt", "password": PW20,
                                         "gewerblich_bestaetigt": True,
                                         "email": f"kaeufer_{SUF}@e2etest-mail.de"})
        assert r.status_code == 200, f"Kaeufer: {r.status_code} {r.text[:300]}"
        z["kaeufer_id"], z["kaeufer_nr"] = r.json()["user"]["id"], r.json()["user"]["kontonummer"]
        yield z
    finally:
        dealer_ids = [z.get("dealer_id")] if z.get("dealer_id") else []
        user_ids = [z.get(k) for k in ("chef_id", "sucher_id", "kaeufer_id") if z.get(k)]
        dbx.users.delete_many({"$or": [{"id": {"$in": user_ids}},
                                       {"dealer_id": {"$in": dealer_ids}},
                                       {"company_name": {"$regex": SUF}}]})
        dbx.dealers.delete_many({"$or": [{"id": {"$in": dealer_ids}},
                                         {"company_name": {"$regex": SUF}}]})
        dbx.driver_accounts.delete_many({"$or": [{"id": z.get("fahrer_id") or "-"},
                                                 {"display_name": {"$regex": SUF}}]})
        for coll in ("subscriptions", "manual_payments", "dealer_drivers", "network_members"):
            dbx[coll].delete_many({"dealer_id": {"$in": dealer_ids}})
        dbx.vehicles.delete_many({"id": {"$in": z["vehicle_ids"]}})
        dbx.appointments.delete_many({"id": {"$in": z["termin_ids"]}})
        dbx.activity_logs.delete_many({"$or": [{"user_id": {"$in": user_ids + [z.get("fahrer_id") or "-"]}},
                                               {"ref": {"$in": user_ids + z["vehicle_ids"]}}]})


@http
def test_10_alle_vier_konten_melden_sich_an(welt):
    """Kontonummer + Passwort klappen nach dem Anlegen — in allen Schreibweisen."""
    from kontonummer import KAEUFER_MUSTER
    chef_nr, sucher_nr = welt["chef_nr"], welt["sucher_nr"]
    assert sucher_nr == f"{chef_nr}-1", (chef_nr, sucher_nr)
    assert KAEUFER_MUSTER.match(welt["kaeufer_nr"]) and not welt["kaeufer_nr"].isdigit()
    assert welt["fahrer_nr"].startswith("FD-") and len(welt["fahrer_nr"]) == 11
    # Chef und Sucher unter /login (Single-Session: jede Anmeldung ersetzt die
    # vorige Sitzung — die zuletzt ausgestellten Token gelten fuer die naechsten Tests)
    for kennung in (chef_nr, f" {chef_nr} ", f"{chef_nr}"):
        r = K.anmelden(kennung, PW20, "auth")
        assert r.status_code == 200, (kennung, r.status_code, r.text[:200])
    welt["C"] = _kopf(r.json()["token"])
    basis, zusatz = sucher_nr.split("-")
    for kennung in (sucher_nr, f"{basis} {zusatz}", f"{basis}/{zusatz}", f" {basis}-0{zusatz} "):
        r = K.anmelden(kennung, PW20, "auth")
        assert r.status_code == 200, (kennung, r.status_code, r.text[:200])
    welt["S"] = _kopf(r.json()["token"])
    # Fahrer in der Fahrer-App
    fn = welt["fahrer_nr"]
    for kennung in (fn, f" {fn} ", fn.lower(), fn.replace("-", " "), fn.replace("-", "")):
        r = K.anmelden(kennung, PW20, "driver")
        assert r.status_code == 200, (kennung, r.status_code, r.text[:200])
    # Zwischenhaendler: Code in jeder Schreibweise, im Marktplatz und unter /login
    code = welt["kaeufer_nr"]
    varianten = (code, code.lower(), f" {code} ", f"{code[:3]}-{code[3:]}", f"{code[:3]} {code[3:].lower()}")
    for kennung in varianten:
        r = K.anmelden(kennung, PW20, "buyer")
        assert r.status_code == 200, (kennung, r.status_code, r.text[:200])
        assert r.json()["user"]["kontonummer"] == code
    r = K.anmelden(code.lower(), PW20, "auth")
    assert r.status_code == 200, r.text[:200]
    # Falsche Tuer: immer 401 "Kontonummer oder Passwort falsch" (keine Aufzaehlung)
    for kennung, weg in ((welt["fahrer_nr"], "auth"), (welt["fahrer_nr"], "buyer"),
                         (code, "driver"), (chef_nr, "driver"), (chef_nr, "buyer"),
                         (sucher_nr, "buyer")):
        r = K.anmelden(kennung, PW20, weg)
        assert r.status_code == 401, (kennung, weg, r.status_code, r.text[:200])
        assert "Kontonummer oder Passwort falsch" in r.text
    # Passwort mit Leerzeichen am Ende ist ein anderes Passwort -> 401
    assert K.anmelden(chef_nr, PW20 + " ", "auth").status_code == 401


@http
def test_11_konto_diagnose(welt):
    S = K.super_kopf()
    erwartet = {welt["chef_nr"]: ("firma", "/login"), welt["sucher_nr"]: ("sucher", "/login"),
                welt["kaeufer_nr"].lower(): ("kaeufer", "/markt/login"),
                welt["fahrer_nr"]: ("fahrer", "/fahrer/login")}
    for kennung, (art, seite) in erwartet.items():
        r = _get("/admin/konten/pruefen", S, params={"kennung": kennung})
        assert r.status_code == 200, (kennung, r.text[:300])
        d = r.json()
        assert d["gefunden"] and d["art"] == art and d["anmeldeseite"] == seite, d
        assert d["aktiv"] is True and d["passwort_gesetzt"] is True
        assert d["fehlversuche"] == 0 and d["anmeldesperre"] is False
        assert d["hinweise"] and seite in d["hinweise"][0]
        assert "password_hash" not in d
    d = _get("/admin/konten/pruefen", S, params={"kennung": welt["fahrer_nr"]}).json()
    assert d["driver_code"].startswith("FD-") and "Fahrer-App" in d["hinweise"][0]
    d = _get("/admin/konten/pruefen", S, params={"kennung": welt["sucher_nr"]}).json()
    assert d["firma"] == f"Runde5 Firma {SUF}"
    # unbekannt / ungueltig
    d = _get("/admin/konten/pruefen", S, params={"kennung": "999999999"}).json()
    assert d["gefunden"] is False and "Kein Konto" in d["hinweis"]
    d = _get("/admin/konten/pruefen", S, params={"kennung": "abc"}).json()
    assert d["gefunden"] is False and "Käufer-Code" in d["hinweis"]
    # nur der Super-Admin
    assert _get("/admin/konten/pruefen", welt["C"], params={"kennung": welt["chef_nr"]}).status_code == 403
    assert _get("/admin/konten/pruefen", S).status_code == 422


@http
def test_12_passwort_setzen_meldet_nur_echte_sperre(welt):
    S = K.super_kopf()
    r = _post(f"/admin/drivers/{welt['fahrer_id']}/password", {"new_password": PW20B}, S)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["sperre_aufgehoben"] is False and r.json()["fehlversuche"] == 0, r.json()
    assert K.anmelden(welt["fahrer_nr"], PW20B, "driver").status_code == 200
    assert K.anmelden(welt["fahrer_nr"], PW20, "driver").status_code == 401
    r = _post(f"/admin/users/{welt['sucher_id']}/password", {"new_password": PW20B}, S)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["sperre_aufgehoben"] is False and r.json()["fehlversuche"] == 0, r.json()
    r = K.anmelden(welt["sucher_nr"], PW20B, "auth")
    assert r.status_code == 200, r.text[:300]
    welt["S"] = _kopf(r.json()["token"])
    r = _post(f"/admin/users/{welt['kaeufer_id']}/password", {"new_password": PW20B}, S)
    assert r.status_code == 200, r.text[:300]
    assert K.anmelden(welt["kaeufer_nr"].lower(), PW20B, "buyer").status_code == 200
    # zu kurz / zu lang: klare 422 statt stillem Scheitern
    for pw in ("Kurz9!", "x" * 80 + "1"):
        r = _post(f"/admin/drivers/{welt['fahrer_id']}/password", {"new_password": pw}, S)
        assert r.status_code in (400, 422), (pw, r.status_code, r.text[:200])


@http
def test_13_freigaben_nur_chef(welt):
    S_kopf, C = welt["S"], welt["C"]
    for pfad in ("/protocols/zur-freigabe/anzahl", "/protocols/zur-freigabe"):
        r = _get(pfad, S_kopf)
        assert r.status_code == 403, (pfad, r.status_code, r.text[:200])
        assert "Hauptaccount" in r.text
        r = _get(pfad, C)
        assert r.status_code == 200, (pfad, r.status_code, r.text[:200])
    r = _post("/protocols/gibtsnicht/freigabe", {"zurueck": False, "stand": "x"}, S_kopf)
    assert r.status_code == 403, (r.status_code, r.text[:200])
    r = _post("/protocols/gibtsnicht/freigabe", {"zurueck": False, "stand": "x"}, C)
    assert r.status_code == 404, (r.status_code, r.text[:200])


@http
def test_14_sucher_entfernt_fahrzeug_nur_bei_sich(welt):
    dbx = K._db()
    S_kopf, C = welt["S"], welt["C"]
    dealer_id, sucher_id, chef_id = welt["dealer_id"], welt["sucher_id"], welt["chef_id"]

    def fahrzeug(kennzeichen, **extra):
        vid = f"r5v_{kennzeichen}_{SUF}"
        doc = {"id": vid, "dealer_id": dealer_id, "source": "manuell", "lifecycle": "abgeholt",
               "status": "abgeholt", "data": {"make": "VW", "model": f"Golf {kennzeichen}",
                                              "price": 5000},
               "created_at": _jetzt(), "updated_at": _jetzt()}
        doc.update(extra)
        dbx.vehicles.insert_one(doc)
        welt["vehicle_ids"].append(vid)
        return vid

    # 1) Sucher ist Hauptbearbeiter -> Fahrzeug geht an den Chef
    v1 = fahrzeug("a", owner_user_id=sucher_id)
    assert _get(f"/vehicles/{v1}", S_kopf).status_code == 200
    assert _get(f"/vehicles/{v1}", C).status_code == 200
    # Chef-Weg ist nicht dieser
    r = _post(f"/vehicles/{v1}/entfernen", None, C)
    assert r.status_code == 400, (r.status_code, r.text[:200])
    # Chef-Entscheidung bleibt Chefsache (Sucher 403)
    assert _post(f"/vehicles/{v1}/decision", {"decision": "loeschen"}, S_kopf).status_code == 403
    r = _post(f"/vehicles/{v1}/entfernen", None, S_kopf)
    assert r.status_code == 200, (r.status_code, r.text[:300])
    assert r.json()["an_chef"] is True and r.json()["owner_user_id"] == chef_id
    assert _get(f"/vehicles/{v1}", S_kopf).status_code == 404       # bei ihm weg ...
    r = _get(f"/vehicles/{v1}", C)
    assert r.status_code == 200 and r.json().get("lifecycle") == "abgeholt"   # ... beim Chef nicht
    doc = dbx.vehicles.find_one({"id": v1})
    assert doc["owner_user_id"] == chef_id and doc["entfernt_von_sucher"] == sucher_id
    assert doc["lifecycle"] == "abgeholt" and doc["data"]["price"] == 5000
    # zweiter Aufruf: nicht mehr im Bereich -> 404
    assert _post(f"/vehicles/{v1}/entfernen", None, S_kopf).status_code == 404

    # 2) Sucher ist nur Mitbearbeiter -> wird ausgetragen, Besitzer bleibt
    v2 = fahrzeug("b", owner_user_id=chef_id, mitbearbeiter_ids=[sucher_id, "andere"])
    assert _get(f"/vehicles/{v2}", S_kopf).status_code == 200
    r = _post(f"/vehicles/{v2}/entfernen", None, S_kopf)
    assert r.status_code == 200 and r.json()["an_chef"] is False, r.text[:300]
    doc = dbx.vehicles.find_one({"id": v2})
    assert doc["owner_user_id"] == chef_id and doc["mitbearbeiter_ids"] == ["andere"]
    assert _get(f"/vehicles/{v2}", S_kopf).status_code == 404
    assert _get(f"/vehicles/{v2}", C).status_code == 200

    # 3) laufender Termin des Suchers -> 409, nichts veraendert
    v3 = fahrzeug("c", owner_user_id=sucher_id)
    tid = f"r5t_{SUF}"
    dbx.appointments.insert_one({"id": tid, "dealer_id": dealer_id, "vehicle_id": v3,
                                 "created_by": sucher_id, "status": "geplant",
                                 "pickup_date": "2026-12-01", "pickup_time": "10:00",
                                 "created_at": _jetzt(), "updated_at": _jetzt()})
    welt["termin_ids"].append(tid)
    r = _post(f"/vehicles/{v3}/entfernen", None, S_kopf)
    assert r.status_code == 409 and "Termin" in r.text, (r.status_code, r.text[:200])
    assert dbx.vehicles.find_one({"id": v3})["owner_user_id"] == sucher_id
    dbx.appointments.update_one({"id": tid}, {"$set": {"status": "abgeholt"}})
    assert _post(f"/vehicles/{v3}/entfernen", None, S_kopf).status_code == 200

    # 4) fremdes Fahrzeug der Firma (weder Besitzer noch Mitbearbeiter) -> 404
    v4 = fahrzeug("d", owner_user_id=chef_id)
    assert _post(f"/vehicles/{v4}/entfernen", None, S_kopf).status_code == 404
    assert dbx.vehicles.find_one({"id": v4})["owner_user_id"] == chef_id
