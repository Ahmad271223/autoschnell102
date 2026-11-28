# -*- coding: utf-8 -*-
"""Wunsch Ahmad 24.11.2026.

Teil 1 — Abweichungen nummerieren: jede Abweichung zwischen Vertrag und
Fahrer-Protokoll hat eine feste Nummer (abweichung_nummern.py), die sich nicht
verschiebt, wenn eine dazukommt oder wegfaellt. Buero (Freigaben, Terminplaner)
und Fahrer-App bekommen dieselben Nummern; das Protokoll-PDF auch.

Teil 2 — "Was ist neu": Eintraege in backend/neuigkeiten.json, Gelesen-Stand je
Konto (routes/neuigkeiten.py), hoechstens EIN Hinweis pro Tag ueber alle Geraete,
Fahrer sehen nur Eintraege "fuer: fahrer".

In-Prozess gegen eine Wegwerf-DB (welt aus test_befunde_runde33_freigaben);
die HTTP-Proben laufen nur, wenn TEST_BASE_URL einen Server mit dieser DB_NAME hat.
"""
import io
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import abweichung_nummern as AN  # noqa: E402
from test_befunde_runde33_freigaben import _abholung, _jetzt, _modul, _protokoll, welt  # noqa: E402,F401

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend" / "src"


# ================================================================ Teil 1 — rein
def test_01_nummern_bleiben_fest():
    n, neu = AN.nummern_vergeben({}, ["fz:a", "sch:x", "aus:Navi"])
    assert n == {"fz:a": 1, "sch:x": 2, "aus:Navi": 3} and neu
    # b faellt weg, eine neue kommt dazu: die alten behalten ihre Nummer, die neue bekommt 4
    n2, neu2 = AN.nummern_vergeben(n, ["fz:a", "aus:Navi", "zus:battery"])
    assert (n2["fz:a"], n2["aus:Navi"], n2["zus:battery"]) == (1, 3, 4) and neu2
    assert n2["sch:x"] == 2, "die weggefallene Nummer bleibt reserviert"
    # die weggefallene kommt zurueck -> wieder Nr. 2, nichts Neues
    n3, neu3 = AN.nummern_vergeben(n2, ["sch:x", "fz:a"])
    assert n3 == n2 and not neu3
    # Speicherform und Ruecklesen (kaputte Eintraege fallen raus)
    liste = AN.als_liste(n2)
    assert [e["nr"] for e in liste] == [1, 2, 3, 4]
    assert AN.gespeicherte_nummern(liste + [{"k": "x"}, "y", {"k": "z", "nr": 2}, {"k": "w", "nr": True}]) == n2


def test_02_eintraege_gruppen_und_titel():
    P = _modul("routes.protocols")
    vor_ort = {"ausstattung": [{"name": "Navi", "art": "fehlt", "befund": "fehlt komplett"},
                               {"name": "Sitzheizung", "art": "fehlt", "befund": "fehlt komplett"},
                               {"name": "AHK", "art": "defekt", "befund": "vorhanden, defekt"},
                               {"name": "Tempomat", "art": "offen", "befund": "keine Angabe"}],
               "dokumente": [{"name": "Bedienungsanleitung", "art": "fehlt", "befund": "fehlt"}],
               "zustand": [{"name": "Batterie / Starter", "schluessel": "battery", "art": "mangel", "befund": "schwach"},
                           {"name": "Sauberkeit Außen", "schluessel": "clean_outside", "art": "hinweis", "befund": "mittel"}]}
    zeilen = [{"schluessel": "mileage_contract", "label": "KM-Stand laut Vertrag", "art": "km", "status": "weicht ab",
               "vertrag_text": "85.000 km", "vertrag_quelle": "inserat", "vor_ort_text": "91.000 km",
               "wert_roh": "91000", "abweichend": True, "hinweis": "+6.000 km"},
              {"schluessel": "make", "label": "Marke", "art": "text", "status": "stimmt", "vertrag_text": "VW",
               "vertrag_quelle": "inserat", "vor_ort_text": "VW", "wert_roh": "", "abweichend": False, "hinweis": ""}]
    schaeden = [{"id": "d1", "type_label": "Kratzer", "zone": "Tür hinten links", "view": "links"},
                {"type_label": "Delle", "zone": "Motorhaube", "view": "oben"}]
    erg = AN.nummerieren({}, AN.eintraege_sammeln(zeilen, vor_ort, schaeden))
    titel = [(e["nr"], e["titel"]) for e in erg["eintraege"]]
    assert titel == [(1, "KM-Stand laut Vertrag"), (2, "Kratzer · Tür hinten links"), (3, "Delle · Motorhaube"),
                     (4, "Navi"), (5, "Sitzheizung"), (6, "AHK"), (7, "Bedienungsanleitung"),
                     (8, "Batterie / Starter")]
    assert erg["anzahl"] == 8, "keine Angabe und reine Hinweise zaehlen nicht"
    gruppen = {g["key"]: [e["nr"] for e in g["eintraege"]] for g in erg["gruppen"]}
    assert gruppen["ausstattung_fehlt"] == [4, 5], "fehlende Ausstattung gesammelt"
    assert [g["titel"] for g in erg["gruppen"]][2] == "Ausstattung fehlt"
    assert erg["eintraege"][0]["text"] == "laut Vertrag: 85.000 km, vor Ort: 91.000 km (+6.000 km)"
    assert erg["speichern"] and AN.fuer_antwort(erg).get("speichern") is None
    assert P.VEHICLE_CHECK_FIELDS is not None


