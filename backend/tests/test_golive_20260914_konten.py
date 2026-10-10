# -*- coding: utf-8 -*-
"""Go-Live 14.09.2026 — Bereich "konten" (Firmenloeschung) und B5-Code-Punkt.

  B6  Firmenloeschung trotz Grabstein nicht wiederaufnehmbar: users stand als
      letzter Eintrag in _COMPANY_COLLECTIONS und wurde VOR Snapshots, Dateien
      und dealers.delete_many geloescht. Brach einer dieser Schritte ab (Mongo-
      Wahl, OOM), lief der erneute Aufruf ueber die Chef-ID auf 404 — Dateien,
      Firmenprofil und nicht pseudonymisierte Snapshots blieben liegen. Waehrend
      der Kaskade arbeiteten die Konten der Firma weiter (Grabstein wertet
      niemand aus). Das Schluss-Audit (Firma und Einzelnutzer) warf nach der
      vollstaendigen Loeschung einen 500, dessen Wiederholung 404 lieferte.
      Jetzt: (a) Konten sofort gesperrt, (b) users als letzter Schritt nach
      dealers, (c) Schluss-Audits per log_activity_sicher.
  B5  (Punkt 3) Unique-Index kaufvorgaenge(contract_id): /admin/betrieb zeigt
      ihn als kaufvorgang_index_aktiv, /admin/betrieb/nachholen legt ihn an.

Einheitentests rufen die Routen direkt (motor ueber deps.db, kein Server).
routes.admin wird in den Testfunktionen importiert, nicht in einer Schleife.
"""
import asyncio
import itertools
import os
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "autoschnell_nla_konten")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
SUF = uuid.uuid4().hex[:8]
MAIL = "e2etest-mail.de"
# Kontonummern je Konto eindeutig: die Suite-DB traegt den Unique-Index
# kontonummer_eindeutig, zwei Testfirmen mit derselben Nummer scheitern dort.
_KONTO_NR = itertools.count(1)

# Super-Admin als Abhaengigkeit fuer direkte Routenaufrufe (kein DB-Konto noetig)
SA = {"id": f"gl14sa_{SUF}", "role": "admin", "is_super_admin": True,
      "active": True, "dealer_id": None, "username": f"gl14sa_{SUF}"}


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


@pytest.fixture(scope="module")
def aufraeumen():
    """Alles, was die Tests dieser Datei anlegen, traegt SUF im Wert."""
    yield
    dbx = _db()
    muster = {"$regex": SUF}
    dbx.users.delete_many({"$or": [{"id": muster}, {"dealer_id": muster}]})
    dbx.dealers.delete_many({"id": muster})
    for coll in ("vehicles", "subscriptions", "listing_snapshots", "activity_logs",
                 "storage_delete_retry", "error_logs"):
        dbx[coll].delete_many({"$or": [{"id": muster}, {"dealer_id": muster},
                                       {"ref": muster}, {"prefix": muster},
                                       {"path": muster}]})


class _SammlungHaken:
    def __init__(self, echt, methoden):
        self._echt, self._methoden = echt, methoden

    def __getattr__(self, name):
        if name in self._methoden:
            return self._methoden[name]
        return getattr(self._echt, name)


class _Haken:
    """Motor-db, bei der einzelne (Sammlung, Methode) werfen."""

    def __init__(self, echt, kaputt):
        self._echt, self._kaputt = echt, kaputt

    def __getattr__(self, name):
        coll = getattr(self._echt, name)
        methoden = {m: _wirft for (c, m) in self._kaputt if c == name}
        return _SammlungHaken(coll, methoden) if methoden else coll

    def __getitem__(self, name):
        return getattr(self, name)


async def _wirft(*args, **kwargs):
    raise RuntimeError("NotPrimary simuliert")


