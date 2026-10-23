# -*- coding: utf-8 -*-
"""Grosser Rollentest (Wunsch Ahmad 20.09.2026): alle Rollen, alle Wege.

Faehrt EINE Firma komplett durch — Betreiber legt an, Chef und Sucher
arbeiten, Fahrer holt ab, Zwischenhaendler kauft — und prueft bei jedem
Schritt BEIDES:

  * darf die Rolle, was sie darf?            (Funktion)
  * darf sie NICHT, was sie nicht darf?      (Abgrenzung)

Das Zweite ist der eigentliche Zweck. Ein Test, der nur den Erfolgsfall
prueft, uebersieht genau die Fehler, die heute gefunden wurden (ein
zweites Chef-Konto mit Chef-Rechten, ein Sucher mit Fahrerdaten).

Aufruf (Backend laeuft auf TEST_BASE_URL, dieselbe Datenbank):
    python -X utf8 scripts/rollentest_gross.py
    python -X utf8 scripts/rollentest_gross.py --behalten   # Daten stehen lassen

Exit 0 = alles wie erwartet, 1 = mindestens eine Abweichung.
Angelegte Konten und Daten werden am Ende wieder entfernt.
"""
import argparse
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

BASE = (os.environ.get("TEST_BASE_URL") or "http://127.0.0.1:8002").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "Rt7Kq2Mx9-Sicher!42"

# Die Kennungen muessen den ECHTEN Mustern folgen (kontonummer.py), sonst
# findet die Anmeldung das Konto gar nicht erst:
#   Chef/Firma   99xxx        (MUSTER: 4-9 Ziffern, keine fuehrende 0)
#   Sucher       99xxx-2      (Firmennummer + Zusatz)
#   Kaeufer      6-9 Zeichen aus dem Kaeufer-Alphabet, mind. ein Buchstabe
#   Fahrer       FD- + 8 Zeichen desselben Alphabets
import kontonummer as _kn  # noqa: E402

_BASIS_NR = 99000 + (int(SUF[:4], 16) % 900)          # 99000-99899
NR_CHEF = str(_BASIS_NR)
NR_ZWEITER = f"{_BASIS_NR}-8"
NR_SUCHER = f"{_BASIS_NR}-2"
NR_KAEUFER = _kn.kaeufer_code_erzeugen()
CODE_FAHRER = "FD-" + "".join(
    _kn.KAEUFER_ALPHABET[(int(SUF, 16) >> (3 * i)) % len(_kn.KAEUFER_ALPHABET)]
    for i in range(8))

_ok = _fehler = 0
_abweichungen: list = []


def pruefe(bedingung, text: str, zusatz: str = "") -> bool:
    """Eine Zusage pruefen. Liefert die Bedingung zurueck, damit sich
    Folgeschritte daran haengen lassen."""
    global _ok, _fehler
    if bedingung:
        _ok += 1
        print(f"  OK    {text}")
    else:
        _fehler += 1
        _abweichungen.append(text + (f" — {zusatz}" if zusatz else ""))
        print(f"  FEHLT {text}" + (f"  [{zusatz}]" if zusatz else ""))
    return bool(bedingung)


def abschnitt(titel: str) -> None:
    print(f"\n{titel}")


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _hash(pw: str) -> str:
    import bcrypt
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat()


