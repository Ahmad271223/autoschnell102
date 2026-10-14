# -*- coding: utf-8 -*-
"""Wunsch Ahmad 06.10.2026: Schadenfotos und Lackdicke im Fahrer-Protokoll.

  * Der Fahrer markiert auf der Skizze einen Schaden oder eine Lackdicke-Messung (eigene Markierung mit
    Wert in µm, kein Schaden, nie im Kaufvertrag) und kann dazu Fotos hochladen.
  * Hoechstens 25 Fotos je Protokoll (auch bei gleichzeitigem Hochladen); Fotos zu wieder entfernten
    Markierungen zaehlen nicht und fallen beim Abschicken heraus.
  * Der Chef sieht die Fotos 7 Tage ab dem Hochladen (Freigabe-Karte, Foto-Endpunkt) — danach 410, die
    Dateien BLEIBEN gespeichert. Geloescht werden sie mit dem Protokoll (verworfener Entwurf, geloeschter
    Termin, Loeschung des Kaufvertrags), nie solange eine andere Version sie noch nennt.
  * Beim Abschicken braucht jede Lackdicke-Messung ihren Wert; das Protokoll-PDF nennt die Messwerte.
"""
import asyncio
import base64
import io
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import schadenfotos as SF  # noqa: E402
from test_befunde_runde30_freigabe import _jetzt, _modul, _vollstaendig, welt  # noqa: E402,F401

KRATZER = {"id": "s1", "view": "left", "zone": "Tür vorne links", "x": 520, "y": 480, "type_key": "kratzer",
           "type_label": "Kratzer",
           "severity_data": {"laenge": "15–30 cm", "tiefe": "bis Grundierung", "anzahl": "einzeln"}}
LACK = {"id": "l1", "view": "top", "zone": "Motorhaube", "x": 700, "y": 300, "wert_um": 180}


def _jpeg(farbe=(200, 30, 30)) -> str:
    from PIL import Image
    puffer = io.BytesIO()
    Image.new("RGB", (120, 90), farbe).save(puffer, "JPEG", quality=80)
    return base64.b64encode(puffer.getvalue()).decode()


@pytest.fixture
def ablage(welt, monkeypatch):  # noqa: F811
    """Speicher im Arbeitsspeicher: Hochladen, Ausliefern und Loeschen sind sichtbar."""
    SS = _modul("storage_service")
    dateien = {}

    async def _save(key, daten):
        dateien[key] = daten
        return key

    async def _load(key):
        if key not in dateien:
            raise SS.StorageError("weg")
        return dateien[key]

    async def _delete(key):
        return dateien.pop(key, None) is not None
    monkeypatch.setattr(SS, "make_key", lambda kat, firma, name: f"{kat}/{firma}/{uuid.uuid4().hex}.jpg")
    monkeypatch.setattr(SS, "save_async", _save)
    monkeypatch.setattr(SS, "load_async", _load)
    monkeypatch.setattr(SS, "delete_async", _delete)
    return dateien


def _entwurf(w, P, **extra):
    return _vollstaendig(w, P, new_damages=[dict(KRATZER)], lackmessungen=[dict(LACK)], **extra)


def _foto(P, schaden_id="s1", farbe=(200, 30, 30)):
    return P.SchadenFotoIn(schaden_id=schaden_id, photo_b64=_jpeg(farbe))


async def _erwarte(status, coro):
    with pytest.raises(HTTPException) as e:
        await coro
    assert e.value.status_code == status, (e.value.status_code, e.value.detail)
    return e.value


def _eintrag(i, schaden_id="s1", tage_alt=0):
    return {"id": f"f{i}", "key": f"protocol/x/{i}.jpg", "schaden_id": schaden_id,
            "erstellt_am": (datetime.now(timezone.utc) - timedelta(days=tage_alt)).isoformat()}


