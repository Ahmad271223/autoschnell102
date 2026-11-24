# -*- coding: utf-8 -*-
"""Chat Buero <-> Fahrer je Abholtermin (Wunsch Ahmad 24.11.2026): routes/chat.py + auftrag_chat.py.

HTTP-Tests gegen ein laufendes Backend (TEST_BASE_URL, MOCK_PROVIDER_FETCH=true) auf derselben Mongo
(DB_NAME) — Konten, Firmen und Termine werden direkt in der Datenbank angelegt, Tokens per
auth.create_token (gleiches JWT_SECRET wie das Backend). Dazu In-Prozess-Tests der Loesch-, Meldungs-
und Limiter-Logik mit eigenem Motor-Client (importiert nie server.py).

Geprueft: Rechte (fremde Firma, fremder Fahrer, Sucher ohne Zugriff, abgelehnte Fahrt), Sichtfrist,
gelesen_am, Zaehler, Laengengrenze, Foto, Rate-Limit-Objekt, Meldung hoechstens einmal je Stunde,
Loeschung (Frist, Termin weg, Vertragsloeschung), Schnellantworten, PDF.
"""
import asyncio
import base64
import io
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auftrag_chat as AC  # noqa: E402
# Module am Dateianfang laden — nie erstmals in einer Test-Schleife (Loop-Bindung, siehe Testsetup).
import deps as DEPS  # noqa: E402
import routes.chat as CH  # noqa: E402

BASE = (os.environ.get("TEST_BASE_URL") or "http://127.0.0.1:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"

_client = None


def _db():
    global _client
    if _client is None:
        from pymongo import MongoClient
        _client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return _client[DB_NAME]


def _jetzt(delta_tage: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=delta_tage)).isoformat()


def _token_user(uid):
    from auth import create_token
    sid = uuid.uuid4().hex
    _db().users.update_one({"id": uid}, {"$set": {"current_session_id": sid}})
    return {"Authorization": f"Bearer {create_token(uid, sid)}"}


def _token_fahrer(fid):
    import jwt
    from auth import JWT_ALG, JWT_SECRET
    sid = uuid.uuid4().hex
    _db().driver_accounts.update_one({"id": fid}, {"$set": {"current_session_id": sid}})
    tok = jwt.encode({"sub": fid, "sid": sid, "role": "driver_account",
                      "exp": datetime.now(timezone.utc) + timedelta(days=1),
                      "seit": int(datetime.now(timezone.utc).timestamp())}, JWT_SECRET, algorithm=JWT_ALG)
    return {"Authorization": f"Bearer {tok}"}