# ================================================================ Teil 1 — Buero und Fahrer
def _dok_alle_da(P):
    return {d: True for d in P.DOCUMENT_ITEMS}


def test_03_buero_und_fahrer_gleiche_nummern_und_stabil(welt):  # noqa: F811
    w = welt
    P = _modul("routes.protocols")
    AW = _modul("routes.abweichungen")
    aid = _abholung(w, "nr", fahrzeug_extra={"features": ["Navi", "Sitzheizung", "Tempomat"]})
    vc = {k: {"status": o[0]} for k, _l, o in P.VEHICLE_CHECK_FIELDS}
    vc["color"] = {"status": "weicht ab", "value": "Blau"}
    _protokoll(w, aid, P, status=P.ZUR_FREIGABE, erstmals_abgeschickt_am=_jetzt(),
               vehicle_check=vc, documents=_dok_alle_da(P),
               features={"Navi": "fehlt", "Sitzheizung": True, "Tempomat": "fehlt"},
               new_damages=[{"id": "s1", "type_key": "kratzer", "type_label": "Kratzer",
                             "zone": "Tür hinten links", "view": "links"}])

    buero = w.run(P.protokolle_zur_freigabe(w.chef))[0]["abweichungen_nummeriert"]
    fahrer = w.run(P.get_protocol(aid, driver=w.driver))["abweichungen_nummeriert"]
    termin = w.run(AW.termin_abweichungen(aid, user=w.chef))
    paare = [(e["nr"], e["titel"]) for e in buero["eintraege"]]
    assert paare == [(1, "Farbe"), (2, "Kratzer · Tür hinten links"), (3, "Navi"), (4, "Tempomat")]
    assert [(e["nr"], e["titel"]) for e in fahrer["eintraege"]] == paare, "Fahrer sieht dieselben Nummern"
    assert [(e["nr"], e["titel"]) for e in termin["eintraege"]] == paare
    assert "speichern" not in buero and "speichern" not in fahrer

    doc = w.run(w.db.pickup_protocols.find_one({"id": f"p_{aid}"}, {"_id": 0}))
    assert [e["nr"] for e in doc["abweichung_nummern"]] == [1, 2, 3, 4] and doc["abweichung_anzahl"] == 4
    appt = w.run(w.db.appointments.find_one({"id": aid}, {"_id": 0}))
    assert appt["protokoll_abweichungen"] == 4, "Zahl fuer die Terminliste"

    # Navi taucht doch auf (faellt weg), ein neuer Schaden kommt VOR dem alten in die Liste:
    w.run(w.db.pickup_protocols.update_one({"id": f"p_{aid}"}, {"$set": {
        "features.Navi": True,
        "new_damages": [{"id": "s0", "type_key": "delle", "type_label": "Delle", "zone": "Motorhaube", "view": "oben"},
                        {"id": "s1", "type_key": "kratzer", "type_label": "Kratzer",
                         "zone": "Tür hinten links", "view": "links"}]}}))
    buero2 = w.run(P.protokolle_zur_freigabe(w.chef))[0]["abweichungen_nummeriert"]
    fahrer2 = w.run(P.get_protocol(aid, driver=w.driver))["abweichungen_nummeriert"]
    paare2 = [(e["nr"], e["titel"]) for e in buero2["eintraege"]]
    assert paare2 == [(1, "Farbe"), (2, "Kratzer · Tür hinten links"), (4, "Tempomat"), (5, "Delle · Motorhaube")], \
        "alte Nummern bleiben, die neue bekommt die naechste freie"
    assert [(e["nr"], e["titel"]) for e in fahrer2["eintraege"]] == paare2
    assert w.run(w.db.appointments.find_one({"id": aid}, {"_id": 0}))["protokoll_abweichungen"] == 4

    # Navi fehlt wieder -> wieder Nr. 3
    w.run(w.db.pickup_protocols.update_one({"id": f"p_{aid}"}, {"$set": {"features.Navi": "fehlt"}}))
    termin3 = w.run(AW.termin_abweichungen(aid, user=w.chef))
    assert [e["nr"] for e in termin3["eintraege"]] == [1, 2, 3, 4, 5]
    gruppe = [g for g in termin3["gruppen"] if g["key"] == "ausstattung_fehlt"][0]
    assert gruppe["titel"] == "Ausstattung fehlt" and [e["nr"] for e in gruppe["eintraege"]] == [3, 4]