# ---------------------------------------------------------------- Modelle
def test_01_lackmessung_eigene_markierung_mit_wert():
    P = _modul("routes.protocols")
    p = P.ProtocolIn(lackmessungen=[{k: v for k, v in LACK.items() if k != "id"}, dict(LACK), dict(LACK),
                                    {**LACK, "id": "l2", "wert_um": None}])
    ids = [m.id for m in p.lackmessungen]
    assert len(set(ids)) == 4 and ids[1] == "l1" and ids[0].startswith("l-")
    for falsch in ({**LACK, "view": "technik"}, {**LACK, "zone": "  "}, {**LACK, "x": 99999},
                   {**LACK, "wert_um": -1}, {**LACK, "wert_um": P.LACKDICKE_MAX_UM + 1}):
        with pytest.raises(Exception):
            P.ProtocolIn(lackmessungen=[falsch])
    # Lackdicke ist KEIN Schaden: nicht unter den Schadensarten
    assert "lackdicke" not in P.SCHADEN_ARTEN
    assert SF.SCHADENFOTO_MAX == 25 and SF.SCHADENFOTO_SICHT_TAGE == 7


# ---------------------------------------------------------------- Hochladen, Anzeigen, Entfernen
def test_02_fahrer_laedt_hoch_chef_sieht_es(welt, ablage):  # noqa: F811
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        await w.db.pickup_protocols.insert_one(_entwurf(w, P))
        f1 = await P.schaden_foto_hochladen(w.aid, _foto(P, "s1"), w.driver)
        f2 = await P.schaden_foto_hochladen(w.aid, _foto(P, "l1", (0, 0, 200)), w.driver)
        doc = await w.db.pickup_protocols.find_one({"id": f"p_r30_{w.s}"}, {"_id": 0})
        geladen = await P.get_protocol(w.aid, w.driver)
        fahrer = await P.schaden_foto_fahrer(w.aid, f1["id"], w.driver)
        chef = await P.schaden_foto_chef(doc["id"], f2["id"], w.chef)
        await _erwarte(404, P.schaden_foto_chef(doc["id"], f2["id"], w.fremd_chef))
        return f1, doc, geladen, fahrer, chef

    f1, doc, geladen, fahrer, chef = w.run(lauf())
    assert "key" not in f1 and f1["sichtbar_bis"]
    eintraege = doc["schaden_fotos"]
    assert [e["schaden_id"] for e in eintraege] == ["s1", "l1"] and doc["schaden_fotos_stand"] == 2
    assert all(e["key"].startswith(f"protocol/{w.dealer_id}/") for e in eintraege)
    assert len(ablage) == 2
    # die App bekommt keine Speicher-Keys
    assert all("key" not in e for e in geladen["protocol"]["schaden_fotos"])
    assert geladen["template"]["schadenfoto_max"] == 25 and geladen["template"]["schadenfoto_sicht_tage"] == 7
    assert fahrer.body[:2] == b"\xff\xd8" and chef.body[:2] == b"\xff\xd8"
    assert fahrer.headers["cache-control"] == "private, no-store"


def test_03_nur_im_entwurf_und_nur_zu_gespeicherten_markierungen(welt, ablage):  # noqa: F811
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        await _erwarte(409, P.schaden_foto_hochladen(w.aid, _foto(P), w.driver))     # noch kein Protokoll
        await w.db.pickup_protocols.insert_one(_entwurf(w, P))
        e = await _erwarte(409, P.schaden_foto_hochladen(w.aid, _foto(P, "gibtsnicht"), w.driver))
        assert "noch nicht gespeichert" in e.detail
        await _erwarte(400, P.schaden_foto_hochladen(
            w.aid, P.SchadenFotoIn(schaden_id="s1", photo_b64=base64.b64encode(b"kein bild" * 20).decode()),
            w.driver))
        await w.db.pickup_protocols.update_one({"id": f"p_r30_{w.s}"}, {"$set": {"status": "zur_freigabe"}})
        e = await _erwarte(409, P.schaden_foto_hochladen(w.aid, _foto(P), w.driver))
        assert "Freigabe" in e.detail

    w.run(lauf())
    assert ablage == {}, "abgelehnte Fotos hinterlassen keine Datei"


