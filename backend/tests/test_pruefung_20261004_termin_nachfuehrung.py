# -*- coding: utf-8 -*-
"""Pruefung 04.10.2026 (Nr. 43): Nach einer neuen Vertragsfassung (nachtraegliche Aenderung oder
Verkaeuferkorrektur) ziehen die offenen Termine Name, Telefon, E-Mail und Abholadresse mit. Scheiterte
das, stand vorher nur eine Zeile im Log — der Fahrer sah weiter den alten Verkaeufer, niemand zog nach.
Jetzt: Merker termin_nachfuehrung_offen + Betriebsalarm, der Aufraeumlauf holt es nach.
Dazu: auch die nachtraegliche Aenderung setzt bei neuem Namen den Zeitpunkt am Termin und leert den
alten Namen im Protokoll-Entwurf (wie die Verkaeuferkorrektur, RP-082).

In-Prozess mit der Wegwerf-Welt aus test_befunde_runde17_termine."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _jetzt, _module, welt  # noqa: E402,F401
from test_vertrag_nachtraeglich_20261001 import _auto_daten_aus, _eingaben, _vertrag  # noqa: E402


def _termine(w, db, cid, offen, zu):
    return db.appointments.insert_many([
        {"id": offen, "dealer_id": w.dealer_id, "created_by": w.chef["id"], "contract_id": cid,
         "status": "offen", "seller_name": "Vera Verkauf", "seller_phone": "0170 1111111",
         "pickup_address": "Alte Straße 1 10115 Berlin", "created_at": _jetzt()},
        {"id": zu, "dealer_id": w.dealer_id, "created_by": w.chef["id"], "contract_id": cid,
         "status": "abgeholt", "seller_name": "Vera Verkauf", "created_at": _jetzt()}])


def test_43_scheitert_das_nachziehen_holt_der_aufraeumlauf_es_nach(welt, monkeypatch):
    C = _module("routes.contracts")
    CL = _module("cleanup_service")
    w, db = welt.w, welt.db
    _auto_daten_aus(monkeypatch)
    cid, offen, zu = f"ctn_{w.s}", f"a_tn_offen_{w.s}", f"a_tn_zu_{w.s}"
    echt = CL.termine_aus_vertrag_nachziehen

    async def _kaputt(*a, **k):
        raise RuntimeError("Datenbank wackelt")

    async def lauf():
        await db.generated_pdfs.insert_one(_vertrag(w, cid))
        await _termine(w, db, cid, offen, zu)
        await db.pickup_protocols.insert_one({"id": f"p_tn_{w.s}", "appointment_id": offen,
                                              "dealer_id": w.dealer_id, "status": "entwurf",
                                              "seller_name": "Vera Verkauf"})
        monkeypatch.setattr(CL, "termine_aus_vertrag_nachziehen", _kaputt)
        antwort = await C.vertrag_nachtraeglich_aendern(cid, _eingaben(C), user=w.chef)
        doc = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        vorher = await db.appointments.find_one({"id": offen}, {"_id": 0})
        alarm = await db.betriebsalarme.find_one({"typ": "termin_nachfuehrung_offen", "ref": cid}, {"_id": 0})
        monkeypatch.setattr(CL, "termine_aus_vertrag_nachziehen", echt)
        n = await CL.termin_nachfuehrung_nachholen(db)
        nachher = await db.appointments.find_one({"id": offen}, {"_id": 0})
        zu_doc = await db.appointments.find_one({"id": zu}, {"_id": 0})
        doc2 = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        entwurf = await db.pickup_protocols.find_one({"id": f"p_tn_{w.s}"}, {"_id": 0})
        alarm2 = await db.betriebsalarme.find_one({"typ": "termin_nachfuehrung_offen", "ref": cid}, {"_id": 0})
        await db.betriebsalarme.delete_many({"typ": "termin_nachfuehrung_offen", "ref": cid})
        return antwort, doc, vorher, alarm, n, nachher, zu_doc, doc2, entwurf, alarm2

    antwort, doc, vorher, alarm, n, nachher, zu_doc, doc2, entwurf, alarm2 = welt.run(lauf())
    # die neue Fassung steht trotzdem — nur der Termin fehlt noch, mit Merker
    assert antwort["geaendert"] is True and antwort["termine_aktualisiert"] == 0
    assert doc["version"] == 2 and doc["termin_nachfuehrung_offen"] is True
    assert vorher["seller_name"] == "Vera Verkauf"
    assert alarm and alarm["offen"] is True, "Betriebsalarm statt nur Log"
    # der Aufraeumlauf zieht nach und nimmt den Merker weg
    assert n >= 1 and "termin_nachfuehrung_offen" not in doc2
    assert alarm2["offen"] is False, "Alarm nach dem Nachholen geschlossen"
    assert nachher["seller_name"] == "Vera Verkauf-Müller" and nachher["seller_phone"] == "0170 2222222"
    assert nachher["seller_email"] == "vera.neu@e2etest-mail.de"
    assert nachher["pickup_address"] == "Neue Allee 7 20095 Hamburg"
    assert nachher["seller_name_geaendert_am"], "Name geaendert -> Zeitpunkt am Termin"
    assert zu_doc["seller_name"] == "Vera Verkauf", "abgeschlossener Termin bleibt Beleg"
    assert "seller_name" not in entwurf, "Entwurf mit altem Namen nimmt beim Abschluss den neuen"


def test_43_normalfall_neue_fassung_setzt_namenszeitpunkt(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    _auto_daten_aus(monkeypatch)
    cid, offen, zu = f"ctm_{w.s}", f"a_tm_offen_{w.s}", f"a_tm_zu_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_one(_vertrag(w, cid))
        await _termine(w, db, cid, offen, zu)
        antwort = await C.vertrag_nachtraeglich_aendern(cid, _eingaben(C), user=w.chef)
        t = await db.appointments.find_one({"id": offen}, {"_id": 0})
        doc = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        return antwort, t, doc

    antwort, t, doc = welt.run(lauf())
    assert antwort["termine_aktualisiert"] == 1 and "termin_nachfuehrung_offen" not in doc
    assert t["seller_name"] == "Vera Verkauf-Müller" and t["seller_name_geaendert_am"]


def test_43_nachholer_laeuft_im_aufraeumlauf():
    import inspect
    CL = _module("cleanup_service")
    q = inspect.getsource(CL)
    assert 'await s("termin_nachfuehrung_nachgeholt", lambda: termin_nachfuehrung_nachholen(db))' in q
    C = _module("routes.contracts")
    for f in (C.verkaeufer_korrigieren, C.vertrag_nachtraeglich_aendern):
        quelle = inspect.getsource(f)
        assert "_cleanup.termine_aus_vertrag_nachziehen(" in quelle
        assert "_cleanup.termin_nachfuehrung_merken(" in quelle