def test_04_entwurf_rechnet_speichert_aber_nicht_und_sucher_nur_eigene(welt):  # noqa: F811
    w = welt
    P = _modul("routes.protocols")
    AW = _modul("routes.abweichungen")
    aid = _abholung(w, "entw")
    vc = {k: {"status": o[0]} for k, _l, o in P.VEHICLE_CHECK_FIELDS}
    vc["make"] = {"status": "weicht ab", "value": "Opel"}
    _protokoll(w, aid, P, vehicle_check=vc, documents=_dok_alle_da(P))
    fahrer = w.run(P.get_protocol(aid, driver=w.driver))["abweichungen_nummeriert"]
    assert fahrer["anzahl"] == 1 and fahrer["eintraege"][0]["nr"] == 1
    doc = w.run(w.db.pickup_protocols.find_one({"id": f"p_{aid}"}, {"_id": 0}))
    assert "abweichung_nummern" not in doc, "Entwurf: Fahrer-Abruf vergibt keine Nummern fest"
    # Sucher: fremder Termin -> 404; Chef: ok
    with pytest.raises(HTTPException) as exc:
        w.run(AW.termin_abweichungen(aid, user=w.sucher))
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc2:
        w.run(AW.termin_abweichungen("gibt-es-nicht", user=w.chef))
    assert exc2.value.status_code == 404
    # ohne Protokoll: leere Liste
    leer = _abholung(w, "leer")
    assert w.run(AW.termin_abweichungen(leer, user=w.chef))["anzahl"] == 0


def test_05_pdf_traegt_dieselben_nummern():
    pdf_mod = _modul("pickup_pdf_service")
    P = _modul("routes.protocols")
    vc = {k: {"status": o[0]} for k, _l, o in P.VEHICLE_CHECK_FIELDS}
    vc["color"] = {"status": "weicht ab", "value": "Blau"}
    filled = {"vehicle_check": vc, "documents": {d: True for d in P.DOCUMENT_ITEMS},
              "features": {"Navi": "fehlt"},
              "condition": {k: (o[0] if isinstance(o, list) else "85000") for k, _l, o in P.CONDITION_FIELDS},
              "new_damages": [{"id": "s1", "type_key": "kratzer", "type_label": "Kratzer", "zone": "Tür hinten links",
                               "view": "links", "x": 10, "y": 10}],
              # gespeichert: Farbe hatte Nr. 7, der Schaden Nr. 2
              "abweichung_nummern": [{"k": "sch:s1", "nr": 2}, {"k": "fz:color", "nr": 7}]}
    vehicle = {"make_label": "VW", "model_label": "Golf", "color": "Rot", "features": ["Navi"]}
    pdf = pdf_mod.build_pickup_pdf(appointment={"id": "t1", "title": "Golf"}, vehicle=vehicle, contract={},
                                   filled=filled)
    from pypdf import PdfReader
    text = "\n".join(s.extract_text() or "" for s in PdfReader(io.BytesIO(pdf)).pages)
    text = " ".join(text.split())
    assert "Nr. 7 · Farbe" in text and "Nr. 2 · Kratzer · Tür hinten links" in text
    assert "Nr. 8 · Navi" in text, "neue Abweichung: naechste freie Nummer"
    assert "Ausstattung fehlt" in text