def _firma_anlegen(dbx, kennung):
    dealer_id = f"gl14_{kennung}_dealer_{SUF}"
    chef_id = f"gl14_{kennung}_chef_{SUF}"
    sucher_id = f"gl14_{kennung}_sucher_{SUF}"
    dbx.dealers.insert_one({"id": dealer_id, "user_id": chef_id,
                            "company_name": f"GL14 {kennung} {SUF}",
                            "created_at": "2026-09-01T00:00:00+00:00"})
    dbx.users.insert_many([
        {"id": chef_id, "role": "dealer", "active": True, "dealer_id": dealer_id,
         "kontonummer": f"9{SUF[:5]}{next(_KONTO_NR)}", "current_session_id": "sitz-chef",
         "password_hash": "x", "email": f"gl14_{kennung}_chef_{SUF}@{MAIL}",
         "created_at": "2026-09-01T00:00:00+00:00"},
        {"id": sucher_id, "role": "sucher", "active": True, "dealer_id": dealer_id,
         "kontonummer": f"9{SUF[:5]}{next(_KONTO_NR)}", "current_session_id": "sitz-sucher",
         "password_hash": "x", "email": f"gl14_{kennung}_sucher_{SUF}@{MAIL}",
         "created_at": "2026-09-02T00:00:00+00:00"},
    ])
    dbx.vehicles.insert_one({"id": f"gl14_{kennung}_v_{SUF}", "dealer_id": dealer_id,
                             "owner_user_id": sucher_id, "data": {},
                             "created_at": "2026-09-02T00:00:00+00:00"})
    dbx.listing_snapshots.insert_one({"id": f"gl14_{kennung}_snap_{SUF}",
                                      "dealer_id": dealer_id, "user_id": sucher_id,
                                      "created_at": "2026-09-02T00:00:00+00:00"})
    return dealer_id, chef_id, sucher_id


def _stufe_snapshots(monkeypatch, a):
    import snapshot_service
    monkeypatch.setattr(snapshot_service, "snapshots_pseudonymisieren", _wirft)


def _stufe_dateien(monkeypatch, a):
    # delete_prefix wirft (wird abgefangen), danach scheitert das Einplanen
    # der Wiederholung an der DB — die Kaskade bricht im Dateischritt ab.
    from storage_service import storage

    def kaputt(prefix):
        raise OSError("Storage nicht erreichbar")
    monkeypatch.setattr(storage, "delete_prefix", kaputt)
    monkeypatch.setattr(a, "db", _Haken(a.db, {("storage_delete_retry", "update_one")}))


def _stufe_dealers(monkeypatch, a):
    monkeypatch.setattr(a, "db", _Haken(a.db, {("dealers", "delete_many")}))


def _stufe_users(monkeypatch, a):
    monkeypatch.setattr(a, "db", _Haken(a.db, {("users", "delete_many")}))


@pytest.mark.parametrize("stufe", [_stufe_snapshots, _stufe_dateien,
                                   _stufe_dealers, _stufe_users],
                         ids=["snapshots", "dateien", "dealers", "users"])
def test_b6_firmenloeschung_nach_abbruch_gesperrt_und_wiederaufnehmbar(
        aufraeumen, monkeypatch, stufe):
    import deps
    import routes.admin as a
    dbx = _db()
    kennung = stufe.__name__.replace("_stufe_", "")
    dealer_id, chef_id, sucher_id = _firma_anlegen(dbx, kennung)

    vorschau = asyncio.run(a.admin_delete_preview(dealer_id, admin=SA))
    assert vorschau["wuerde_loeschen"].get("users") == 2, vorschau
    assert vorschau["wuerde_loeschen"].get("vehicles") == 1, vorschau

    with monkeypatch.context() as m:
        stufe(m, a)
        with pytest.raises(RuntimeError):
            asyncio.run(a.admin_delete_user(chef_id, firma_loeschen=True, admin=SA))

    # Nach dem Abbruch: Chef existiert noch (Wiederaufnahme ueber die Chef-ID),
    # alle Konten der Firma sind gesperrt und abgemeldet.
    assert dbx.users.find_one({"id": chef_id}) is not None, \
        "Chef-Konto vor dem Ende der Kaskade geloescht — Wiederholung liefe auf 404"
    for konto in dbx.users.find({"dealer_id": dealer_id}):
        assert konto.get("active") is False and konto.get("current_session_id") is None, konto
    assert asyncio.run(deps.firma_gesperrt(dealer_id)) is True

    # Wiederholung fuehrt die Loeschung zu Ende.
    erg = asyncio.run(a.admin_delete_user(chef_id, firma_loeschen=True, admin=SA))
    assert erg["ok"] is True, erg
    assert erg["geloescht"].get("users") == 2, erg
    assert dbx.users.count_documents({"dealer_id": dealer_id}) == 0
    assert dbx.dealers.count_documents({"id": dealer_id}) == 0
    assert dbx.vehicles.count_documents({"dealer_id": dealer_id}) == 0
    snap = dbx.listing_snapshots.find_one({"id": f"gl14_{kennung}_snap_{SUF}"})
    assert snap and snap["dealer_id"] != dealer_id and snap["user_id"] != sucher_id, snap
    eintrag = dbx.activity_logs.find_one({"action": "admin.firma.geloescht", "ref": dealer_id})
    assert eintrag and eintrag["meta"]["wiederaufnahme"] is True, eintrag
    # Ein dritter Aufruf findet nichts mehr (alles weg).
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        asyncio.run(a.admin_delete_user(chef_id, firma_loeschen=True, admin=SA))
    assert e.value.status_code == 404