def _foto_b64():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 30, 30)).save(buf, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class Welt:
    """Zwei Firmen; Firma A mit Chef, zwei Suchern (S1 hat den Termin angelegt, S2 nicht), zwei
    Fahrern (F1 zugeteilt, F2 in der Fahrerliste, nicht zugeteilt); Firma B mit Chef."""

    def __init__(self):
        s = uuid.uuid4().hex[:10]
        self.s = s
        db = _db()
        self.firma, self.firma_b = f"d_chat_{s}", f"db_chat_{s}"
        self.chef, self.s1, self.s2, self.chef_b = (f"u_chef_{s}", f"u_s1_{s}", f"u_s2_{s}", f"u_chefb_{s}")
        self.f1, self.f2 = f"f1_chat_{s}", f"f2_chat_{s}"
        jetzt = _jetzt()
        db.dealers.insert_many([
            {"id": self.firma, "user_id": self.chef, "company_name": f"Chat Firma {s}", "created_at": jetzt},
            {"id": self.firma_b, "user_id": self.chef_b, "company_name": f"Andere {s}", "created_at": jetzt}])
        db.users.insert_many([
            {"id": self.chef, "dealer_id": self.firma, "role": "dealer", "active": True,
             "first_name": "Carla", "created_at": jetzt, "email": f"chef-{s}@example.test"},
            {"id": self.s1, "dealer_id": self.firma, "role": "sucher", "active": True,
             "first_name": "Sven", "created_at": jetzt, "email": f"s1-{s}@example.test"},
            {"id": self.s2, "dealer_id": self.firma, "role": "sucher", "active": True,
             "first_name": "Sina", "created_at": jetzt, "email": f"s2-{s}@example.test"},
            {"id": self.chef_b, "dealer_id": self.firma_b, "role": "dealer", "active": True,
             "created_at": jetzt, "email": f"chefb-{s}@example.test"}])
        db.driver_accounts.insert_many([
            {"id": self.f1, "display_name": f"Fahrer Eins {s}", "active": True, "created_at": jetzt,
             "driver_code": f"FC1-{s.upper()}"},
            {"id": self.f2, "display_name": f"Fahrer Zwei {s}", "active": True, "created_at": jetzt,
             "driver_code": f"FC2-{s.upper()}"}])
        db.dealer_drivers.insert_many([
            {"id": uuid.uuid4().hex, "dealer_id": self.firma, "driver_account_id": self.f1, "added_at": jetzt},
            {"id": uuid.uuid4().hex, "dealer_id": self.firma, "driver_account_id": self.f2, "added_at": jetzt}])
        self.appt = self.termin()

    def termin(self, **extra):
        aid = f"a_chat_{uuid.uuid4().hex[:10]}"
        doc = {"id": aid, "dealer_id": self.firma, "driver_id": self.f1, "zuteilung": "angenommen",
               "status": "offen", "title": "Golf abholen", "pickup_date": "2099-03-04",
               "pickup_time": "10:00", "created_by": self.s1, "created_at": _jetzt(),
               "updated_at": _jetzt()}
        doc.update(extra)
        _db().appointments.insert_one(doc)
        return aid

    def aufraeumen(self):
        db = _db()
        firmen = [self.firma, self.firma_b]
        termine = db.appointments.distinct("id", {"dealer_id": {"$in": firmen}})
        db.auftrag_chat.delete_many({"$or": [{"dealer_id": {"$in": firmen}},
                                             {"appointment_id": {"$in": termine}}]})
        db.auftrag_chat_status.delete_many({"appointment_id": {"$in": termine}})
        db.meldungen.delete_many({"dealer_id": {"$in": firmen}})
        db.appointments.delete_many({"dealer_id": {"$in": firmen}})
        db.dealer_drivers.delete_many({"dealer_id": {"$in": firmen}})
        db.driver_accounts.delete_many({"id": {"$in": [self.f1, self.f2]}})
        db.users.delete_many({"id": {"$in": [self.chef, self.s1, self.s2, self.chef_b]}})
        db.dealers.delete_many({"id": {"$in": firmen}})
        db.activity_logs.delete_many({"dealer_id": {"$in": firmen}})
        db.generated_pdfs.delete_many({"dealer_id": {"$in": firmen}})


@pytest.fixture
def w():
    welt = Welt()
    welt.H = {"chef": _token_user(welt.chef), "s1": _token_user(welt.s1), "s2": _token_user(welt.s2),
              "chef_b": _token_user(welt.chef_b), "f1": _token_fahrer(welt.f1), "f2": _token_fahrer(welt.f2)}
    try:
        yield welt
    finally:
        welt.aufraeumen()


def _buero(w, wer, aid, **kw):
    return requests.post(f"{API}/appointments/{aid}/chat", headers=w.H[wer], json=kw, timeout=30)


def _fahrer(w, wer, aid, **kw):
    return requests.post(f"{API}/driver/appointments/{aid}/chat", headers=w.H[wer], json=kw, timeout=30)


# ============================================================ Grundablauf, gelesen, Zaehler
def test_01_verlauf_gelesen_und_zaehler(w):
    r = _buero(w, "chef", w.appt, text="Melde mich gleich", schnell=True)
    assert r.status_code == 200, r.text
    assert r.json()["eigene"] and r.json()["schnell"] and r.json()["gelesen_am"] is None
    # Fahrer: Zaehler 1, dann Chat oeffnen -> gelesen
    z = requests.get(f"{API}/driver/chat/ungelesen", headers=w.H["f1"], timeout=30).json()
    assert z["termine"] == {w.appt: 1} and z["gesamt"] == 1
    v = requests.get(f"{API}/driver/appointments/{w.appt}/chat", headers=w.H["f1"], timeout=30).json()
    assert [m["text"] for m in v["nachrichten"]] == ["Melde mich gleich"]
    assert v["nachrichten"][0]["eigene"] is False and v["nachrichten"][0]["von_name"] == "Carla"
    assert "Bin vor Ort" in v["schnellantworten"]
    assert requests.get(f"{API}/driver/chat/ungelesen", headers=w.H["f1"], timeout=30).json()["gesamt"] == 0
    # Buero sieht "gelesen" unter der eigenen Nachricht
    b = requests.get(f"{API}/appointments/{w.appt}/chat", headers=w.H["chef"], timeout=30).json()
    assert b["nachrichten"][0]["gelesen_am"], "Gelesen-Haken fehlt"
    assert b["fahrer"]["name"].startswith("Fahrer Eins") and b["kann_schreiben"] is True
    assert "Bitte Foto schicken" in b["schnellantworten"]
    # Antwort des Fahrers: Zaehler im Buero (Chef und Sucher S1), nicht bei S2 (kein Zugriff)
    assert _fahrer(w, "f1", w.appt, text="Bin vor Ort", schnell=True).status_code == 200
    for wer, erwartet in (("chef", 1), ("s1", 1), ("s2", 0)):
        z = requests.get(f"{API}/chat/ungelesen", headers=w.H[wer], timeout=30).json()
        assert z["gesamt"] == erwartet, (wer, z)
    # lesen=false laesst ungelesen
    requests.get(f"{API}/appointments/{w.appt}/chat", params={"lesen": "false"}, headers=w.H["s1"], timeout=30)
    assert requests.get(f"{API}/chat/ungelesen", headers=w.H["chef"], timeout=30).json()["gesamt"] == 1
    requests.get(f"{API}/appointments/{w.appt}/chat", headers=w.H["s1"], timeout=30)
    assert requests.get(f"{API}/chat/ungelesen", headers=w.H["chef"], timeout=30).json()["gesamt"] == 0
    f = requests.get(f"{API}/driver/appointments/{w.appt}/chat", headers=w.H["f1"], timeout=30).json()
    assert f["nachrichten"][-1]["eigene"] and f["nachrichten"][-1]["gelesen_am"]
    # Schnell-Kennzeichen nur fuer echte Schnellantworten
    r = _fahrer(w, "f1", w.appt, text="frei getippt", schnell=True)
    assert r.json()["schnell"] is False


# ============================================================ Rechte
def test_02_rechte_fremde_firma_fremder_fahrer_sucher_ohne_zugriff(w):
    assert _buero(w, "chef", w.appt, text="Hallo").status_code == 200
    # fremde Firma
    assert requests.get(f"{API}/appointments/{w.appt}/chat", headers=w.H["chef_b"], timeout=30).status_code == 404
    assert _buero(w, "chef_b", w.appt, text="x").status_code == 404
    assert requests.get(f"{API}/chat/ungelesen", headers=w.H["chef_b"], timeout=30).json()["gesamt"] == 0
    # Sucher ohne Zugriff (Termin von S1)
    assert requests.get(f"{API}/appointments/{w.appt}/chat", headers=w.H["s2"], timeout=30).status_code == 404
    assert _buero(w, "s2", w.appt, text="x").status_code == 404
    # Sucher mit Zugriff
    assert _buero(w, "s1", w.appt, text="Von Sven").status_code == 200
    # fremder Fahrer (gleiche Firma, nicht zugeteilt)
    assert requests.get(f"{API}/driver/appointments/{w.appt}/chat", headers=w.H["f2"], timeout=30).status_code == 404
    assert _fahrer(w, "f2", w.appt, text="x").status_code == 404
    assert requests.get(f"{API}/driver/chat/ungelesen", headers=w.H["f2"], timeout=30).json()["gesamt"] == 0
    # Fahrer-Token gilt nicht fuer die Buero-Route und umgekehrt
    assert requests.get(f"{API}/appointments/{w.appt}/chat", headers=w.H["f1"], timeout=30).status_code in (401, 403)
    assert requests.get(f"{API}/driver/appointments/{w.appt}/chat", headers=w.H["chef"], timeout=30).status_code in (401, 403)
    # Fahrer aus der Fahrerliste entfernt -> kein Zugriff mehr
    _db().dealer_drivers.delete_one({"dealer_id": w.firma, "driver_account_id": w.f1})
    assert requests.get(f"{API}/driver/appointments/{w.appt}/chat", headers=w.H["f1"], timeout=30).status_code == 404
    assert requests.get(f"{API}/driver/chat/ungelesen", headers=w.H["f1"], timeout=30).json()["gesamt"] == 0
    b = requests.get(f"{API}/appointments/{w.appt}/chat", headers=w.H["chef"], timeout=30).json()
    assert b["kann_schreiben"] is False and b["hinweis"]
    assert _buero(w, "chef", w.appt, text="noch da?").status_code == 409


def test_03_neuer_fahrer_sieht_nicht_den_verlauf_des_vorgaengers(w):
    assert _fahrer(w, "f1", w.appt, text="Verkäufer nicht da").status_code == 200
    _db().appointments.update_one({"id": w.appt}, {"$set": {"driver_id": w.f2}})
    assert requests.get(f"{API}/driver/appointments/{w.appt}/chat", headers=w.H["f1"], timeout=30).status_code == 404
    v = requests.get(f"{API}/driver/appointments/{w.appt}/chat", headers=w.H["f2"], timeout=30).json()
    assert v["nachrichten"] == []
    assert _buero(w, "chef", w.appt, text="Hallo neuer Fahrer").status_code == 200
    v = requests.get(f"{API}/driver/appointments/{w.appt}/chat", headers=w.H["f2"], timeout=30).json()
    assert [m["text"] for m in v["nachrichten"]] == ["Hallo neuer Fahrer"]
    # Buero sieht beide Gespraeche
    b = requests.get(f"{API}/appointments/{w.appt}/chat", headers=w.H["chef"], timeout=30).json()
    assert len(b["nachrichten"]) == 2


def test_04_abgelehnt_und_ohne_fahrer(w):
    abg = w.termin(zuteilung="abgelehnt")
    assert requests.get(f"{API}/driver/appointments/{abg}/chat", headers=w.H["f1"], timeout=30).status_code == 404
    assert _buero(w, "chef", abg, text="x").status_code == 409
    ohne = w.termin(driver_id=None)
    r = requests.get(f"{API}/appointments/{ohne}/chat", headers=w.H["chef"], timeout=30)
    assert r.status_code == 200 and r.json()["kann_schreiben"] is False and "kein Fahrer" in r.json()["hinweis"]
    assert _buero(w, "chef", ohne, text="x").status_code == 409
    # Zuteilung noch offen (Fahrt angeboten): Chat geht schon
    angeboten = w.termin(zuteilung="offen")
    assert _buero(w, "chef", angeboten, text="Kannst du morgen?").status_code == 200
    assert _fahrer(w, "f1", angeboten, text="Ja").status_code == 200


# ============================================================ Sichtfrist
def test_05_sichtfrist_des_fahrers(w):
    frisch = w.termin(status="abgeholt", status_changed_at=_jetzt(-3), abgeschlossen_seit=_jetzt(-3))
    alt = w.termin(status="abgeholt", status_changed_at=_jetzt(-20), abgeschlossen_seit=_jetzt(-20))
    storno = w.termin(status="storniert", status_changed_at=_jetzt(-1))
    assert _fahrer(w, "f1", frisch, text="Fahrzeug steht im Hof").status_code == 200
    for aid in (alt, storno):
        assert requests.get(f"{API}/driver/appointments/{aid}/chat", headers=w.H["f1"], timeout=30).status_code == 404
        assert _fahrer(w, "f1", aid, text="x").status_code == 404
        assert _buero(w, "chef", aid, text="x").status_code == 409
        b = requests.get(f"{API}/appointments/{aid}/chat", headers=w.H["chef"], timeout=30)
        assert b.status_code == 200 and b.json()["kann_schreiben"] is False
    # Ungelesenes aus einer abgelaufenen Fahrt zaehlt beim Fahrer nicht mit
    _db().auftrag_chat.insert_one(AC.nachricht_neu(appointment_id=alt, dealer_id=w.firma, fahrer_id=w.f1,
                                                   von="buero", von_id=w.chef, von_name="Carla", text="alt"))
    assert alt not in requests.get(f"{API}/driver/chat/ungelesen", headers=w.H["f1"], timeout=30).json()["termine"]


# ============================================================ Grenzen
def test_06_laengengrenze_und_leer(w):
    assert _buero(w, "chef", w.appt, text="x" * AC.TEXT_MAX).status_code == 200
    r = _buero(w, "chef", w.appt, text="x" * (AC.TEXT_MAX + 1))
    assert r.status_code == 422 and "zu lang" in r.text
    assert _buero(w, "chef", w.appt, text="   ").status_code == 422
    assert _fahrer(w, "f1", w.appt, text="").status_code == 422
    assert _fahrer(w, "f1", w.appt, text="y" * (AC.TEXT_MAX + 300)).status_code == 422
    # Steuerzeichen fallen weg, Zeilenumbruch bleibt
    r = _fahrer(w, "f1", w.appt, text="a\x00b\nc")
    assert r.status_code == 200 and r.json()["text"] == "ab\nc"


def test_07_text_pruefen_direkt():
    assert AC.text_pruefen("  hallo  ") == "hallo"
    assert AC.text_pruefen("", mit_foto=True) == ""
    with pytest.raises(ValueError):
        AC.text_pruefen("")
    with pytest.raises(ValueError):
        AC.text_pruefen("z" * (AC.TEXT_MAX + 1))


def test_08_rate_limit_objekt():
    """Die Drossel greift je Konto (20/min). Ueber HTTP waere sie sichtbar (Schluessel ist kein
    Loopback) — der Test prueft das Objekt direkt mit einem frischen Schluessel."""
    assert CH.chat_limiter.max_attempts == 20 and CH.chat_limiter.window_seconds == 60
    schluessel = f"fahrer:test-{uuid.uuid4().hex}"

    async def lauf():
        from motor.motor_asyncio import AsyncIOMotorClient
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        alt = DEPS.db
        DEPS.db = cl[DB_NAME]
        try:
            return [await CH.chat_limiter.check(schluessel) for _ in range(21)]
        finally:
            DEPS.db = alt            # Zaehler-Dokumente raeumt der TTL-Index von rate_limits
            cl.close()
    erg = asyncio.run(lauf())
    assert all(erg[:20]) and erg[20] is False, erg


# ============================================================ Foto
def test_09_foto_senden_und_laden(w):
    r = _fahrer(w, "f1", w.appt, text="", foto_b64=_foto_b64())
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    assert r.json()["foto"] is True
    doc = _db().auftrag_chat.find_one({"id": mid})
    assert doc["foto_key"].startswith(f"protocol/{w.firma}/")
    assert "foto_key" not in r.json()
    g = requests.get(f"{API}/appointments/{w.appt}/chat/fotos/{mid}", headers=w.H["chef"], timeout=30)
    assert g.status_code == 200 and g.headers["content-type"].startswith("image/")
    assert "no-store" in g.headers.get("cache-control", "")
    assert requests.get(f"{API}/driver/appointments/{w.appt}/chat/fotos/{mid}",
                        headers=w.H["f1"], timeout=30).status_code == 200
    assert requests.get(f"{API}/appointments/{w.appt}/chat/fotos/{mid}",
                        headers=w.H["chef_b"], timeout=30).status_code == 404
    assert requests.get(f"{API}/appointments/{w.appt}/chat/fotos/{mid}",
                        headers=w.H["s2"], timeout=30).status_code == 404
    kaputt = _fahrer(w, "f1", w.appt, text="", foto_b64="data:image/jpeg;base64," + base64.b64encode(b"keinbild").decode())
    assert kaputt.status_code == 400


# ============================================================ Meldung
def test_10_meldung_hoechstens_einmal_je_stunde(w):
    for t in ("Bin vor Ort", "Verkäufer nicht da", "Bitte zurückrufen"):
        assert _fahrer(w, "f1", w.appt, text=t).status_code == 200
    ms = list(_db().meldungen.find({"dealer_id": w.firma, "typ": AC.MELDUNG_TYP}))
    assert len(ms) == 1, ms
    assert set(ms[0]["empfaenger"]) == {w.chef, w.s1}, "Chef + Sucher des Termins, nicht S2"
    assert ms[0]["ref"] == w.appt and "Fahrer Eins" in ms[0]["text"]
    assert "Bin vor Ort" not in ms[0]["text"], "kein Nachrichtentext in der Meldung"
    # Buero-Nachrichten erzeugen keine Meldung
    _buero(w, "chef", w.appt, text="Passt so")
    assert _db().meldungen.count_documents({"dealer_id": w.firma, "typ": AC.MELDUNG_TYP}) == 1
    # Chat oeffnen markiert die Meldung des Kontos als gelesen
    requests.get(f"{API}/appointments/{w.appt}/chat", headers=w.H["chef"], timeout=30)
    m = _db().meldungen.find_one({"id": ms[0]["id"]})
    assert m["gelesen"].get(w.chef) and not m["gelesen"].get(w.s1)
    # eine Stunde spaeter: wieder eine
    _db().auftrag_chat_status.update_one({"appointment_id": w.appt},
                                         {"$set": {"meldung_am": _jetzt(-0.05)}})
    assert _fahrer(w, "f1", w.appt, text="Preis besprechen").status_code == 200
    assert _db().meldungen.count_documents({"dealer_id": w.firma, "typ": AC.MELDUNG_TYP}) == 2
    # zweites Gespraech (anderer Termin) bekommt seine eigene
    zweit = w.termin()
    assert _fahrer(w, "f1", zweit, text="Bin vor Ort").status_code == 200
    assert _db().meldungen.count_documents({"dealer_id": w.firma, "typ": AC.MELDUNG_TYP}) == 3


def _motor(fn):
    async def inner():
        from motor.motor_asyncio import AsyncIOMotorClient
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            return await fn(cl[DB_NAME])
        finally:
            cl.close()
    return asyncio.run(inner())


def test_11_meldung_beanspruchen_atomar(w):
    aid = w.termin()

    async def lauf(db):
        jetzt = datetime.now(timezone.utc)
        erg = await asyncio.gather(*[AC.meldung_beanspruchen(db, aid, w.firma, jetzt) for _ in range(8)])
        spaeter = await AC.meldung_beanspruchen(db, aid, w.firma, jetzt + timedelta(minutes=30))
        neu = await AC.meldung_beanspruchen(db, aid, w.firma, jetzt + timedelta(minutes=61))
        return erg, spaeter, neu
    erg, spaeter, neu = _motor(lauf)
    assert sum(1 for e in erg if e) == 1, erg
    assert spaeter is False and neu is True


# ============================================================ Loeschung
def test_12_loeschung_termin_weg_pii_und_frist(w):
    weg = w.termin()
    pii = w.termin()
    alt = w.termin()
    bleibt = w.termin()
    db = _db()
    for aid in (weg, pii, alt, bleibt):
        db.auftrag_chat.insert_one(AC.nachricht_neu(appointment_id=aid, dealer_id=w.firma, fahrer_id=w.f1,
                                                    von="fahrer", von_id=w.f1, von_name="F", text=aid))
    db.auftrag_chat_status.insert_one({"appointment_id": weg, "dealer_id": w.firma, "meldung_am": _jetzt()})
    db.meldungen.insert_one({"id": uuid.uuid4().hex, "dealer_id": w.firma, "empfaenger": [w.chef],
                             "typ": AC.MELDUNG_TYP, "ref": weg, "text": "x", "gelesen": {},
                             "erstellt_am": _jetzt()})
    db.appointments.delete_one({"id": weg})
    db.appointments.update_one({"id": pii}, {"$set": {"pii_geloescht_at": _jetzt()}})
    db.auftrag_chat.update_one({"appointment_id": alt},
                               {"$set": {"erstellt_am": _jetzt(-(AC.CHAT_AUFBEWAHRUNG_TAGE + 1))}})
    # ohne scharfe Frist: nur Termin weg / ohne Personendaten
    n = _motor(lambda d: AC.chat_nach_frist_loeschen(d, frist_aktiv=False))
    assert n >= 2
    assert db.auftrag_chat.count_documents({"appointment_id": {"$in": [weg, pii]}}) == 0
    assert db.auftrag_chat.count_documents({"appointment_id": alt}) == 1
    assert db.auftrag_chat_status.count_documents({"appointment_id": weg}) == 0
    assert db.meldungen.count_documents({"ref": weg}) == 0
    # scharf: die alte Nachricht geht, die junge bleibt
    _motor(lambda d: AC.chat_nach_frist_loeschen(d, frist_aktiv=True))
    assert db.auftrag_chat.count_documents({"appointment_id": alt}) == 0
    assert db.auftrag_chat.count_documents({"appointment_id": bleibt}) == 1


def test_13_loeschung_mit_foto(w):
    r = _fahrer(w, "f1", w.appt, text="mit Bild", foto_b64=_foto_b64())
    assert r.status_code == 200
    key = _db().auftrag_chat.find_one({"id": r.json()["id"]})["foto_key"]
    n = _motor(lambda d: AC.chat_loeschen(d, [w.appt], "test"))
    assert n == 1 and _db().auftrag_chat.count_documents({"appointment_id": w.appt}) == 0
    # Datei geloescht (oder zur Nachholung vorgemerkt) — nie still liegen gelassen
    vorgemerkt = _db().storage_delete_retry.count_documents({"key": key})
    try:
        from storage_service import storage
        noch_da = True
        storage.load(key)
    except Exception:  # noqa: BLE001
        noch_da = False
    assert not noch_da or vorgemerkt, "Chatfoto blieb ohne Vormerkung liegen"


def test_14_verdrahtung_im_quelltext():
    """Aufraeumlauf, Vertragsloeschung und Firmenloeschung kennen den Chat (Quelltext statt Ausfuehrung:
    keine Loop-Bindung durch routes.admin/cleanup_service in der Testschleife)."""
    backend = Path(__file__).resolve().parents[1]
    cs = (backend / "cleanup_service.py").read_text(encoding="utf-8")
    assert 'chat_nach_frist_loeschen(db, now)' in cs
    assert 'chat_loeschen(db, termin_ids, "vertrag_geloescht")' in cs
    adm = (backend / "routes" / "admin.py").read_text(encoding="utf-8")
    assert '"auftrag_chat", "auftrag_chat_status"' in adm
    srv = (backend / "server.py").read_text(encoding="utf-8")
    assert "include_router(chat_routes.router)" in srv and "await chat_indizes(db)" in srv


# ============================================================ PDF
def test_15_pdf_nachrichten_buero_fahrer(w):
    _buero(w, "chef", w.appt, text="Bitte Foto schicken")
    _fahrer(w, "f1", w.appt, text="Schlüssel liegt beim Nachbarn")
    r = requests.get(f"{API}/appointments/{w.appt}/chat.pdf", headers=w.H["chef"], timeout=60)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    from pypdf import PdfReader
    text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages)
    assert "Nachrichten Büro/Fahrer" in text and "Schlüssel liegt beim Nachbarn" in text
    assert requests.get(f"{API}/appointments/{w.appt}/chat.pdf", headers=w.H["s2"], timeout=30).status_code == 404
    assert requests.get(f"{API}/appointments/{w.appt}/chat.pdf", headers=w.H["chef_b"], timeout=30).status_code == 404