# ================================================================ Teil 2 — rein
def test_06_neuigkeiten_datei_gueltig():
    N = _modul("routes.neuigkeiten")
    roh = json.loads((BACKEND / "neuigkeiten.json").read_text(encoding="utf-8"))
    liste = N.eintraege_pruefen(roh)
    assert len({e["id"] for e in liste}) == len(liste) >= 8
    assert any("fahrer" in e["fuer"] for e in liste) and any("chef" in e["fuer"] for e in liste)
    for wort in ("QR-Code", "Chat", "Abänderungsvertrag", "STORNIERT", "Zulassung", "Einkaufssumme",
                 "Fahrzeugseite", "Nummer"):
        assert any(wort in (e["titel"] + e["text"] + " ".join(e["punkte"])) for e in liste), wort
    for kaputt in ({"eintraege": [{"id": "a", "datum": "24.11.2026", "titel": "x", "text": "y", "art": "neu"}]},
                   {"eintraege": [{"id": "a", "datum": "2026-11-24", "titel": "x", "text": "y", "art": "toll"}]},
                   {"eintraege": [{"id": "a", "datum": "2026-11-24", "titel": "x", "text": "y", "art": "neu",
                                   "link": "https://fremd.example"}]},
                   {"eintraege": [{"id": "a", "datum": "2026-11-24", "titel": "x", "text": "y", "art": "neu",
                                   "fuer": ["admin"]}]},
                   {"eintraege": [{"id": "a", "datum": "2026-11-24", "titel": "x", "text": "y", "art": "neu"},
                                  {"id": "a", "datum": "2026-11-24", "titel": "x", "text": "y", "art": "neu"}]}):
        with pytest.raises(ValueError):
            N.eintraege_pruefen(kaputt)
    # neueste zuerst; bei gleichem Datum Reihenfolge der Datei
    s = N.eintraege_pruefen({"eintraege": [
        {"id": "b", "datum": "2026-11-24", "titel": "x", "text": "y", "art": "neu"},
        {"id": "a", "datum": "2026-11-20", "titel": "x", "text": "y", "art": "neu"},
        {"id": "c", "datum": "2026-11-24", "titel": "x", "text": "y", "art": "neu", "wichtig": True}]})
    assert [e["id"] for e in s] == ["b", "c", "a"]
    assert N.hinweis_waehlen(s, set())["id"] == "c", "wichtig zuerst"
    assert N.hinweis_waehlen(s, {"c"})["id"] == "b", "sonst der neueste ungelesene"
    assert N.hinweis_waehlen(s, {"a", "b", "c"}) is None
    # Tag nach Europe/Berlin: 23:30 UTC am 24.11. ist in Berlin schon der 25.
    assert N.heute_berlin(datetime(2026, 11, 24, 23, 30, tzinfo=timezone.utc)) == "2026-11-25"


# ================================================================ Teil 2 — Konten
def test_07_gelesen_stand_zaehler_und_404(welt):  # noqa: F811
    w = welt
    N = _modul("routes.neuigkeiten")
    chef_ids = [e["id"] for e in N.fuer_rolle("chef")]
    fahrer_ids = [e["id"] for e in N.fuer_rolle("fahrer")]
    stand = w.run(N.neuigkeiten_stand(user=w.chef))
    assert stand["ungelesen"] == len(chef_ids) and stand["ids"] == chef_ids
    nach = w.run(N.neuigkeiten_gelesen(chef_ids[0], user=w.chef))
    assert nach["ungelesen"] == len(chef_ids) - 1
    w.run(N.neuigkeiten_gelesen(chef_ids[0], user=w.chef))          # doppelt: keine Wirkung
    assert w.run(N.neuigkeiten_stand(user=w.chef))["ungelesen"] == len(chef_ids) - 1
    liste = w.run(N.neuigkeiten_liste(user=w.chef))
    assert [e["gelesen"] for e in liste["eintraege"]][0] is True and "fuer" not in liste["eintraege"][0]
    for falsch in ("gibt-es-nicht", fahrer_ids[0], "$where"):
        with pytest.raises(HTTPException) as exc:
            w.run(N.neuigkeiten_gelesen(falsch, user=w.chef))
        assert exc.value.status_code == 404
    # der Sucher hat seinen eigenen Stand
    assert w.run(N.neuigkeiten_stand(user=w.sucher))["ungelesen"] == len(N.fuer_rolle("sucher"))
    assert w.run(N.neuigkeiten_alle_gelesen(user=w.chef))["ungelesen"] == 0
    assert w.run(N.neuigkeiten_stand(user=w.chef))["ungelesen"] == 0