def test_04_hoechstens_25_auch_gleichzeitig_entfernte_zaehlen_nicht(welt, ablage):  # noqa: F811
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        alt = [_eintrag(i) for i in range(23)] + [_eintrag(100 + i, "entfernt") for i in range(10)]
        await w.db.pickup_protocols.insert_one(_entwurf(w, P, schaden_fotos=alt, schaden_fotos_stand=33))
        ergebnisse = await asyncio.gather(*[P.schaden_foto_hochladen(w.aid, _foto(P), w.driver)
                                            for _ in range(5)], return_exceptions=True)
        doc = await w.db.pickup_protocols.find_one({"id": f"p_r30_{w.s}"}, {"_id": 0})
        return ergebnisse, doc

    ergebnisse, doc = w.run(lauf())
    ok = [r for r in ergebnisse if isinstance(r, dict)]
    abgelehnt = [r for r in ergebnisse if isinstance(r, HTTPException)]
    assert len(ok) == 2 and len(abgelehnt) == 3, ergebnisse
    assert all(r.status_code == 409 for r in abgelehnt)
    assert len(SF.aktive_fotos(doc)) == 25
    assert len(ablage) == 2, "die Dateien der abgelehnten Versuche sind wieder weg"


def test_05_entfernen_loescht_die_datei(welt, ablage):  # noqa: F811
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        await w.db.pickup_protocols.insert_one(_entwurf(w, P))
        f = await P.schaden_foto_hochladen(w.aid, _foto(P), w.driver)
        assert len(ablage) == 1
        assert (await P.schaden_foto_entfernen(w.aid, f["id"], w.driver)) == {"ok": True}
        await _erwarte(404, P.schaden_foto_entfernen(w.aid, f["id"], w.driver))
        return await w.db.pickup_protocols.find_one({"id": f"p_r30_{w.s}"}, {"_id": 0})

    doc = w.run(lauf())
    assert doc["schaden_fotos"] == [] and ablage == {}


# ---------------------------------------------------------------- Sichtfrist beim Chef
def test_06_nach_7_tagen_ausgeblendet_aber_gespeichert(welt, ablage):  # noqa: F811
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        alt, neu = _eintrag(1, tage_alt=8), _eintrag(2, tage_alt=6)
        ablage[alt["key"]] = b"\xff\xd8alt"
        ablage[neu["key"]] = b"\xff\xd8neu"
        await w.db.pickup_protocols.insert_one(_entwurf(w, P, schaden_fotos=[alt, neu]))
        await P.submit_protocol(w.aid, w.driver)
        liste = await P.protokolle_zur_freigabe(w.chef)
        e = await _erwarte(410, P.schaden_foto_chef(f"p_r30_{w.s}", "f1", w.chef))
        ok = await P.schaden_foto_chef(f"p_r30_{w.s}", "f2", w.chef)
        await _erwarte(404, P.schaden_foto_fahrer(w.aid, "f1", w.driver))
        return liste, e, ok

    liste, e, ok = w.run(lauf())
    karte = liste[0]
    assert [f["id"] for f in karte["schaden_fotos"]] == ["f2"] and "key" not in karte["schaden_fotos"][0]
    assert karte["lackmessungen"][0]["wert_um"] == 180
    assert "7 Tage" in e.detail and ok.body == b"\xff\xd8neu"
    assert len(ablage) == 2, "nach der Sichtfrist bleibt die Datei gespeichert (Entscheidung Ahmad)"


# ---------------------------------------------------------------- Abschicken
def test_07_abschicken_lackwert_pflicht_und_entfernte_fotos_raus(welt, ablage):  # noqa: F811
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        bleibt, weg = _eintrag(1), _eintrag(2, "entfernt")
        ablage[bleibt["key"]] = b"x"
        ablage[weg["key"]] = b"y"
        await w.db.pickup_protocols.insert_one(_vollstaendig(
            w, P, new_damages=[dict(KRATZER)], lackmessungen=[{**LACK, "wert_um": None}],
            schaden_fotos=[bleibt, weg]))
        e = await _erwarte(422, P.submit_protocol(w.aid, w.driver))
        assert "Lackdicke" in e.detail and "Motorhaube" in e.detail
        await w.db.pickup_protocols.update_one({"id": f"p_r30_{w.s}"},
                                               {"$set": {"lackmessungen.0.wert_um": 95}})
        await P.submit_protocol(w.aid, w.driver)
        return await w.db.pickup_protocols.find_one({"id": f"p_r30_{w.s}"}, {"_id": 0})

    doc = w.run(lauf())
    assert doc["status"] == "zur_freigabe"
    assert [e["id"] for e in doc["schaden_fotos"]] == ["f1"]
    assert list(ablage) == ["protocol/x/1.jpg"], "Foto des entfernten Schadens ist geloescht"


