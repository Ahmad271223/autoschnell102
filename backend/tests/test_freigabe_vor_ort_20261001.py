# -*- coding: utf-8 -*-
"""Wunsch Ahmad 01.10.2026: "Wenn der Fahrer vor Ort Maengel feststellt, z. B. Ausstattungen nicht dabei
sind oder abweichen, bekommt der Chef das nicht. Der Chef soll alle Daten bekommen, die abweichen, nicht
angegeben waren, fehlen usw."

Befund: /protocols/zur-freigabe lieferte die Haken (dokumente/ausstattung/zustand) schon mit, die
Freigabe-Seite zeigte sie aber nie — sichtbar waren sie nur im Protokoll-PDF und in der KI-Karte.
Jetzt: protokoll_vergleich.vor_ort_befunde fasst sie zusammen ("vor_ort"), die Seite zeigt den Block
"Vor Ort festgestellt" (Freigaben.jsx, VorOrt)."""
from pathlib import Path

import protokoll_vergleich as PV

from test_befunde_runde33_freigaben import _abholung, _jetzt, _modul, _protokoll, welt  # noqa: F401

FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"


def _p():
    return _modul("routes.protocols")


def test_01_zusammenfassung_fehlt_defekt_anders_mangel_und_offen():
    P = _p()
    doc = {"features": {"Sitzheizung": "fehlt", "Navigationssystem": True, "Anhängerkupplung": "defekt",
                        "Panoramadach": "anders", "Standheizung": False},
           "documents": {d: True for d in P.DOCUMENT_ITEMS},
           "condition": {"mileage": "85000", "fuel_level": "1/2", "tire_profile": "5 mm", "driving": "ok",
                         "battery": "schwach", "warning_lights": "ja", "clean_inside": "gut", "clean_outside": "mittel"}}
    doc["documents"]["Fahrzeugbrief / Zulassung Teil II"] = False
    del doc["documents"]["Bedienungsanleitung"]                  # nicht beantwortet
    v = PV.vor_ort_befunde(doc, condition_fields=P.CONDITION_FIELDS, document_items=P.DOCUMENT_ITEMS,
                           ausstattung=["Sitzheizung", "Navigationssystem", "Anhängerkupplung", "Panoramadach",
                                        "Standheizung", "Klimaautomatik"])
    assert [(x["name"], x["art"], x["befund"]) for x in v["ausstattung"]] == [
        ("Sitzheizung", "fehlt", "fehlt komplett"), ("Anhängerkupplung", "defekt", "vorhanden, defekt"),
        ("Panoramadach", "anders", "anders als beschrieben"), ("Standheizung", "fehlt", "fehlt komplett"),
        ("Klimaautomatik", "offen", "keine Angabe")]
    assert [(x["name"], x["art"]) for x in v["dokumente"]] == [("Fahrzeugbrief / Zulassung Teil II", "fehlt"),
                                                               ("Bedienungsanleitung", "offen")]
    assert [(x["schluessel"], x["art"], x["befund"]) for x in v["zustand"]] == [
        ("battery", "mangel", "schwach"), ("warning_lights", "mangel", "ja"), ("clean_outside", "hinweis", "mittel")]
    assert (v["anzahl"], v["hinweise"], v["offen"]) == (7, 1, 2)
    # alles in Ordnung -> leere Listen, Zaehler null
    gut = {"features": {"Sitzheizung": True}, "documents": {d: True for d in P.DOCUMENT_ITEMS},
           "condition": {"mileage": "1", "fuel_level": "voll", "tire_profile": "x", "driving": "ok", "battery": "ok",
                         "warning_lights": "nein", "clean_inside": "gut", "clean_outside": "gut"}}
    v2 = PV.vor_ort_befunde(gut, condition_fields=P.CONDITION_FIELDS, document_items=P.DOCUMENT_ITEMS,
                            ausstattung=["Sitzheizung"])
    assert v2 == {"ausstattung": [], "dokumente": [], "zustand": [], "anzahl": 0, "hinweise": 0, "offen": 0}
    # kaputte Altdaten (Ausstattung als Text, Dokumente kein dict) werfen nicht
    v3 = PV.vor_ort_befunde({"features": "x", "documents": [], "condition": None},
                            condition_fields=P.CONDITION_FIELDS, document_items=["A"], ausstattung="Sitzheizung, Navi")
    assert [x["name"] for x in v3["ausstattung"]] == ["Sitzheizung", "Navi"] and v3["dokumente"][0]["art"] == "offen"


def test_02_freigabe_liste_traegt_den_block(welt):  # noqa: F811
    w = welt
    P = _p()
    aid = _abholung(w, "vorort", fahrzeug_extra={"features": ["Sitzheizung", "Navigationssystem", "Anhängerkupplung"]})
    dokumente = {d: True for d in P.DOCUMENT_ITEMS}
    dokumente["Zweitsatz Reifen"] = False
    _protokoll(w, aid, P, status=P.ZUR_FREIGABE, erstmals_abgeschickt_am=_jetzt(),
               features={"Sitzheizung": "fehlt", "Navigationssystem": True},
               documents=dokumente,
               condition={"mileage": "85000", "fuel_level": "1/4", "tire_profile": "4 mm", "driving": "Mängel",
                          "battery": "ok", "warning_lights": "nein", "clean_inside": "schlecht", "clean_outside": "gut"})
    e = w.run(P.protokolle_zur_freigabe(w.chef))[0]
    v = e["vor_ort"]
    assert [(x["name"], x["befund"]) for x in v["ausstattung"]] == [("Sitzheizung", "fehlt komplett"),
                                                                    ("Anhängerkupplung", "keine Angabe")]
    assert [(x["name"], x["befund"]) for x in v["dokumente"]] == [("Zweitsatz Reifen", "fehlt")]
    assert [(x["name"], x["befund"]) for x in v["zustand"]] == [("Fahrverhalten (Probefahrt)", "Mängel"),
                                                                ("Sauberkeit Innenraum", "schlecht")]
    assert (v["anzahl"], v["offen"]) == (4, 1)
    # die rohen Haken bleiben weiter in der Antwort (fuer das PDF und die KI)
    assert e["ausstattung"] == {"Sitzheizung": "fehlt", "Navigationssystem": True}
    assert e["dokumente"]["Zweitsatz Reifen"] is False


def test_03_seite_zeigt_den_block():
    quelle = (FRONTEND / "pages" / "app" / "Freigaben.jsx").read_text(encoding="utf-8")
    assert "function VorOrt(" in quelle and "<VorOrt eintrag={e} />" in quelle
    assert "Vor Ort festgestellt" in quelle and "ohne Befund" in quelle