def test_08_ein_hinweis_pro_tag_ueber_alle_geraete(welt):  # noqa: F811
    w = welt
    N = _modul("routes.neuigkeiten")
    konto = N._konto_firma(w.chef)
    tag1 = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)
    erst = w.run(N._hinweis(konto, "chef", tag1))["hinweis"]
    wichtig = [e for e in N.fuer_rolle("chef") if e["wichtig"]]
    assert erst and erst["id"] == (wichtig or N.fuer_rolle("chef"))[0]["id"]
    # zweites Geraet / zweiter Aufruf am selben Tag (auch abends) -> nichts
    assert w.run(N._hinweis(konto, "chef", tag1 + timedelta(hours=12)))["hinweis"] is None
    # "Spaeter": am naechsten Tag wieder ein Hinweis
    tag2 = tag1 + timedelta(days=1)
    zweit = w.run(N._hinweis(konto, "chef", tag2))["hinweis"]
    assert zweit and zweit["id"] == erst["id"], "nicht gelesen -> derselbe kommt wieder"
    # alles gelesen -> kein Hinweis, und der Tag wird nicht verbraucht
    w.run(N.neuigkeiten_alle_gelesen(user=w.chef))
    tag3 = tag1 + timedelta(days=2)
    assert w.run(N._hinweis(konto, "chef", tag3))["hinweis"] is None
    doc = w.run(w.db[N.SAMMLUNG].find_one({"konto": konto}, {"_id": 0}))
    assert doc["hinweis_tag"] == "2026-11-25"


def test_08b_schalter_hinweis_aus(welt, monkeypatch):  # noqa: F811
    """25.11.2026: NEUIGKEITEN_HINWEIS=false (E2E) — kein Fenster, und der Tag wird nicht verbraucht."""
    w = welt
    N = _modul("routes.neuigkeiten")
    konto = N._konto_firma(w.chef)
    tag = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("NEUIGKEITEN_HINWEIS", "false")
    assert w.run(N._hinweis(konto, "chef", tag))["hinweis"] is None
    assert w.run(w.db[N.SAMMLUNG].find_one({"konto": konto, "hinweis_tag": "2026-11-26"})) is None
    monkeypatch.delenv("NEUIGKEITEN_HINWEIS")
    assert w.run(N._hinweis(konto, "chef", tag))["hinweis"], "Standard: Fenster an"


def test_09_fahrer_sieht_nur_fahrer_eintraege(welt):  # noqa: F811
    w = welt
    N = _modul("routes.neuigkeiten")
    liste = w.run(N.fahrer_neuigkeiten_liste(driver=w.driver))
    ids = [e["id"] for e in liste["eintraege"]]
    assert ids and set(ids) == {e["id"] for e in N.EINTRAEGE if "fahrer" in e["fuer"]}
    chef_only = [e["id"] for e in N.EINTRAEGE if "fahrer" not in e["fuer"]][0]
    with pytest.raises(HTTPException) as exc:
        w.run(N.fahrer_neuigkeiten_gelesen(chef_only, driver=w.driver))
    assert exc.value.status_code == 404
    h = w.run(N.fahrer_neuigkeiten_hinweis(driver=w.driver))["hinweis"]
    assert h is None or h["id"] in ids
    assert w.run(N.fahrer_neuigkeiten_hinweis(driver=w.driver))["hinweis"] is None, "einmal pro Tag"
    w.run(N.fahrer_neuigkeiten_gelesen(ids[0], driver=w.driver))
    assert w.run(N.fahrer_neuigkeiten_stand(driver=w.driver))["ungelesen"] == len(ids) - 1
    # der Chef-Stand bleibt davon unberuehrt
    assert w.run(N.neuigkeiten_stand(user=w.chef))["ungelesen"] == len(N.fuer_rolle("chef"))