# ---------------------------------------------------------------- Loeschen mit dem Protokoll
def test_08_verworfener_entwurf_nimmt_fotos_mit_ausser_andere_version_nennt_sie(welt, ablage):  # noqa: F811
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        geteilt, eigen = _eintrag(1), _eintrag(2)
        ablage[geteilt["key"]] = b"g"
        ablage[eigen["key"]] = b"e"
        # eine abgeloeste Vorversion nennt dieselbe Datei (Korrektur uebernimmt die Fotos)
        await w.db.pickup_protocols.insert_one({"id": f"alt_{w.s}", "appointment_id": "anderer",
                                                "dealer_id": w.dealer_id, "status": "final",
                                                "superseded": True, "schaden_fotos": [geteilt]})
        await w.db.pickup_protocols.insert_one(_entwurf(w, P, schaden_fotos=[geteilt, eigen]))
        return await P.entwurf_bei_terminaenderung_verwerfen(w.aid)

    assert w.run(lauf()) is True
    assert list(ablage) == ["protocol/x/1.jpg"], ablage


def test_09_kaufvertrag_loeschen_loescht_die_fotos(welt, ablage):  # noqa: F811
    w = welt
    P = _modul("routes.protocols")
    C = _modul("cleanup_service")

    async def lauf():
        f = _eintrag(1, tage_alt=30)
        ablage[f["key"]] = b"x"
        await w.db.pickup_protocols.insert_one(_entwurf(w, P, status="final", contract_id=w.cid,
                                                        schaden_fotos=[f]))
        await C._protokolle_pii_entfernen(w.db, [w.aid], w.dealer_id, _jetzt(), contract_id=w.cid)
        return await w.db.pickup_protocols.find_one({"id": f"p_r30_{w.s}"}, {"_id": 0})

    doc = w.run(lauf())
    assert doc["schaden_fotos"] == [] and ablage == {}


def test_10_termin_geloescht_und_verwaiste_entwuerfe(welt, ablage):  # noqa: F811
    w = welt
    P = _modul("routes.protocols")
    C = _modul("cleanup_service")

    async def lauf():
        f = _eintrag(1)
        ablage[f["key"]] = b"x"
        await w.db.pickup_protocols.insert_one(_entwurf(w, P, schaden_fotos=[f],
                                                        updated_at="2026-01-01T00:00:00+00:00"))
        await w.db.appointments.delete_one({"id": w.aid})
        return await C.verwaiste_protokoll_entwuerfe_loeschen(w.db, datetime.now(timezone.utc))

    assert w.run(lauf()) == 1 and ablage == {}


def test_11_pdf_nennt_die_lackdicke_ohne_fotos():
    import pickup_pdf_service as PDF
    from pypdf import PdfReader
    pdf = PDF.build_pickup_pdf(appointment={"id": "a"}, filled={
        "new_damages": [], "lackmessungen": [dict(LACK), {**LACK, "id": "l2", "zone": "Dach", "wert_um": None}]})
    text = " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "Lackdicke gemessen (2)" in text and "Motorhaube: 180" in text and "Dach: ohne Wert" in text


def test_12_bildunterschriften():
    assert SF.markierung_text(dict(KRATZER)) == "Kratzer · Tür vorne links"
    assert SF.markierung_text({**LACK, "art": "lackdicke"}) == "Lackdicke 180 µm · Motorhaube"
    assert SF.markierungen({"new_damages": [KRATZER], "lackmessungen": [LACK]}) == {
        "s1": "Kratzer · Tür vorne links", "l1": "Lackdicke 180 µm · Motorhaube"}
