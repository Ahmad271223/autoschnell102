# -*- coding: utf-8 -*-
"""Go-Live 13.09.2026 — Bereich "preis": Der vor Ort nachverhandelte Preis
muss IMMER genau einmal im Kaufvorgang DIESES Termins landen.

  * P5   Scheiterte der Abschluss nach dem Protokoll-Write (Failover,
         Prozessende, Termin kurz geschlossen), zog die Selbstheilung Termin
         und Vorgangsstatus nach — den Preis aber nie. Einkaufspreis in Vorgang,
         Fahrzeug und Auswertung blieb der Vertragspreis.
  * N2   Dieselbe Luecke nach einem Abbruch VOR dem Termin-Write.
  * P5-Zusatz-Korrektur  Eine Korrektur-Version, in der der Chef den Preis
         auf den Vertragspreis zuruecksetzte, liess den Preis der Vorversion im
         Vorgang stehen (PDF 10.000, Vorgang 9.000).
  * P6   Ein Termin wurde geschlossen, waehrend das Protokoll beim Chef lag oder
         schon freigegeben war — nach dem Wiederoeffnen galt die alte Freigabe
         weiter; ein Abschicken im selben Moment landete am geschlossenen Termin.

Fachregel: dasselbe Auto darf beliebig viele Vertraege, Vorgaenge und Termine
haben; verhindert wird nur, dass sich EIN Abschluss mit einem anderen
Vertrag/Vorgang vermischt.

In-Prozess gegen DB_NAME (Fixture und Aufbau aus test_golive_20260913_abschluss);
Rennen deterministisch per Haken zwischen Lesen und Schreiben.
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_golive_20260913_abschluss import (  # noqa: E402,F401
    welt, _abholung, _doc, _fin, _m)


def _ohne_zeit(d):
    return {k: v for k, v in (d or {}).items() if k != "updated_at"}


def _stirbt_einmal(monkeypatch, P, name):
    """Die erste Ausfuehrung von P.<name> wirft (Prozessende/Failover)."""
    echt = getattr(P, name)
    n = {"c": 0}

    async def stirbt(*a, **k):
        n["c"] += 1
        if n["c"] == 1:
            raise RuntimeError(f"Abbruch in {name}")
        return await echt(*a, **k)
    monkeypatch.setattr(P, name, stirbt)


# ============================================================ P5 / N2
def test_p5_preis_update_scheitert_selbstheilung_holt_preis_nach(welt, monkeypatch):
    """Das Kaufvorgang-Update wirft einmal (rs0-Failover). Die App laedt nicht
    neu, der Fahrer tippt erneut -> Selbstheilung muss den Preis nachziehen."""
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    echt_db = P.db
    n = {"c": 0}

    class _Vorgaenge:
        def __getattr__(self, name):
            return getattr(echt_db.kaufvorgaenge, name)

        async def update_one(self, *a, **k):
            n["c"] += 1
            if n["c"] == 1:
                raise RuntimeError("Failover waehrend Kaufvorgang-Update")
            return await echt_db.kaufvorgaenge.update_one(*a, **k)

    class _DB:
        def __getattr__(self, name):
            return _Vorgaenge() if name == "kaufvorgaenge" else getattr(echt_db, name)

        def __getitem__(self, name):
            return self.__getattr__(name)
    monkeypatch.setattr(P, "db", _DB())
    with pytest.raises(RuntimeError):
        w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    monkeypatch.setattr(P, "db", echt_db)
    assert _doc(w, "pickup_protocols", t.pid)["status"] == "final"
    assert _doc(w, "kaufvorgaenge", t.ka)["purchase_price"] == 10000.0

    heil = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert heil.get("nachgezogen") is True and "hinweis" not in heil, heil
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["status"] == "abgeholt"
    assert kv["purchase_price"] == 9000.0, kv
    assert kv["preis_nachverhandelt"] is True and kv["preis_vorher"] == 10000.0
    assert kv["preis_protokoll_id"] == t.pid
    assert _doc(w, "vehicles", t.vid).get("purchase_price") == 9000.0
    # Vorgang B desselben Autos bleibt unberuehrt.
    assert _doc(w, "kaufvorgaenge", t.kb)["purchase_price"] == 20000.0

    # Idempotent: ein weiterer Heil-Aufruf aendert nichts mehr.
    w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert _ohne_zeit(_doc(w, "kaufvorgaenge", t.ka)) == _ohne_zeit(kv)


def test_n2_abbruch_vor_termin_write_selbstheilung_mit_preis(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    _stirbt_einmal(monkeypatch, P, "_termin_abgeholt_setzen")
    with pytest.raises(RuntimeError):
        w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert _doc(w, "pickup_protocols", t.pid)["status"] == "final"
    assert _doc(w, "appointments", t.aid)["status"] == "offen"

    heil = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert heil.get("nachgezogen") is True, heil
    assert _doc(w, "appointments", t.aid)["status"] == "abgeholt"
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["status"] == "abgeholt"
    assert kv["purchase_price"] == 9000.0 and kv["preis_nachverhandelt"] is True, kv
    assert kv["preis_vorher"] == 10000.0

    w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert _ohne_zeit(_doc(w, "kaufvorgaenge", t.ka)) == _ohne_zeit(kv)


def test_p5_termin_kurz_geschlossen_nach_wiederoeffnen_preis(welt):
    """Termin wird waehrend des Abschlusses geschlossen (kein Preis, kein
    abgeholt — richtig), danach wieder geoeffnet: die Heilung zieht beides nach."""
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)

    async def hook(key):
        await w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "storniert"}})
    w.save_hook = hook
    erst = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    w.save_hook = None
    assert erst.get("hinweis"), erst
    assert _doc(w, "kaufvorgaenge", t.ka)["purchase_price"] == 10000.0

    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "offen"}}))
    heil = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert heil.get("nachgezogen") is True and "hinweis" not in heil, heil
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["status"] == "abgeholt" and kv["purchase_price"] == 9000.0, kv


def test_p5_termin_ohne_kaufvorgang_id_preis_im_vorgang_des_vertrags(welt):
    """Termin traegt nur contract_id: der Preis gehoert in den Vorgang, den
    kaufvorgang.fuer_termin findet — vorher wurde er still verworfen."""
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$unset": {"kaufvorgang_id": ""}}))
    fertig = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert fertig["ok"] is True and "hinweis" not in fertig, fertig
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["status"] == "abgeholt"
    assert kv["purchase_price"] == 9000.0 and kv["preis_nachverhandelt"] is True, kv
    assert _doc(w, "kaufvorgaenge", t.kb)["purchase_price"] == 20000.0


def test_p5_heilung_ueberschreibt_handpreis_nicht(welt, monkeypatch):
    """Nebenwirkung: Hat der Chef nach dem Abbruch von Hand einen Preis
    eingetragen (final_price, preis_quelle vor_ort), bleibt der stehen."""
    w = welt
    P = _m("routes.protocols")
    A = _m("routes.appointments")
    t = _abholung(w)
    _stirbt_einmal(monkeypatch, P, "_termin_abgeholt_setzen")
    with pytest.raises(RuntimeError):
        w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    w.run(A.update_appointment(t.aid, A.AppointmentIn(final_price=9500), w.chef))
    assert _doc(w, "kaufvorgaenge", t.ka)["purchase_price"] == 9500.0

    heil = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert heil.get("nachgezogen") is True, heil
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["status"] == "abgeholt"
    assert kv["purchase_price"] == 9500.0 and kv.get("preis_quelle") == "vor_ort", kv


def test_p5_ohne_verhandlung_fremde_preisquelle_bleibt(welt):
    """Ohne neuen Preis wird nur ein Preis zurueckgesetzt, der aus einer
    anderen Version DIESES Termins stammt — nicht der eines fremden Protokolls."""
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, neuer_preis=None)
    w.run(w.db.kaufvorgaenge.update_one(
        {"id": t.ka}, {"$set": {"purchase_price": 9100.0, "preis_nachverhandelt": True,
                                "preis_vorher": 10000.0, "preis_protokoll_id": "p_fremd"}}))
    fertig = w.run(P.finalize_protocol(t.aid, _fin(P, preis=None), w.driver))
    assert fertig["ok"] is True, fertig
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["purchase_price"] == 9100.0 and kv["preis_protokoll_id"] == "p_fremd", kv


# ============================================================ P5-Zusatz-Korrektur
def _korrektur_freigeben(w, P, t, *, preis=None):
    """v1 ist abgeschlossen. Chef oeffnet den Termin, Fahrer startet v2 und
    schickt ab, Chef gibt frei — mit neuem Preis oder zurueckgesetzt."""
    async def lauf():
        await w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "offen"}})
        neu = await P.start_correction(t.aid, w.driver)
        await P.submit_protocol(t.aid, w.driver)
        if preis is None:
            z = await P.protokoll_freigeben(neu["id"], P.FreigabeIn(preis_zuruecksetzen=True), w.chef)
            fr = await P.protokoll_freigeben(neu["id"], P.FreigabeIn(stand=z["stand"]), w.chef)
        else:
            fr = await P.protokoll_freigeben(neu["id"], P.FreigabeIn(neuer_preis=preis), w.chef)
        return neu["id"], fr["stand"]
    return w.run(lauf())


def test_p5_zusatz_korrektur_auf_vertragspreis_setzt_vorgang_zurueck(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert _doc(w, "kaufvorgaenge", t.ka)["purchase_price"] == 9000.0

    v2, stand = _korrektur_freigeben(w, P, t)
    assert _doc(w, "pickup_protocols", v2).get("neuer_preis") is None
    fertig = w.run(P.finalize_protocol(t.aid, _fin(P, preis=None, stand=stand), w.driver))
    assert fertig["ok"] is True and fertig["version"] == 2, fertig
    assert _doc(w, "pickup_protocols", v2)["status"] == "final"
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["purchase_price"] == 10000.0, kv
    assert "preis_nachverhandelt" not in kv and "preis_protokoll_id" not in kv, kv
    assert _doc(w, "vehicles", t.vid).get("purchase_price") == 10000.0


def test_p5_zusatz_korrektur_mit_neuem_preis_gilt(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    v2, stand = _korrektur_freigeben(w, P, t, preis=8000)
    w.run(P.finalize_protocol(t.aid, _fin(P, preis=8000.0, stand=stand), w.driver))
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["purchase_price"] == 8000.0 and kv["preis_vorher"] == 10000.0, kv
    assert kv["preis_protokoll_id"] == v2


# ============================================================ P6
def test_p6_schliessen_nimmt_freigabe_zurueck(welt):
    """Termin wird geschlossen, waehrend das Protokoll freigegeben ist. Nach dem
    Wiederoeffnen darf die alte Freigabe nicht mehr gelten."""
    w = welt
    P = _m("routes.protocols")
    A = _m("routes.appointments")
    t = _abholung(w)
    w.run(A.update_appointment(t.aid, A.AppointmentIn(status="storniert"), w.chef))
    p = _doc(w, "pickup_protocols", t.pid)
    assert p["status"] == "entwurf", p
    assert p.get("rueckfrage") and p.get("freigabe_stand") not in (None, "", "s1"), p

    w.run(A.update_appointment(t.aid, A.AppointmentIn(status="offen"), w.chef))
    with pytest.raises(HTTPException) as fin:
        w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert fin.value.status_code == 409
    assert _doc(w, "pickup_protocols", t.pid)["status"] == "entwurf"
    assert _doc(w, "kaufvorgaenge", t.ka)["status"] != "abgeholt"
    liste = w.run(P.protokolle_zur_freigabe(w.chef))
    assert t.pid not in [e.get("protocol_id") for e in liste]


def test_p6_schliessen_laesst_laufenden_abschluss_unberuehrt(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w, proto_status="wird_abgeschlossen",
                  claim_bis="2099-01-01T00:00:00+00:00", claim_token="laeuft")
    w.run(A.update_appointment(t.aid, A.AppointmentIn(status="storniert"), w.chef))
    p = _doc(w, "pickup_protocols", t.pid)
    assert p["status"] == "wird_abgeschlossen" and p["freigabe_stand"] == "s1", p
    assert p["claim_token"] == "laeuft" and not p.get("rueckfrage")


def _protokolle_mit_haken(monkeypatch, P, wann, haken):
    """pickup_protocols.update_one: vor dem ersten Write, fuer den
    `wann(filter, aenderung)` gilt, laeuft `haken()` (einmal)."""
    echt_db = P.db
    schon = {"x": False}

    class _Protokolle:
        def __getattr__(self, name):
            return getattr(echt_db.pickup_protocols, name)

        async def update_one(self, filt, aenderung, *a, **k):
            if not schon["x"] and wann(filt, aenderung):
                schon["x"] = True
                await haken()
            return await echt_db.pickup_protocols.update_one(filt, aenderung, *a, **k)

    class _DB:
        def __getattr__(self, name):
            return _Protokolle() if name == "pickup_protocols" else getattr(echt_db, name)

        def __getitem__(self, name):
            return self.__getattr__(name)
    monkeypatch.setattr(P, "db", _DB())
    return echt_db


def test_p6_abschicken_gegen_schliessen(welt, monkeypatch):
    """Der Chef schliesst den Termin genau zwischen Vorabpruefung und Write des
    Abschickens: das Protokoll darf nicht beim Chef am geschlossenen Termin landen."""
    w = welt
    P = _m("routes.protocols")
    A = _m("routes.appointments")
    t = _abholung(w, proto_status="entwurf", neuer_preis=None, freigabe_stand=None)

    async def schliessen():
        await A.update_appointment(t.aid, A.AppointmentIn(status="storniert"), w.chef)
    echt_db = _protokolle_mit_haken(
        monkeypatch, P, lambda f, a: f.get("status") == "entwurf"
        and (a.get("$set") or {}).get("status") == P.ZUR_FREIGABE, schliessen)
    with pytest.raises(HTTPException) as ab:
        w.run(P.submit_protocol(t.aid, w.driver))
    monkeypatch.setattr(P, "db", echt_db)
    assert ab.value.status_code == 409
    assert _doc(w, "appointments", t.aid)["status"] == "storniert"
    assert _doc(w, "pickup_protocols", t.pid)["status"] == "entwurf"


def test_p6_freigabe_gegen_schliessen(welt, monkeypatch):
    """Der Chef A gibt frei, Chef B schliesst den Termin zwischen Terminpruefung
    und Write der Freigabe: keine Freigabe am geschlossenen Termin."""
    w = welt
    P = _m("routes.protocols")
    A = _m("routes.appointments")
    t = _abholung(w, proto_status="zur_freigabe", neuer_preis=None)

    async def schliessen():
        await A.update_appointment(t.aid, A.AppointmentIn(status="storniert"), w.chef)
    echt_db = _protokolle_mit_haken(
        monkeypatch, P, lambda f, a: (a.get("$set") or {}).get("status") == P.FREIGEGEBEN,
        schliessen)
    with pytest.raises(HTTPException) as fr:
        w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(neuer_preis=8500), w.chef))
    monkeypatch.setattr(P, "db", echt_db)
    assert fr.value.status_code == 409
    p = _doc(w, "pickup_protocols", t.pid)
    assert p["status"] == "entwurf" and p.get("neuer_preis") is None, p