def test_b6_schluss_audit_fehler_liefert_ok_statt_500(aufraeumen, monkeypatch):
    import deps
    import routes.admin as a
    dbx = _db()
    echt = deps.log_activity

    async def audit(dealer_id, user_id, action, ref=None, meta=None):
        if action in ("admin.firma.geloescht", "admin.user.geloescht"):
            raise RuntimeError("Audit-Schreiben gescheitert")
        return await echt(dealer_id, user_id, action, ref=ref, meta=meta)
    monkeypatch.setattr(deps, "log_activity", audit)
    monkeypatch.setattr(a, "log_activity", audit)

    # Einzelnutzer (Sucher einer anderen Firma)
    _d2, _c2, sucher2 = _firma_anlegen(dbx, "audit_einzel")
    erg = asyncio.run(a.admin_delete_user(sucher2, admin=SA))
    assert erg == {"ok": True, "geloescht": "nur_nutzer"}
    assert dbx.users.count_documents({"id": sucher2}) == 0

    # Firma
    dealer_id, chef_id, _s = _firma_anlegen(dbx, "audit_firma")
    erg = asyncio.run(a.admin_delete_user(chef_id, firma_loeschen=True, admin=SA))
    assert erg["ok"] is True and erg["geloescht"].get("users") == 2, erg
    assert dbx.users.count_documents({"dealer_id": dealer_id}) == 0
    assert dbx.dealers.count_documents({"id": dealer_id}) == 0
    # Das Start-Audit (werfende Variante) steht weiterhin
    assert dbx.activity_logs.count_documents(
        {"action": "admin.firma.loeschung.gestartet", "ref": dealer_id}) == 1


def test_b5_betrieb_zeigt_und_holt_kaufvorgang_index_nach(monkeypatch):
    """Nach einem drop()-Reset fehlt der Unique-Index kaufvorgaenge(contract_id)
    bis zum naechsten Neustart. Die Betrieb-Seite muss das zeigen, "Nachholen"
    muss ihn ohne Neustart anlegen."""
    import routes.admin as a
    dbx = _db()

    def unique_contract_index():
        return [n for n, i in dbx.kaufvorgaenge.index_information().items()
                if i.get("unique") and [f for f, _r in i["key"]] == ["contract_id"]]

    async def nichts(*args, **kwargs):
        return {"uebersprungen": True}
    # Zahlungs-/Abo-Abgleich gehoert nicht zu diesem Befund
    monkeypatch.setattr(a, "abo_vorgaenge_nachholen", nichts)

    for name in unique_contract_index():
        dbx.kaufvorgaenge.drop_index(name)
    try:
        status = asyncio.run(a.admin_betrieb(admin=SA))
        assert status.get("kaufvorgang_index_aktiv") is False, \
            "Betrieb-Seite zeigt den fehlenden Kaufvorgang-Index nicht"
        erg = asyncio.run(a.admin_betrieb_nachholen(admin=SA))
        assert erg.get("kaufvorgang_index") is True, erg
        assert unique_contract_index(), "Nachholen legt kaufvorgaenge(contract_id) nicht an"
        status = asyncio.run(a.admin_betrieb(admin=SA))
        assert status.get("kaufvorgang_index_aktiv") is True
    finally:
        if not unique_contract_index():
            try:
                dbx.kaufvorgaenge.create_index("contract_id", unique=True)
            except Exception:  # noqa: BLE001 — Dubletten: ensure_indexes meldet es
                pass