def test_10_oberflaeche_verdrahtet():
    app = (FRONTEND / "App.jsx").read_text(encoding="utf-8")
    assert 'path="/app/neuigkeiten"' in app
    layout = (FRONTEND / "components" / "AppLayout.jsx").read_text(encoding="utf-8")
    assert '"/app/neuigkeiten"' in layout and "nav-neuigkeiten-zaehler" in layout and "<NeuigkeitenHinweis" in layout
    fahrer = (FRONTEND / "pages" / "driver" / "DriverLayout.jsx").read_text(encoding="utf-8")
    assert 'basis="/driver/neuigkeiten"' in fahrer
    for datei in (("pages", "app", "Freigaben.jsx"), ("pages", "driver", "Protokoll.jsx")):
        assert "<AbweichungenListe" in (FRONTEND.joinpath(*datei)).read_text(encoding="utf-8")
    termine = (FRONTEND / "pages" / "app" / "Termine.jsx").read_text(encoding="utf-8")
    assert "protokoll_abweichungen" in termine and "<AbweichungenDialog" in termine
    server = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert "abweichungen_routes.router" in server and "neuigkeiten_routes.router" in server


# ================================================================ HTTP (eigener Server)
def _http_bereit():
    if not os.environ.get("TEST_BASE_URL"):
        return False
    try:
        import requests
        return requests.get(os.environ["TEST_BASE_URL"].rstrip("/") + "/api/health", timeout=3).ok
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _http_bereit(), reason="kein Test-Server unter TEST_BASE_URL")
def test_11_http_routen():
    import requests
    import konten as K
    db = K._db()
    s = uuid.uuid4().hex[:8]
    dealer_id, chef_id, fahrer_id = f"d_nw_{s}", f"u_nw_{s}", f"f_nw_{s}"
    jetzt = datetime.now(timezone.utc).isoformat()
    db.users.insert_one({"id": chef_id, "dealer_id": dealer_id, "role": "dealer", "active": True,
                         "email": f"nw{s}@t.invalid", "created_at": jetzt})
    db.dealers.insert_one({"id": dealer_id, "user_id": chef_id, "company_name": "NW", "created_at": jetzt})
    db.driver_accounts.insert_one({"id": fahrer_id, "display_name": "F", "active": True, "created_at": jetzt})
    try:
        chef = {"Authorization": f"Bearer {K.token_direkt(chef_id)}"}
        fahrer = {"Authorization": f"Bearer {K.token_direkt(fahrer_id, 'driver_accounts')}"}
        api = K.API
        st = requests.get(f"{api}/neuigkeiten/stand", headers=chef, timeout=10)
        assert st.status_code == 200 and st.json()["ungelesen"] > 0
        assert requests.post(f"{api}/neuigkeiten/gibt-es-nicht/gelesen", headers=chef, timeout=10).status_code == 404
        h1 = requests.post(f"{api}/neuigkeiten/hinweis", headers=chef, timeout=10).json()["hinweis"]
        h2 = requests.post(f"{api}/neuigkeiten/hinweis", headers=chef, timeout=10).json()["hinweis"]
        assert h1 and h2 is None, "zweiter Aufruf (anderes Geraet) am selben Tag: kein Hinweis"
        assert requests.post(f"{api}/neuigkeiten/{h1['id']}/gelesen", headers=chef, timeout=10).status_code == 200
        f = requests.get(f"{api}/driver/neuigkeiten", headers=fahrer, timeout=10)
        assert f.status_code == 200 and all(e["id"] for e in f.json()["eintraege"])
        assert requests.get(f"{api}/neuigkeiten", headers=fahrer, timeout=10).status_code in (401, 403)
        assert requests.get(f"{api}/driver/neuigkeiten", headers=chef, timeout=10).status_code in (401, 403)
        assert requests.get(f"{api}/appointments/gibt-es-nicht/abweichungen", headers=chef,
                            timeout=10).status_code == 404
    finally:
        db.users.delete_one({"id": chef_id})
        db.dealers.delete_one({"id": dealer_id})
        db.driver_accounts.delete_one({"id": fahrer_id})
        db.neuigkeiten_stand.delete_many({"konto": {"$in": [f"u:{chef_id}", f"f:{fahrer_id}"]}})
