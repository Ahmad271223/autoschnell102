# -*- coding: utf-8 -*-
"""KI-Block der Pruefliste vom 30.09.2026 (Nr. 11-23, 30) — ohne echten KI-Aufruf.

  Q1  Quelle vertraut = exakte Domain (kein Teilstring: "atu" in "Reparatur", "adac.de.beispiel.com");
      der Quellenname allein genuegt nur als ganzes Wort auf einem ECHTEN Suchtreffer des Laufs
  Q2  jede Zeile des Datenblocks wird geprueft (belegt, vertraut, plausibel, ungefaehr) — das Ergebnis
      steht im Text fuer die Bewertung ("Gepruefte Werte" / "NICHT verwenden")
  Q3  gelernt wird nur Geprueftes, dieselbe Aussage derselben Quelle je Segment nur einmal
  Q4  eigene Daten: Belege statt Zeilen, Median je Quelle, ungefaehre Werte zaehlen nicht fuer "reicht",
      kein gemeinsames Limit, markengebundene Arten ohne Stufe "alle"
  Q5  eigene Daten werden nur angewendet, wenn sie reichen; ein Kostenvoranschlag bleibt stehen
  Q6  auffaelliger Kostenvoranschlag: Annahme + Hinweis; Datenlage sinkt bei ungefaehrem Webwert
  Q7  je Position steht die Grundlage im Ergebnis (Web geprueft / eigene Daten / Markttabelle / Startwert)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _jetzt, _module, welt  # noqa: E402,F401

Q = _module("ai.quellen")
MD = _module("ai.marktdaten")
KX = _module("ai.kontext")
PB = _module("ai.preisbasis")


def _paket(*positionen, marke="Testmarke", modell="Alpha 2.0"):
    return {"vehicle": {"make": marke, "model": modell, "age_years": 5}, "damages": list(positionen),
            "prices": {"agreed_price_eur": 10000}, "market": {"x": 1}}


def _pos(pid, key, low=80, median=150, high=250, **ref):
    return {"id": pid, "type": "delle", "label": "Delle", "zone": "Tür",
            "repair_reference": {"key": key, "low": low, "median": median, "high": high, "source": "AutoSchnell-Startwerte",
                                 **ref}}


def _fall(zeilen, hosts):
    return {"status": "ok", "text": "Bericht\n" + MD.DATEN_MARKER + "\n" + "\n".join(zeilen), "hosts": hosts,
            "quellen": [{"url": f"https://{h}/seite", "titel": h} for h in hosts]}


# ------------------------------------------------ Q1
def test_q1_quelle_nur_ueber_exakte_domain():
    assert Q.host_von("https://www.ADAC.de/rund-ums-fahrzeug?x=1") == "adac.de"
    assert Q.host_von("preise.fairgarage.com/x") == "preise.fairgarage.com" and Q.host_von("adac.de:443/x") == "adac.de"
    assert Q.host_von("") == "" and Q.host_von("kein host") == "" and Q.host_von("u") == ""
    assert Q.host_von("https://nutzer:pw@adac.de.beispiel.com/x") == "adac.de.beispiel.com"
    assert Q.stamm_domain("www.preise.adac.de") == "adac.de"
    # exakt oder Unterdomain — kein Teilstring mehr
    for gut in ("adac.de", "preise.adac.de", "fairgarage.com", "dat.de", "atu.de", "boschcarservice.com"):
        assert Q.host_vertraut(gut), gut
    for schlecht in ("adac.de.beispiel.com", "notadac.de", "mein-adac.de.evil.org", "datenbank-preise.de",
                     "reparatur24.de", "update.example.org", "bosch.evil.com", "tuev.example.net", ""):
        assert not Q.host_vertraut(schlecht), schlecht
    # Fachbetriebe: Stichwort nur im Namen der eingetragenen Domain
    assert Q.host_vertraut("dellentechnik-mueller.de") and Q.host_vertraut("www.smart-repair-hamburg.de")
    assert not Q.host_vertraut("dellen.evil.com") and not Q.host_vertraut("smartrepair.foren.example.org")
    # der Quellenname: nur als ganzes Wort, nur auf einem belegten Treffer
    assert Q.name_passt("ADAC (2024)", "adac.de") and Q.name_passt("Dellen Doktor", "dellen-doktor.de")
    assert not Q.name_passt("Reparaturkosten-Portal", "atu.de") and not Q.name_passt("Datenbank", "dat.de")
    assert not Q.name_passt("", "adac.de")
    assert Q.belegter_host("ADAC", "", ["adac.de", "foren.example.org"]) == "adac.de"
    assert Q.belegter_host("ADAC", "https://www.adac.de/x", ["foren.example.org"]) == ""          # nicht unter den Treffern
    assert Q.belegter_host("irgendwer", "https://preise.adac.de/y", ["adac.de"]) == "adac.de"     # gleiche Domain belegt
    assert Q.belegter_host("ADAC", "https://adac.de/x", []) == "" and Q.belegter_host("ADAC", "", None) == ""
    assert MD.quelle_vertraut("ADAC", "https://www.adac.de/x") and not MD.quelle_vertraut("ADAC", "")
    assert not MD.quelle_vertraut("Reparaturkosten-Datenbank", "https://blog.example.org/x")
    assert not MD.quelle_vertraut("ADAC", "https://adac.de.beispiel.com/x")
    assert MD.quelle_vertraut("ADAC", "", ["adac.de"]) and not MD.quelle_vertraut("ADAC", "https://adac.de/x", ["blog.example.org"])


# ------------------------------------------------ Q2
def test_q2_jede_zeile_wird_geprueft_und_die_bewertung_erfaehrt_es():
    zeilen = MD._daten_parsen("x\n###DATEN\nd1|120|200|160|ADAC|https://www.adac.de/x|genau\n"
                              "d2|100|300|200|FairGarage|https://fairgarage.com/y|ungefaehr\nd3|1|2|1|x|u\n")
    assert zeilen[0]["ungefaehr"] is False and zeilen[1]["ungefaehr"] is True and "ungefaehr" not in zeilen[2]
    assert "passung" in MD.DATEN_ANWEISUNG and "VOLLSTAENDIGE Adresse" in MD.DATEN_ANWEISUNG
    paket = _paket(_pos("d1", "delle_klein"), _pos("d2", "delle_mittel"), _pos("d3", "kratzer_kurz"),
                   _pos("d4", "rost_klein"), _pos("d5", "delle_gross", low=200, median=400, high=600))
    fall = _fall(["d1|120|200|160|ADAC|https://www.adac.de/x|genau",
                  "d2|100|300|200|Reparaturkosten-Portal|https://reparatur24.de/x|genau",      # Treffer, aber unbekannt
                  "d3|100|200|150|ADAC|https://www.adac.de/kratzer",                           # ok, ohne Passung
                  "d4|100|200|150|DEKRA|https://www.dekra.de/rost|genau",                      # nicht unter den Treffern
                  "d5|5000|9000|7000|ADAC|https://www.adac.de/delle|genau",                    # unplausibel
                  "d1|90|150|120|FairGarage||ungefaehr",                                       # Name als ganzes Wort
                  "fremd|1|2|1|ADAC|https://www.adac.de/z"],                                   # keine Position
                 ["adac.de", "reparatur24.de", "fairgarage.com"])
    p = {(z["id"], z["quelle"]): z for z in MD.recherche_pruefen(fall, paket, "vertrag")}
    assert len(p) == 6 and fall["pruefung"] == list(p.values())
    assert p[("d1", "ADAC")] == {**p[("d1", "ADAC")], "host": "adac.de", "belegt": True, "vertraut": True,
                                 "plausibel": True, "ok": True, "ungefaehr": False}
    assert p[("d2", "Reparaturkosten-Portal")]["belegt"] and not p[("d2", "Reparaturkosten-Portal")]["vertraut"]
    assert not p[("d2", "Reparaturkosten-Portal")]["ok"], '"atu" in "Reparatur" genuegt nicht mehr'
    assert p[("d3", "ADAC")]["ok"] and p[("d3", "ADAC")]["ungefaehr"] is False
    assert not p[("d4", "DEKRA")]["belegt"] and not p[("d4", "DEKRA")]["ok"], "bekannte Domain, aber kein Treffer dieses Laufs"
    assert p[("d5", "ADAC")]["vertraut"] and not p[("d5", "ADAC")]["plausibel"] and not p[("d5", "ADAC")]["ok"]
    assert p[("d1", "FairGarage")]["host"] == "fairgarage.com" and p[("d1", "FairGarage")]["ok"] and p[("d1", "FairGarage")]["ungefaehr"]
    # zweiter Aufruf prueft nicht neu
    assert MD.recherche_pruefen(fall, paket, "vertrag") is fall["pruefung"]
    text = MD.fall_als_text(fall)
    assert text.startswith("Marktrecherche zu diesem Fall") and MD.DATEN_MARKER not in text
    assert "Gepruefte Werte" in text and "id d1: 120-200 EUR, typisch 160 (adac.de)" in text
    assert "nur Orientierungswert" in text
    nicht = text.split("NICHT verwenden", 1)[1]
    assert "id d2" in nicht and "id d4" in nicht and "id d5: 5000-9000" in nicht and "id d1" not in nicht.split("Bericht")[0]
    assert len(text) <= MD.FALL_TEXT_MAX_ZEICHEN
    # ohne Pruefung (alter gespeicherter Lauf) bleibt der Text wie frueher
    assert "Gepruefte Werte" not in MD.fall_als_text({"text": fall["text"]})
    # gepruefte Webwerte werden zur Referenz; "genau" geht vor "ungefaehr"
    assert MD.recherche_anwenden(paket, "vertrag", fall) == 2
    r1, r3 = paket["damages"][0]["repair_reference"], paket["damages"][2]["repair_reference"]
    assert (r1["low"], r1["median"], r1["high"]) == (120, 160, 200) and r1["source"] == "Websuche (adac.de)"
    assert r1["web_geprueft"] is True and r1["approximate"] is False and r3["web_geprueft"] is True
    for unberuehrt in (1, 3, 4):
        assert paket["damages"][unberuehrt]["repair_reference"]["source"] == "AutoSchnell-Startwerte"
    assert paket["precomputed"]["repair_reference_total_eur"] == 160 + 150 + 150 + 150 + 400
    # nur ungefaehre Werte: Referenz gilt, ist aber markiert -> Datenlage sinkt
    p2 = _paket(_pos("d1", "delle_klein"))
    assert KX.datenlage(p2) == "hoch"
    f2 = _fall(["d1|90|150|120|FairGarage|https://fairgarage.com/x|ungefaehr"], ["fairgarage.com"])
    MD.recherche_pruefen(f2, p2, "vertrag")
    assert MD.recherche_anwenden(p2, "vertrag", f2) == 1 and p2["damages"][0]["repair_reference"]["approximate"] is True
    assert KX.datenlage(p2) == "mittel"
    # Lauf ohne Suchtreffer: nichts ist belegt, nichts wird angewendet
    p3 = _paket(_pos("d1", "delle_klein"))
    f3 = _fall(["d1|120|200|160|ADAC|https://www.adac.de/x"], [])
    assert [z["ok"] for z in MD.recherche_pruefen(f3, p3, "vertrag")] == [False]
    assert MD.recherche_anwenden(p3, "vertrag", f3) == 0 and MD.recherche_pruefen(None, p3, "vertrag") == []


# ------------------------------------------------ Q3
def test_q3_gelernt_wird_nur_geprueftes_und_nichts_doppelt(welt):  # noqa: F811
    db, s = welt.db, welt.w.s
    key = f"delle_q3_{s}"
    paket = _paket(_pos("d1", key), marke=f"Marke{s}")
    try:
        fall = _fall(["d1|120|200|160|ADAC|https://www.adac.de/x|genau", "d1|1|2|1|Forum|https://foren.example.org/x",
                      "d1|100|220|150|Reparaturkosten-Datenbank|", "d1|130|210|170|DEKRA|https://dekra.de/x|ungefaehr"],
                     ["adac.de", "foren.example.org", "dekra.de"])
        assert welt.run(MD.lernen_aus_recherche(fall, paket, "vertrag")) == 2
        docs = welt.run(db.ki_reparaturpreise.find({"key": key}, {"_id": 0}).sort("typisch_eur", 1).to_list(10))
        assert [(d["host"], d["typisch_eur"], d["ungefaehr"]) for d in docs] == [("adac.de", 160, False), ("dekra.de", 170, True)]
        assert docs[0]["fp"] == f"{key}|adac.de|120|200|160" and docs[0]["marke"] == f"marke{s}".lower()
        assert {v["grund"] for v in fall["verworfen"]} == {"Quelle unbekannt", "nicht belegt"}
        # dieselben Treffer noch einmal (gleiches Segment): nichts Neues
        nochmal = _fall(["d1|120|200|160|ADAC (Beispielpreis)|https://www.adac.de/andere-seite",
                         "d1|120|200|160|ADAC|https://www.adac.de/x"], ["adac.de"])
        assert welt.run(MD.lernen_aus_recherche(nochmal, paket, "vertrag")) == 0
        assert welt.run(db.ki_reparaturpreise.count_documents({"key": key})) == 2
        # anderes Preisband derselben Quelle: neuer Beleg; anderes Fahrzeugsegment: eigener Eintrag
        assert welt.run(MD.lernen_aus_recherche(_fall(["d1|140|260|190|ADAC|https://adac.de/x"], ["adac.de"]), paket, "vertrag")) == 1
        anderes = _paket(_pos("d1", key), marke=f"Andere{s}")
        assert welt.run(MD.lernen_aus_recherche(_fall(["d1|120|200|160|ADAC|https://adac.de/x"], ["adac.de"]), anderes, "vertrag")) == 1
        # Lauf ohne Treffer oder mit Status != ok lernt nichts
        assert welt.run(MD.lernen_aus_recherche(_fall(["d1|150|250|200|ADAC|https://adac.de/x"], []), paket, "vertrag")) == 0
        assert welt.run(MD.lernen_aus_recherche({"status": "fehler", "text": fall["text"]}, paket, "vertrag")) == 0
        assert welt.run(db.ki_reparaturpreise.count_documents({"key": key})) == 4
    finally:
        welt.run(db.ki_reparaturpreise.delete_many({"key": key}))


# ------------------------------------------------ Q4
def _doc(key, quelle, typisch, *, host=None, lo=80, hi=250, marke="testmarke", modell="Alpha 2.0", alter="3-7", **extra):
    d = {"key": key, "typ": "delle", "marke": marke, "modell": modell, "alter_klasse": alter, "min_eur": lo, "max_eur": hi,
         "typisch_eur": typisch, "quelle": quelle, "url": "", "art": "vertrag", "stand": _jetzt(), **extra}
    if host:
        d["host"] = host
    return d


def test_q4_eigene_daten_belege_median_je_quelle_und_markenbindung(welt):  # noqa: F811
    db, s = welt.db, welt.w.s
    key, key2 = f"delle_q4_{s}", f"technik_q4_{s}"
    paket = _paket(_pos("d1", key))

    def _setzen(docs, k=key, p=paket):
        welt.run(db.ki_reparaturpreise.delete_many({"key": k}))
        if docs:
            welt.run(db.ki_reparaturpreise.insert_many([dict(d) for d in docs]))
        return welt.run(MD.eigene_referenzen(p, "vertrag")).get(k)
    try:
        # 20mal derselbe Wert von A + 1mal B: zwei Belege, reicht NICHT (vorher: 21 Werte, 2 Quellen -> reicht)
        e = _setzen([_doc(key, "ADAC", 150, host="adac.de") for _ in range(20)] + [_doc(key, "FairGarage", 300, host="fairgarage.com")])
        assert e["n"] == 2 and e["quellen_n"] == 2 and e["reicht"] is False
        assert [p["id"] for p in MD.recherche_noetig(paket, "vertrag", {key: e})] == ["d1"]
        # "ADAC" und "adac.de" sind EINE Quelle, sobald die Domain bekannt ist
        e = _setzen([_doc(key, "ADAC" if i % 2 else "adac.de, 2024", 150 + i, host="adac.de") for i in range(6)])
        assert e["n"] == 6 and e["quellen_n"] == 1 and e["reicht"] is False
        # fuenf verschiedene Belege aus zwei Quellen reichen; der Median gewichtet je Quelle:
        # A liefert 100,100,100,100 (versch. Spannen), B liefert 300 -> Median ueber Quellen = 200 (nicht 100)
        e = _setzen([_doc(key, "ADAC", 100, host="adac.de", lo=60 + i) for i in range(4)] + [_doc(key, "FairGarage", 300, host="fairgarage.com")])
        assert e["reicht"] is True and e["n"] == 5 and e["median"] == 200 and e["stufe"] == "marke_modell_alter"
        assert MD.recherche_noetig(paket, "vertrag", {key: e}) == []
        # ungefaehre Orientierungswerte zaehlen fuer "reicht" nicht mit
        e = _setzen([_doc(key, "ADAC", 100 + i, host="adac.de", ungefaehr=i < 2) for i in range(4)]
                    + [_doc(key, "FairGarage", 300, host="fairgarage.com")])
        assert e["n"] == 5 and e["reicht"] is False
        # kein gemeinsames Limit mehr: ein zweiter Schluessel mit sehr vielen Werten verdraengt den ersten nicht
        voll = f"kratzer_q4_{s}"
        welt.run(db.ki_reparaturpreise.insert_many([_doc(voll, "ADAC", 100 + i, host="adac.de") for i in range(320)]))
        beide = _paket(_pos("d1", key), _pos("d2", voll))
        welt.run(db.ki_reparaturpreise.delete_many({"key": key}))
        welt.run(db.ki_reparaturpreise.insert_many([dict(_doc(key, "ADAC", 100, host="adac.de", lo=60 + i), stand="2026-09-01T00:00:00+00:00")
                                                    for i in range(4)] + [dict(_doc(key, "FairGarage", 300, host="fairgarage.com"),
                                                                               stand="2026-09-01T00:00:00+00:00")]))
        erg = welt.run(MD.eigene_referenzen(beide, "vertrag"))
        assert erg[key]["reicht"] is True and erg[voll]["n"] == 30 and MD.EIGENE_JE_SCHLUESSEL_MAX == 300
        welt.run(db.ki_reparaturpreise.delete_many({"key": voll}))
        # markengebundene Art (Technik): Werte einer ANDEREN Marke sind keine Grundlage — keine Stufe "alle"
        assert MD._markengebunden(key2) and MD._markengebunden("licht_led") and MD._markengebunden("keys_fehlt")
        assert not MD._markengebunden("delle_klein") and not MD._markengebunden("kratzer_kurz")
        p_tech = _paket(_pos("t1", key2))
        fremd = [_doc(key2, "ADAC" if i % 2 else "FairGarage", 900 + i, host="adac.de" if i % 2 else "fairgarage.com",
                      marke="andere") for i in range(6)]
        assert _setzen(fremd, key2, p_tech) is None
        # dieselben Werte bei einer Karosserie-Art: Stufe "alle" gilt weiter
        e = _setzen([dict(d, key=key) for d in fremd])
        assert e["stufe"] == "alle" and e["reicht"] is True
        # gleiche Marke: Technik reicht ueber die Stufe "marke"
        e = _setzen([dict(d, marke="testmarke", modell="Beta", alter_klasse="12+") for d in fremd], key2, p_tech)
        assert e["stufe"] == "marke" and e["reicht"] is True
    finally:
        welt.run(db.ki_reparaturpreise.delete_many({"key": {"$in": [key, key2, f"kratzer_q4_{s}"]}}))


# ------------------------------------------------ Q5 / Q6 / Q7
def test_q5_bis_q7_anwenden_kostenvoranschlag_und_grundlage():
    # Q5: eigene Daten nur, wenn sie reichen (vorher ueberschrieb EIN Wert von irgendeinem Auto die Referenz)
    paket = _paket(_pos("d1", "delle_klein"), _pos("d2", "kratzer_kurz"),
                   _pos("d3", "technik_getriebe", low=1080, median=1200, high=1440, basis="kostenvoranschlag",
                        source="Kostenvoranschlag Werkstatt"))
    eigene = {"delle_klein": {"low": 10, "median": 20, "high": 30, "source": "eigene Datenbank (n=1)", "n": 1, "reicht": False},
              "kratzer_kurz": {"low": 100, "median": 140, "high": 190, "source": "eigene Datenbank (n=5)", "n": 5, "reicht": True},
              "technik_getriebe": {"low": 1, "median": 2, "high": 3, "source": "eigene Datenbank (n=9)", "n": 9, "reicht": True}}
    assert KX.eigene_anwenden(paket, eigene) == 1
    r1, r2, r3 = (p["repair_reference"] for p in paket["damages"])
    assert r1["median"] == 150 and r1["source"] == "AutoSchnell-Startwerte" and "own_data_n" not in r1
    assert r2["median"] == 140 and r2["own_data_n"] == 5
    assert r3["median"] == 1200 and r3["source"] == "Kostenvoranschlag Werkstatt", "Kostenvoranschlag bleibt stehen"
    # ... auch gegen Markttabelle und Websuche
    best = {"status": "Werkstatt hat Diagnose bestätigt", "kva_eur": "1200"}
    markt = {"status": "ok", "stand": "2026-09-30", "positionen": [
        {"typ": "technical", "auspraegung": "Getriebe/Kupplung (Ruckeln, Schaltfehler, rutscht)", "min_eur": 1, "max_eur": 3,
         "typisch_eur": 2, "quelle": "x"}]}
    ref = KX.reparaturreferenz({"type_key": "technik", "severity_data": best, "zone": "Getriebe/Kupplung"}, markt)
    assert ref["basis"] == "kostenvoranschlag" and ref["median"] == 1200 and ref["source"] == "Kostenvoranschlag Werkstatt"
    ohne = KX.reparaturreferenz({"type_key": "technik", "severity_data": {"status": "nur Symptom"}, "zone": "Getriebe/Kupplung"}, markt)
    assert ohne["source"].startswith("Marktdaten 2026-09-30") and ohne["low"] == 1
    p_kva = _paket({"id": "t1", "type": "technik", "label": "Getriebe", "zone": "Getriebe/Kupplung", "repair_reference": dict(ref)})
    f = _fall(["t1|300|900|600|FairGarage|https://fairgarage.com/x|genau"], ["fairgarage.com"])
    MD.recherche_pruefen(f, p_kva, "vertrag")
    assert MD.recherche_anwenden(p_kva, "vertrag", f) == 0 and p_kva["damages"][0]["repair_reference"]["median"] == 1200

    # Q6: Kostenvoranschlag weit ueber dem aufwendigsten ueblichen Fall (Getriebe: 3.500 EUR) -> Annahme + Hinweis
    assert ref["assumption_made"] is False and ref.get("kva_auffaellig") is False
    hoch = PB.referenz("technik", {**best, "kva_eur": "8000"}, "Getriebe/Kupplung")
    assert hoch["median"] == 8000 and hoch["kva_auffaellig"] is True and hoch["assumption_made"] is True
    grenze = PB.referenz("technik", {**best, "kva_eur": "7000"}, "Getriebe/Kupplung")
    assert grenze["kva_auffaellig"] is False and PB.KVA_AUFFAELLIG_FAKTOR == 2.0
    p_hoch = _paket({"id": "t1", "type": "technik", "label": "Technischer Mangel", "zone": "Getriebe/Kupplung",
                     "repair_reference": hoch})
    hinweise = KX.referenz_hinweise(p_hoch)
    assert len(hinweise) == 1 and "Kostenvoranschlag" in hinweise[0] and "8000" in hinweise[0] and "Beleg" in hinweise[0]
    assert KX.referenz_hinweise(paket) == [] and KX.datenlage(p_hoch) == "mittel"

    # Q7: Grundlage je Position
    assert KX.grundlage(r1) == {"art": "start", "text": "Startwert (nicht recherchiert)"}
    assert KX.grundlage(r2) == {"art": "eigene", "text": "Eigene Daten (5 Belege)"}
    assert KX.grundlage(r3) == {"art": "kva", "text": "Kostenvoranschlag Werkstatt"}
    assert KX.grundlage(hoch)["text"].endswith("Beleg prüfen")
    assert KX.grundlage(ohne) == {"art": "markt", "text": "Markttabelle 2026-09-30"}
    web = {"key": "delle_klein", "source": "Websuche (adac.de, fairgarage.com)", "web_geprueft": True, "approximate": False}
    assert KX.grundlage(web) == {"art": "web", "text": "Web geprüft: adac.de, fairgarage.com"}
    assert KX.grundlage({**web, "approximate": True})["text"].endswith("(nur Orientierungswert)")
    assert KX.grundlage(None) is None and KX.grundlage({"manual_review": True}) is None
    ergebnis = {"items": [{"source_id": "d1"}, {"source_id": "d2"}, {"source_id": "fehlt"}]}
    KX.grundlagen_setzen(ergebnis, {"d1": r1, "d2": r2})
    assert [i.get("grundlage", {}).get("art") for i in ergebnis["items"]] == ["start", "eigene", None]



# ------------------------------------------------ Markttabelle: ein Merker fuer beide Wege (Nr. 10)
def test_markttabelle_stuendlicher_weg_nutzt_den_merker(welt, monkeypatch):  # noqa: F811
    db = welt.db
    vorher = welt.run(db.ki_marktdaten.find_one({"_id": MD.DOK_ID}))
    laeufe = []

    async def _aktualisieren(db_, erzwingen=False):
        laeufe.append(welt.run is not None and (await db_.ki_marktdaten.find_one({"_id": MD.DOK_ID}) or {}).get("lauf_seit"))
        if len(laeufe) == 2:
            raise RuntimeError("Recherche gescheitert")
        return {"status": "ok", "aktualisiert": True}
    monkeypatch.setattr(MD, "aktualisieren", _aktualisieren)
    monkeypatch.setattr(MD, "ki_aktiv", lambda: True)
    monkeypatch.setenv("KI_MARKTANALYSE_AKTIV", "true")
    try:
        welt.run(db.ki_marktdaten.delete_many({"_id": MD.DOK_ID}))
        # kein Lauf unterwegs: der stuendliche Weg setzt den Merker, rechnet und nimmt ihn wieder weg
        assert welt.run(MD.pruefen_und_aktualisieren(db))["status"] == "ok"
        assert len(laeufe) == 1 and laeufe[0], "waehrend des Laufs steht der Merker"
        assert not (welt.run(db.ki_marktdaten.find_one({"_id": MD.DOK_ID})) or {}).get("lauf_seit")
        # auch nach einem Fehler bleibt kein Merker stehen
        try:
            welt.run(MD.pruefen_und_aktualisieren(db))
        except RuntimeError:
            pass
        assert len(laeufe) == 2 and not (welt.run(db.ki_marktdaten.find_one({"_id": MD.DOK_ID})) or {}).get("lauf_seit")
        # ein Lauf ist unterwegs (Knopf "Marktdaten jetzt"): der stuendliche Weg startet keinen zweiten
        assert welt.run(MD.lauf_markieren(db)) is True
        assert welt.run(MD.pruefen_und_aktualisieren(db)) == {"status": "laeuft"} and len(laeufe) == 2
        assert welt.run(MD.aktualisieren_im_hintergrund(db)) == {"status": "laeuft", "gestartet": False}
    finally:
        welt.run(db.ki_marktdaten.delete_many({"_id": MD.DOK_ID}))
        if vorher:
            welt.run(db.ki_marktdaten.insert_one(vorher))


# ------------------------------------------------ Limit beim Anbieter erreicht: Klartext statt Rohfehler
def test_limit_beim_anbieter_gibt_klartext():
    PR = _module("ai.provider")
    roh = ("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached "
           "your specified API usage limits. You will regain access on 2026-10-01 at 00:00 UTC.'}}")
    grund = PR.grund_aus_ausnahme(RuntimeError(roh))
    assert grund.startswith(PR.LIMIT_ERREICHT) and "wieder ab 01.10.2026" in grund and "Limits/Billing" in grund
    assert "Error code" not in grund
    assert PR.grund_aus_ausnahme(RuntimeError("Your credit balance is too low to access the API")).startswith(PR.LIMIT_ERREICHT)
    assert PR.grund_aus_ausnahme(ValueError("irgendwas")) == "ValueError: irgendwas"


# ------------------------------------------------ Kostenfrage 30.09.2026: Tabellenlaeufe je Monat begrenzt
def test_markttabelle_hoechstens_vier_laeufe_je_monat(welt, monkeypatch):  # noqa: F811
    db = welt.db
    vorher = welt.run(db.ki_marktdaten.find_one({"_id": MD.DOK_ID}))
    laeufe = []

    async def _scheitert(db_, erzwingen=False):
        laeufe.append(1)
        # wie ein Fehlversuch nach bezahlter Recherche: Versuch vermerkt, Status fehler
        await db_.ki_marktdaten.update_one({"_id": MD.DOK_ID}, {"$set": {"stand_versuch": "2020-01-01T00:00:00+00:00",
                                                                        "status": "fehler", "grund": "Test"}}, upsert=True)
        return {"status": "fehler", "grund": "Test", "aktualisiert": False}
    monkeypatch.setattr(MD, "aktualisieren", _scheitert)
    monkeypatch.setattr(MD, "ki_aktiv", lambda: True)
    monkeypatch.setenv("KI_MARKTANALYSE_AKTIV", "true")
    monkeypatch.delenv("KI_MARKTDATEN_LAEUFE_MAX", raising=False)
    try:
        welt.run(db.ki_marktdaten.delete_many({"_id": MD.DOK_ID}))
        assert MD.laeufe_max() == 4
        # vier Fehlversuche (stuendlicher Weg; die Wartezeit ist hier schon abgelaufen) laufen ...
        for _ in range(4):
            assert welt.run(MD.pruefen_und_aktualisieren(db))["status"] == "fehler"
        assert len(laeufe) == 4
        # ... der fuenfte startet KEINE Recherche mehr — weder automatisch noch per Knopf
        erg = welt.run(MD.pruefen_und_aktualisieren(db))
        assert erg["status"] == "limit" and "Monatsgrenze" in erg["grund"] and len(laeufe) == 4
        knopf = welt.run(MD.aktualisieren_im_hintergrund(db))
        assert knopf["status"] == "limit" and knopf["gestartet"] is False and len(laeufe) == 4
        d = welt.run(db.ki_marktdaten.find_one({"_id": MD.DOK_ID}))
        assert d["laeufe_n"] == 4 and d["laeufe_monat"] == MD._monat() and not d.get("lauf_seit")
        alarm = welt.run(db.betriebsalarme.find_one({"typ": "ki_marktdaten_limit", "ref": MD._monat()}))
        assert alarm and alarm["offen"] is True
        # neuer Monat: es geht wieder; hoehere Grenze per Umgebung ebenso
        welt.run(db.ki_marktdaten.update_one({"_id": MD.DOK_ID}, {"$set": {"laeufe_monat": "2020-01"}}))
        assert welt.run(MD.pruefen_und_aktualisieren(db))["status"] == "fehler" and len(laeufe) == 5
        assert welt.run(db.ki_marktdaten.find_one({"_id": MD.DOK_ID}))["laeufe_n"] == 1
        welt.run(db.ki_marktdaten.update_one({"_id": MD.DOK_ID}, {"$set": {"laeufe_n": 4}}))
        monkeypatch.setenv("KI_MARKTDATEN_LAEUFE_MAX", "6")
        assert welt.run(MD.lauf_zaehlen(db)) is True and welt.run(MD.lauf_zaehlen(db)) is True
        assert welt.run(MD.lauf_zaehlen(db)) is False
    finally:
        welt.run(db.ki_marktdaten.delete_many({"_id": MD.DOK_ID}))
        welt.run(db.betriebsalarme.delete_many({"typ": "ki_marktdaten_limit"}))
        if vorher:
            welt.run(db.ki_marktdaten.insert_one(vorher))


def test_markttabelle_ausnahme_vermerkt_den_versuch_und_der_zaehler_ueberlebt_den_erfolg(welt, monkeypatch):  # noqa: F811
    db = welt.db
    vorher = welt.run(db.ki_marktdaten.find_one({"_id": MD.DOK_ID}))
    monkeypatch.setattr(MD, "ki_aktiv", lambda: True)
    monkeypatch.setenv("KI_MARKTANALYSE_AKTIV", "true")

    async def _kaputt(**kw):
        raise RuntimeError("Anbieter weg")
    monkeypatch.setattr(MD, "recherche", _kaputt)
    try:
        welt.run(db.ki_marktdaten.delete_many({"_id": MD.DOK_ID}))
        erg = welt.run(MD.aktualisieren(db, erzwingen=True))
        assert erg["status"] == "fehler" and "Anbieter weg" in erg["grund"]
        d = welt.run(db.ki_marktdaten.find_one({"_id": MD.DOK_ID}))
        assert d["status"] == "fehler" and d["stand_versuch"], "sonst recherchiert der naechste Aufraeumlauf in einer Stunde erneut"
        # gleich danach wartet der stuendliche Weg (6 Stunden), statt wieder zu recherchieren
        assert welt.run(MD.pruefen_und_aktualisieren(db))["status"] == "wartet"
        # der Zaehler ueberlebt einen erfolgreichen Lauf
        welt.run(db.ki_marktdaten.update_one({"_id": MD.DOK_ID}, {"$set": {"laeufe_monat": MD._monat(), "laeufe_n": 3}}))

        async def _gut(**kw):
            return {"status": "ok", "text": "Delle 100-200 EUR (ADAC)", "quellen": [{"url": "https://www.adac.de/x", "titel": "ADAC"}],
                    "suchen": 1, "dauer_ms": 1, "usage": {}}

        async def _json(**kw):
            return {"status": "ok", "daten": {"positionen": [{"typ": "delle", "auspraegung": "klein", "min_eur": 100, "max_eur": 200,
                                                             "typisch_eur": 150, "quelle": "ADAC", "hinweis": ""}],
                                              "zusammenfassung": "x"}, "usage": {}}
        monkeypatch.setattr(MD, "recherche", _gut)
        monkeypatch.setattr(MD, "json_bewerten", _json)
        assert welt.run(MD.aktualisieren(db, erzwingen=True))["status"] == "ok"
        d = welt.run(db.ki_marktdaten.find_one({"_id": MD.DOK_ID}))
        assert d["status"] == "ok" and d["laeufe_n"] == 3 and d["laeufe_monat"] == MD._monat()
    finally:
        welt.run(db.ki_marktdaten.delete_many({"_id": MD.DOK_ID}))
        if vorher:
            welt.run(db.ki_marktdaten.insert_one(vorher))