def kopf(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def get(pfad: str, token=None, **kw):
    return requests.get(f"{API}{pfad}", headers=kopf(token) if token else None,
                        timeout=60, **kw)


def post(pfad: str, json_=None, token=None):
    return requests.post(f"{API}{pfad}", json=json_ or {},
                         headers=kopf(token) if token else None, timeout=60)


def put(pfad: str, json_=None, token=None):
    return requests.put(f"{API}{pfad}", json=json_ or {},
                        headers=kopf(token) if token else None, timeout=60)


def delete(pfad: str, token=None):
    return requests.delete(f"{API}{pfad}", headers=kopf(token) if token else None,
                           timeout=60)


# =====================================================================
def aufbau(db) -> dict:
    """Konten direkt in der Datenbank anlegen — der Test prueft die Wege,
    nicht die Anlage (die hat ihre eigenen Tests)."""
    w = {"user_ids": [], "dealer_ids": [], "driver_ids": []}
    firma = f"f-{SUF}"
    chef, zweiter, sucher = f"chef-{SUF}", f"zweit-{SUF}", f"such-{SUF}"
    kaeufer, fahrer = f"kauf-{SUF}", f"fahr-{SUF}"

    db.dealers.insert_one({"id": firma, "company_name": f"Rollentest {SUF}",
                           "kunden_nr": _BASIS_NR,
                           "user_id": chef, "created_at": _jetzt()})
    db.users.insert_many([
        {"id": chef, "username": chef, "role": "dealer", "dealer_id": firma,
         "active": True, "password_hash": _hash(PW), "current_session_id": None,
         "kontonummer": NR_CHEF, "created_at": "2026-01-01T00:00:00+00:00"},
        # Genau die Lage aus dem P0-Befund: ein ZWEITES dealer-Konto.
        {"id": zweiter, "username": zweiter, "role": "dealer", "dealer_id": firma,
         "active": True, "password_hash": _hash(PW), "current_session_id": None,
         "kontonummer": NR_ZWEITER, "created_at": "2026-06-01T00:00:00+00:00"},
        {"id": sucher, "username": sucher, "role": "sucher", "dealer_id": firma,
         "active": True, "password_hash": _hash(PW), "current_session_id": None,
         "kontonummer": NR_SUCHER, "created_at": _jetzt()},
        {"id": kaeufer, "username": kaeufer, "role": "b2b_buyer", "dealer_id": None,
         "active": True, "password_hash": _hash(PW), "current_session_id": None,
         "kontonummer": NR_KAEUFER, "company_name": "Zwischenhandel Test",
         "created_at": _jetzt()},
    ])
    db.subscriptions.insert_many([
        {"id": f"abo-{SUF}-1", "dealer_id": firma, "subject_user_id": chef,
         "plan": "yearly", "status": "active",
         "expires_at": (datetime.now(timezone.utc) + timedelta(days=300)).isoformat()},
        {"id": f"abo-{SUF}-2", "dealer_id": firma, "subject_user_id": sucher,
         "plan": "monthly", "status": "active",
         "expires_at": (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()},
    ])
    db.driver_accounts.insert_one(
        {"id": fahrer, "display_name": "Test Fahrer", "driver_code": CODE_FAHRER,
         "kontonummer": CODE_FAHRER,
         "email": f"fahrer{SUF}@example.invalid", "active": True,
         "password_hash": _hash(PW), "current_session_id": None,
         "created_at": _jetzt()})
    db.dealer_drivers.insert_one(
        {"id": f"link-{SUF}", "dealer_id": firma, "driver_account_id": fahrer,
         "display_name": "Test Fahrer",
         "active": True, "created_at": _jetzt()})
    w.update(firma=firma, chef=chef, zweiter=zweiter, sucher=sucher,
             kaeufer=kaeufer, fahrer=fahrer)
    w["user_ids"] = [chef, zweiter, sucher, kaeufer]
    w["dealer_ids"] = [firma]
    w_firma.append(firma)
    w_chef.append(chef)
    w["driver_ids"] = [fahrer]
    return w


def anmelden(w: dict) -> dict:
    """Jede Rolle anmelden. WICHTIG: nacheinander — eine neue Anmeldung
    desselben Kontos wirft die alte raus (Einzelsitzung ist Absicht)."""
    abschnitt("1. Anmeldung aller Rollen")
    token = {}
    for name, nummer in (("chef", NR_CHEF), ("zweiter", NR_ZWEITER),
                         ("sucher", NR_SUCHER)):
        r = post("/auth/login", {"kontonummer": nummer, "password": PW})
        if pruefe(r.status_code == 200, f"{name} kann sich anmelden",
                  f"HTTP {r.status_code} {r.text[:120]}"):
            token[name] = r.json().get("token")
    r = post("/buyer/login", {"kontonummer": NR_KAEUFER, "password": PW})
    if pruefe(r.status_code == 200, "Zwischenhaendler kann sich anmelden",
              f"HTTP {r.status_code} {r.text[:120]}"):
        token["kaeufer"] = r.json().get("token")
    r = post("/driver/login", {"kontonummer": CODE_FAHRER, "password": PW})
    if pruefe(r.status_code == 200, "Fahrer kann sich anmelden",
              f"HTTP {r.status_code} {r.text[:120]}"):
        token["fahrer"] = r.json().get("token")

    pruefe(post("/auth/login", {"kontonummer": NR_CHEF,
                                "password": "falsch"}).status_code in (401, 429),
           "falsches Passwort wird abgewiesen")
    return token


w_firma: list = []          # Firmen-Kennung, fuer die Proben in Abschnitt 2
w_chef: list = []           # Kennung des echten Hauptchefs


def db_lesen():
    """Frische Sicht auf die Datenbank (der Server schreibt dazwischen)."""
    return _db()


def chef_gegen_zweites_konto(t: dict) -> None:
    """Der P0-Befund von heute — die wichtigste Abgrenzung des Tests."""
    abschnitt("2. Chef-Rechte: nur der EINE Hauptchef (P0, 20.09.2026)")
    chef, zweit = t.get("chef"), t.get("zweiter")
    if not (chef and zweit):
        pruefe(False, "Chef und zweites Konto angemeldet")
        return
    wege = [
        ("Firmenlogo", lambda tok: post("/dealer/logo", {"logo_b64": "x"}, tok)),
        ("Abo kuendigen", lambda tok: post("/dealer/subscription/cancel", {}, tok)),
        ("Team-Seite", lambda tok: get("/dealer/sucher", tok)),
    ]
    for name, ruf in wege:
        r = ruf(zweit)
        pruefe(r.status_code == 403,
               f"zweites dealer-Konto: {name} -> 403",
               f"HTTP {r.status_code} {r.text[:100]}")
    # Profilwechsel ist mit Absicht KEIN 403: wer nicht Hauptchef ist,
    # schaltet sein EIGENES Profil um (Override). Geprueft wird deshalb,
    # dass die Firma dabei unberuehrt bleibt.
    put("/dealer/active-profile", {"active_profile": "inland"}, chef)
    vorher = (db_lesen().dealers.find_one({"id": w_firma[0]}) or {}).get(
        "active_profile")
    r = put("/dealer/active-profile", {"active_profile": "export"}, zweit)
    pruefe(r.status_code == 200,
           "zweites dealer-Konto: eigenes Profil umschalten -> 200",
           f"HTTP {r.status_code} {r.text[:100]}")
    nachher = (db_lesen().dealers.find_one({"id": w_firma[0]}) or {}).get(
        "active_profile")
    pruefe(nachher == vorher,
           "zweites dealer-Konto aendert das Profil der FIRMA nicht",
           f"vorher {vorher!r}, nachher {nachher!r}")
    r = put("/dealer/active-profile", {"active_profile": "export"}, chef)
    pruefe(r.status_code == 200, "echter Chef: Firmenprofil umschalten -> 200",
           f"HTTP {r.status_code} {r.text[:100]}")
    pruefe((db_lesen().dealers.find_one({"id": w_firma[0]}) or {}).get(
        "active_profile") == "export",
        "und beim Chef wirkt der Wechsel auf die Firma")

    # Nachpruefung 20.09.2026: Rechte sind das eine, die SICHT das andere.
    # Ein zweites dealer-Konto bekam ueber ist_sucher() ("wer kein Sucher
    # ist, ist Chef") die ganze Firma zu sehen — Fahrzeuge, Termine,
    # Vertraege. Seit dem Fix nordet current_firma es auf Sucher ein.
    fremd = f"nurchef-{SUF}"
    db_lesen().vehicles.insert_one(
        {"id": fremd, "dealer_id": w_firma[0], "lifecycle": "bestand",
         "owner_user_id": w_chef[0], "mitbearbeiter_ids": [w_chef[0]],
         "created_at": _jetzt(), "data": {"make": "Audi", "model": "A4"}})
    try:
        r = get("/vehicles", chef)
        ids_chef = [x.get("id") for x in (r.json() or []) if isinstance(x, dict)]
        pruefe(fremd in ids_chef, "der echte Chef sieht das Fahrzeug der Firma",
               f"HTTP {r.status_code}")
        r = get("/vehicles", zweit)
        ids_zweit = [x.get("id") for x in (r.json() or []) if isinstance(x, dict)]
        pruefe(fremd not in ids_zweit,
               "zweites dealer-Konto sieht fremde Firmenfahrzeuge NICHT",
               f"{len(ids_zweit)} Fahrzeuge sichtbar")
    finally:
        db_lesen().vehicles.delete_one({"id": fremd})


def sucher_grenzen(t: dict) -> None:
    abschnitt("3. Sucher: eigener Bereich, keine Chefsachen")
    such = t.get("sucher")
    if not such:
        pruefe(False, "Sucher angemeldet")
        return
    for name, r in (
            ("Team-Seite", get("/dealer/sucher", such)),
            ("Sucher anlegen", post("/dealer/sucher", {}, such)),
            ("Firmenlogo", post("/dealer/logo", {"logo_b64": "x"}, such)),
            ("Abo kuendigen", post("/dealer/subscription/cancel", {}, such))):
        pruefe(r.status_code == 403, f"Sucher: {name} -> 403",
               f"HTTP {r.status_code} {r.text[:100]}")
    for name, r in (
            ("eigene Einstellungen lesen", get("/dealer/settings", such)),
            ("Bestand sehen", get("/vehicles", such)),
            ("Termine sehen", get("/appointments", such))):
        pruefe(r.status_code == 200, f"Sucher: {name} -> 200",
               f"HTTP {r.status_code} {r.text[:100]}")


def fremde_bereiche(t: dict) -> None:
    abschnitt("4. Rollen bleiben in ihrem Bereich")
    faelle = [
        ("kaeufer", "Firmenbestand", lambda tok: get("/vehicles", tok)),
        ("kaeufer", "Termine", lambda tok: get("/appointments", tok)),
        ("kaeufer", "Betreiber-Bereich", lambda tok: get("/admin/stats", tok)),
        ("chef", "Betreiber-Bereich", lambda tok: get("/admin/stats", tok)),
        ("sucher", "Betreiber-Bereich", lambda tok: get("/admin/stats", tok)),
        ("fahrer", "Firmenbestand", lambda tok: get("/vehicles", tok)),
        ("fahrer", "Betreiber-Bereich", lambda tok: get("/admin/stats", tok)),
    ]
    for rolle, was, ruf in faelle:
        if rolle not in t:
            continue
        r = ruf(t[rolle])
        pruefe(r.status_code in (401, 403), f"{rolle}: {was} -> gesperrt",
               f"HTTP {r.status_code} {r.text[:100]}")
    r = get("/vehicles")
    pruefe(r.status_code in (401, 403), "ohne Anmeldung: Bestand -> gesperrt",
           f"HTTP {r.status_code}")


def fahrerdaten(t: dict) -> None:
    """Nachpruefung 15.09. (Fahrer Nr. 15) + P0 von heute."""
    abschnitt("5. Fahrerdaten: Kennung und E-Mail nur fuer den Hauptchef")
    for rolle, voll_erwartet in (("chef", True), ("zweiter", False),
                                 ("sucher", False)):
        if rolle not in t:
            continue
        r = get("/drivers", t[rolle])
        if not pruefe(r.status_code == 200, f"{rolle}: Fahrerliste -> 200",
                      f"HTTP {r.status_code}"):
            continue
        eintraege = r.json() or []
        hat_kennung = any(d.get("driver_code") for d in eintraege)
        hat_mail = any(d.get("email") for d in eintraege)
        pruefe(hat_kennung is voll_erwartet,
               f"{rolle}: Fahrer-Kennung sichtbar = {voll_erwartet}")
        pruefe(hat_mail is voll_erwartet,
               f"{rolle}: Fahrer-E-Mail sichtbar = {voll_erwartet}")
        pruefe(all(d.get("name") for d in eintraege) or not eintraege,
               f"{rolle}: Fahrername ist fuer alle sichtbar")


def marktplatz_regeln(t: dict, w: dict, db) -> None:
    """Die Regeln vom 20.09.2026 am echten Weg."""
    abschnitt("6. Weiterverkauf: Regeln vom 20.09.2026")
    chef = t.get("chef")
    if not chef:
        pruefe(False, "Chef angemeldet")
        return
    vid = f"v-{SUF}"
    db.vehicles.insert_one(
        {"id": vid, "dealer_id": w["firma"], "lifecycle": "bestand",
         "created_at": _jetzt(),
         "data": {"make": "BMW", "model": "320d", "price": 15000,
                  "image_urls": [f"https://example.invalid/{i}.jpg" for i in range(12)]}})
    r = post(f"/resale/draft/{vid}", {}, chef)
    if not pruefe(r.status_code in (200, 201), "Chef legt ein Inserat an",
                  f"HTTP {r.status_code} {r.text[:150]}"):
        return
    lid = (r.json() or {}).get("id")
    doc = db.resale_listings.find_one({"id": lid}, {"_id": 0, "photos": 1})
    fotos = (doc or {}).get("photos") or {}
    pruefe(not fotos.get("einkauf_urls"),
           "KEINE Fotos aus dem Portal-Inserat uebernommen",
           f"{len(fotos.get('einkauf_urls') or [])} uebernommen")
    pruefe(fotos.get("mode") == "neu", "Foto-Modus ist 'neu'")

    r = put(f"/resale/{lid}", {"description": "x" * 501}, chef)
    pruefe(r.status_code == 422, "Beschreibung ueber 500 Zeichen -> abgelehnt",
           f"HTTP {r.status_code}")
    r = put(f"/resale/{lid}", {"description": "x" * 500, "price_public": 17900}, chef)
    pruefe(r.status_code == 200, "Beschreibung mit genau 500 Zeichen -> 200",
           f"HTTP {r.status_code} {r.text[:120]}")

    # Fahrzeug UND Inserat muessen auf verkaufsbereit stehen — der Weg ist
    # bestand -> verkaufsentwurf -> verkaufsbereit -> veroeffentlicht.
    db.resale_listings.update_one({"id": lid}, {"$set": {"status": "verkaufsbereit"}})
    db.vehicles.update_one({"id": vid}, {"$set": {"lifecycle": "verkaufsbereit"}})
    r = post(f"/resale/{lid}/publish", {"visibility": "public"}, chef)
    pruefe(r.status_code == 400 and "Foto" in r.text,
           "ohne eigenes Foto: Veroeffentlichen -> abgelehnt",
           f"HTTP {r.status_code} {r.text[:150]}")

    db.resale_listings.update_one(
        {"id": lid}, {"$set": {"photos.uploaded_keys": [f"resale/{SUF}.jpg"]}})
    r = post(f"/resale/{lid}/publish", {"visibility": "public"}, chef)
    pruefe(r.status_code in (200, 402),
           "mit eigenem Foto: Veroeffentlichen -> erlaubt (oder Kontingent)",
           f"HTTP {r.status_code} {r.text[:150]}")
    w["listing_id"] = lid
    w["vehicle_id"] = vid


def fotogrenze(t: dict, w: dict) -> None:
    """Hoechstens 10 Fotos — und zwar am echten Weg, nicht nur im Modell."""
    abschnitt("6b. Fotogrenze: hoechstens 10 Bilder je Inserat")
    chef, lid = t.get("chef"), w.get("listing_id")
    if not (chef and lid):
        pruefe(False, "Inserat aus Schritt 6 vorhanden")
        return
    # Elf Bilder: die Zaehlung greift VOR jeder Bildpruefung, der Text nennt
    # die Grenze. Zehn duerfen zahlenmaessig durch (und scheitern dann
    # hoechstens am Bildinhalt — ein anderer Fehler, genau das wird geprueft).
    r = post(f"/resale/{lid}/photos", {"photos_b64": ["x"] * 11}, chef)
    pruefe(r.status_code == 400 and "10" in r.text,
           "elftes Foto -> abgelehnt mit Hinweis auf die Grenze",
           f"HTTP {r.status_code} {r.text[:150]}")
    db_lesen().resale_listings.update_one(
        {"id": lid}, {"$set": {"photos.uploaded_keys": []}})
    r = post(f"/resale/{lid}/photos", {"photos_b64": ["x"] * 10}, chef)
    pruefe("10" not in (r.text or "") or r.status_code != 400,
           "zehn Fotos scheitern NICHT an der Grenze",
           f"HTTP {r.status_code} {r.text[:150]}")


def kaeufer_sicht(t: dict, w: dict, db) -> None:
    """Der Zwischenhaendler sieht Inserate — aber nur veroeffentlichte,
    und anfassen darf er sie nicht."""
    abschnitt("7. Zwischenhaendler: sehen ja, aendern nein")
    kauf, lid = t.get("kaeufer"), w.get("listing_id")
    if not (kauf and lid):
        pruefe(False, "Kaeufer angemeldet und Inserat vorhanden")
        return
    db.resale_listings.update_one({"id": lid}, {"$set": {"status": "entwurf"}})
    r = get("/marktplatz/listings", kauf)
    pruefe(r.status_code in (200, 402, 403),
           "Marktplatz ist fuer den Zwischenhaendler erreichbar",
           f"HTTP {r.status_code} {r.text[:100]}")
    if r.status_code == 200:
        ids = [x.get("id") for x in (r.json() or {}).get("items", r.json() or [])
               if isinstance(x, dict)]
        pruefe(lid not in ids, "ein Entwurf taucht im Marktplatz NICHT auf")
    for was, ruf in (("aendern", lambda: put(f"/resale/{lid}",
                                             {"description": "fremd"}, kauf)),
                     ("loeschen", lambda: delete(f"/resale/{lid}", kauf)),
                     ("veroeffentlichen", lambda: post(f"/resale/{lid}/publish",
                                                       {"visibility": "public"},
                                                       kauf))):
        r = ruf()
        pruefe(r.status_code in (401, 403, 404),
               f"fremdes Inserat {was} -> gesperrt",
               f"HTTP {r.status_code} {r.text[:100]}")
    pruefe(db.resale_listings.find_one({"id": lid}) is not None,
           "das Inserat steht danach unveraendert da")


def laufzeit(w: dict, db) -> None:
    abschnitt("8. Laufzeit: 3 Wochen, dann verschwindet nur die Anzeige")
    import asyncio

    import cleanup_service as CS
    lid = w.get("listing_id")
    if not lid:
        pruefe(False, "Inserat aus Schritt 6 vorhanden")
        return
    db.resale_listings.update_one(
        {"id": lid},
        {"$set": {"status": "veroeffentlicht", "photos.uploaded_keys": [],
                  "published_at": (datetime.now(timezone.utc)
                                   - timedelta(days=30)).isoformat()}})
    db.generated_pdfs.insert_one(
        {"id": f"c-{SUF}", "dealer_id": w["firma"], "vehicle_id": w["vehicle_id"],
         "created_at": _jetzt()})
    n = asyncio.run(CS.abgelaufene_inserate_entfernen(
        __import__("deps").db, datetime.now(timezone.utc)))
    pruefe(n >= 1, "abgelaufenes Inserat wird entfernt", f"{n} entfernt")
    pruefe(db.resale_listings.find_one({"id": lid}) is None,
           "die Anzeige ist weg")
    pruefe(db.generated_pdfs.find_one({"id": f"c-{SUF}"}) is not None,
           "der Kaufvertrag bleibt")
    pruefe(db.vehicles.find_one({"id": w["vehicle_id"]}) is not None,
           "das Fahrzeug bleibt im Bestand")


def einzelsitzung(w: dict, t: dict) -> None:
    abschnitt("9. Einzelsitzung: neue Anmeldung wirft die alte raus")
    alt = t.get("sucher")
    if not alt:
        pruefe(False, "Sucher angemeldet")
        return
    r = post("/auth/login", {"kontonummer": NR_SUCHER, "password": PW})
    if not pruefe(r.status_code == 200, "Sucher meldet sich erneut an"):
        return
    neu = r.json().get("token")
    pruefe(get("/dealer/settings", alt).status_code == 401,
           "die ALTE Sitzung ist damit ungueltig")
    pruefe(get("/dealer/settings", neu).status_code == 200,
           "die NEUE Sitzung arbeitet weiter")
    t["sucher"] = neu


def abbau(w: dict, db) -> None:
    for coll, feld, werte in (
            ("users", "id", w.get("user_ids") or []),
            ("dealers", "id", w.get("dealer_ids") or []),
            ("driver_accounts", "id", w.get("driver_ids") or [])):
        if werte:
            db[coll].delete_many({feld: {"$in": werte}})
    for coll in ("subscriptions", "dealer_drivers", "resale_listings",
                 "vehicles", "generated_pdfs", "kaufvorgaenge", "appointments",
                 "activity_logs", "plan_requests"):
        db[coll].delete_many({"$or": [{"dealer_id": w.get("firma")},
                                      {"id": {"$regex": SUF}}]})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Grosser Rollentest")
    ap.add_argument("--behalten", action="store_true",
                    help="Testdaten nicht aufraeumen (zum Nachsehen)")
    args = ap.parse_args(argv)

    print(f"Grosser Rollentest gegen {BASE}  (Datenbank {DB_NAME}, Kennung {SUF})")
    try:
        r = requests.get(f"{API}/ready", timeout=10)
        if r.status_code not in (200, 503):
            print(f"FEHLER: {API}/ready antwortet {r.status_code}")
            return 1
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER: Backend unter {BASE} nicht erreichbar ({exc})")
        return 1

    db = _db()
    w = aufbau(db)
    try:
        t = anmelden(w)
        chef_gegen_zweites_konto(t)
        sucher_grenzen(t)
        fremde_bereiche(t)
        fahrerdaten(t)
        marktplatz_regeln(t, w, db)
        fotogrenze(t, w)
        kaeufer_sicht(t, w, db)
        laufzeit(w, db)
        einzelsitzung(w, t)
    finally:
        if not args.behalten:
            try:
                abbau(w, db)
                print("\nTestdaten entfernt.")
            except Exception as exc:  # noqa: BLE001
                print(f"\nWARNUNG: Aufraeumen unvollstaendig ({exc}) — Kennung {SUF}")
        else:
            print(f"\nTestdaten BLEIBEN stehen (Kennung {SUF}).")

    print(f"\nERGEBNIS: {_ok} wie erwartet, {_fehler} Abweichungen")
    for a in _abweichungen:
        print(f"  - {a}")
    return 1 if _fehler else 0


if __name__ == "__main__":
    sys.exit(main())
