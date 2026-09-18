# -*- coding: utf-8 -*-
"""Runde 24 (11.09.2026): Kategorie, Navigation und Klimatisierung sind aus
den Filtereinstellungen BEIDER Portale (mobile.de, AutoScout24) entfernt.

Anlass (Wunsch Ahmad): AutoScout24 kann die Kategorie nicht sauber umsetzen
("AutoScout24 hat keine passende Kategorie fuer 'Kombi' — der AutoScout-
Link filtert nicht nach Kategorie."). Navigation und Klimatisierung filterte
ohnehin nur mobile.de.

Geprueft wird:
  1. Standard-Regeln (Inland/Export) ohne die drei Filter.
  2. mobile.de-Link: kein c=, kein climatisation=, kein Navi-Feature —
     auch wenn gespeicherte Alt-Regeln die Schluessel noch enthalten.
  3. AutoScout-Link: kein body=; keine Hinweise zu Kategorie/Navi/Klima
     (Land und Hubraum bleiben).
  4. regeln_validieren verwirft die Alt-Schluessel STILL (kein RegelFehler),
     regeln_lesen ignoriert sie.
  5. In-Prozess gegen eine Wegwerf-DB: Chef speichert Alt-Regeln (200,
     bereinigt gespeichert); ein Sucher, der die unveraenderten Chef-Regeln
     zurueckschickt, bekommt KEINEN eingefrorenen Override; die manuelle
     Suche mit Alt-Regeln liefert Links ohne die Filter und ohne Hinweise.

Die Fahrzeug-Kategorie als DATEN (Inserat, Vertrag, PDF) ist nicht Teil
dieses Strangs und bleibt unberuehrt. Kein Server, keine Anbieter-Abrufe.
"""
import asyncio
import copy
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"

ENTFALLEN = ("category", "climatisation", "features")


def _q(url):
    return parse_qs(urlparse(url).query)


def _alt(basis):
    """Gespeichertes Alt-Regelpaket: alle drei entfallenen Filter aktiv."""
    return {**copy.deepcopy(basis),
            "category": {"mode": "exact"},
            "features": {"navigation": {"mode": "always"}},
            "climatisation": {"mode": "always", "value": "AUTOMATIC_CLIMATISATION"}}


FZ = {"make": "VW", "make_label": "Volkswagen", "model": "Passat", "model_label": "Passat",
      "first_registration": "05/2019", "mileage": 90000, "power_kw": 110, "power_ps": 150,
      "fuel": "DIESEL", "fuel_label": "Diesel", "gearbox": "AUTOMATIC_GEAR",
      "gearbox_label": "Automatik", "category": "EstateCar", "category_label": "Kombi",
      "doors": "FOUR_OR_FIVE", "displacement": 1968,
      "features": ["Navigationssystem", "Klimaautomatik", "Tempomat"]}


# =====================================================================
#                 Einheitentests (ohne Datenbank)
# =====================================================================
def test_01_standard_regeln_ohne_entfallene_filter():
    from mobile_service import DEFAULT_RULES, DEFAULT_EXPORT_RULES
    from regeln import regeln_validieren
    for std in (DEFAULT_RULES, DEFAULT_EXPORT_RULES):
        for k in ENTFALLEN:
            assert k not in std, k
        # Standard ueberlebt den Schreibpfad unveraendert (wichtig fuer den
        # Override-Vergleich der Sucher).
        assert regeln_validieren(copy.deepcopy(std)) == std


@pytest.mark.parametrize("nav_klima", ["always", "exact"])
def test_02_mobile_link_ohne_kategorie_und_klima_auch_bei_altregeln(nav_klima):
    """Alte gespeicherte Regeln (Kategorie, features.navigation, climatisation)
    steuern den Link NICHT mehr — Runde 24.

    Wunsch Ahmad 18.09.2026: Das Navi kommt seitdem aus dem INSERAT, nicht aus
    einer Regel: hat das Inserat eines, haengt `fe=NAVIGATION_SYSTEM` an (der
    Parameter aus seinem geprueften Suchlink; das alte einbuchstabige `f=`
    filterte nie). Der Regel-Modus daneben aendert daran nichts."""
    from mobile_service import DEFAULT_RULES, build_search_url
    alt = _alt(DEFAULT_RULES)
    alt["features"]["navigation"]["mode"] = nav_klima
    alt["climatisation"]["mode"] = nav_klima
    url = build_search_url(dict(FZ), alt)
    q = _q(url)
    assert "c" not in q, url
    assert "climatisation" not in q and "CLIMATISATION" not in url, url
    assert "f" not in q, url                      # das alte, wirkungslose f=
    # FZ hat "Navigationssystem" in der Ausstattung -> Navi-Filter ist gesetzt
    assert q.get("fe") == ["NAVIGATION_SYSTEM"], url
    # ... und ohne Navi im Inserat bleibt er weg, egal welcher Regel-Modus.
    ohne = {**FZ, "features": ["Klimaautomatik", "Tempomat"]}
    assert "fe" not in _q(build_search_url(ohne, alt)), "Navi ohne Navi im Inserat"
    # die uebrigen Regeln greifen weiter
    assert q.get("ft") == ["DIESEL"] and q.get("tr") == ["AUTOMATIC_GEAR"], q
    assert q.get("cn") == ["DE"] and q.get("dam") == ["0"], q


def test_03_autoscout_link_ohne_body_und_ohne_hinweise():
    from mobile_service import DEFAULT_RULES
    from autoscout_service import build_search_url, regeln_nicht_abgebildet
    alt = {**_alt(DEFAULT_RULES), "doors": {"mode": "exact"}}
    for kategorie in ("EstateCar", "OffRoad", "OtherCar", "Kombi"):
        fz = {**FZ, "category": kategorie}
        q = _q(build_search_url(fz, alt))
        assert "body" not in q, (kategorie, q)
        assert q.get("doorfrom") == ["4"] and q.get("doorto") == ["5"], q
        assert regeln_nicht_abgebildet(fz, alt) == [], kategorie
    # Land und Hubraum melden sich weiterhin
    mit_luecken = {**alt, "country": {"mode": "exact", "codes": ["DE", "CH"]},
                   "displacement": {"mode": "tolerance", "value": 100}}
    text = " ".join(regeln_nicht_abgebildet(dict(FZ), mit_luecken))
    assert "CH" in text and "Hubraum" in text, text
    assert "Kategorie" not in text and "Navi" not in text and "Klima" not in text, text


def test_04_regeln_validieren_verwirft_altschluessel_still():
    from mobile_service import DEFAULT_RULES
    from regeln import RegelFehler, regeln_validieren, ENTFERNTE_REGELN, ENTFERNTE_FEATURES
    assert ENTFERNTE_REGELN == {"category", "climatisation"}
    assert ENTFERNTE_FEATURES == {"navigation"}
    sauber = regeln_validieren(_alt(DEFAULT_RULES))
    for k in ENTFALLEN:
        assert k not in sauber, k
    assert sauber == DEFAULT_RULES
    # auch kaputte Alt-Werte sind kein Fehler — sie fallen einfach weg
    assert regeln_validieren({"category": "kaputt", "climatisation": {"mode": "unsinn"},
                              "features": {"navigation": "kaputt"}}) == {}
    assert regeln_validieren({"features": {"navigation": {"mode": "exact"}}}) == {}
    # unbekannte Ausstattungen bleiben ein Fehler (Runde 11)
    with pytest.raises(RegelFehler, match="panorama"):
        regeln_validieren({"features": {"navigation": {"mode": "always"},
                                        "panorama": {"mode": "always"}}})


def test_05_regeln_lesen_ignoriert_altschluessel():
    from mobile_service import DEFAULT_RULES, DEFAULT_EXPORT_RULES
    from regeln import regeln_lesen
    for std in (DEFAULT_RULES, DEFAULT_EXPORT_RULES):
        gelesen = regeln_lesen(_alt(std), std)
        for k in ENTFALLEN:
            assert k not in gelesen, k
        assert gelesen == std


# =====================================================================
#          In-Prozess gegen eine Wegwerf-Datenbank (wird gedroppt)
# =====================================================================
@pytest.fixture
def welt():
    import importlib
    from motor.motor_asyncio import AsyncIOMotorClient
    mods = [importlib.import_module(n) for n in ("deps", "routes.dealer", "routes.manual_search")]
    alt = [(m, getattr(m, "db", None)) for m in mods]
    s = uuid.uuid4().hex[:10]

    class _Ctx:
        pass

    w = _Ctx()
    w.s = s
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_r24_filter_{s}"
    assert w.db_name != "autoschnell"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)

    from mobile_service import DEFAULT_RULES, DEFAULT_EXPORT_RULES
    w.dealer_id = f"d_r24f_{s}"
    w.chef = {"id": f"chef_r24f_{s}", "dealer_id": w.dealer_id, "role": "dealer",
              "active": True, "email": f"chef_r24f_{s}@e2etest-mail.de"}
    w.sucher = {"id": f"su_r24f_{s}", "dealer_id": w.dealer_id, "role": "sucher",
                "active": True, "email": f"su_r24f_{s}@e2etest-mail.de"}
    # Alt-Bestand: Firmen-Regeln MIT den entfallenen Filtern, wie sie vor
    # Runde 24 gespeichert wurden.
    w.alt_inland = _alt(DEFAULT_RULES)
    w.alt_export = _alt(DEFAULT_EXPORT_RULES)
    w.run(w.db.dealers.insert_one({
        "id": w.dealer_id, "user_id": w.chef["id"], "company_name": f"R24 Filter {s}",
        "comparison_rules": copy.deepcopy(w.alt_inland),
        "export_rules": copy.deepcopy(w.alt_export),
        "active_profile": "inland", "created_at": "2026-09-01T10:00:00+00:00"}))
    w.run(w.db.users.insert_many([dict(w.chef), dict(w.sucher)]))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def test_06_sucher_speichert_unveraenderte_altregeln_ohne_override(welt):
    """Die Oberflaeche schickt beim Speichern die effektiven (hier: alten)
    Chef-Regeln zurueck. regeln_validieren verwirft category & Co. — ohne
    normalisierten Vergleich waere das eine "Abweichung" und fror das
    Chef-Paket als persoenlichen Override ein."""
    import routes.dealer as d
    w = welt
    body = d.DealerSettingsIn(comparison_rules=copy.deepcopy(w.alt_inland),
                              export_rules=copy.deepcopy(w.alt_export))
    w.run(d.update_settings(body, user=dict(w.sucher)))
    u = w.run(w.db.users.find_one({"id": w.sucher["id"]}, {"_id": 0}))
    ov = u.get("settings_override") or {}
    assert "comparison_rules" not in ov and "export_rules" not in ov, ov

    # Eine ECHTE Abweichung wird Override — bereinigt, ohne Alt-Schluessel.
    eigen = {**copy.deepcopy(w.alt_inland), "sort": "mileage_asc"}
    w.run(d.update_settings(d.DealerSettingsIn(comparison_rules=eigen),
                            user={**w.sucher, "settings_override": ov}))
    u = w.run(w.db.users.find_one({"id": w.sucher["id"]}, {"_id": 0}))
    gespeichert = (u.get("settings_override") or {}).get("comparison_rules")
    assert gespeichert and gespeichert["sort"] == "mileage_asc", u
    for k in ENTFALLEN:
        assert k not in gespeichert, k
    # Chef-Dokument bleibt unberuehrt
    dealer = w.run(w.db.dealers.find_one({"id": w.dealer_id}, {"_id": 0}))
    assert dealer["comparison_rules"] == w.alt_inland


def test_07_chef_speichert_altregeln_ohne_fehler_bereinigt(welt):
    import routes.dealer as d
    from mobile_service import DEFAULT_RULES, DEFAULT_EXPORT_RULES
    w = welt
    body = d.DealerSettingsIn(comparison_rules=copy.deepcopy(w.alt_inland),
                              export_rules=copy.deepcopy(w.alt_export))
    antwort = w.run(d.update_settings(body, user=dict(w.chef)))   # kein HTTPException
    for feld, std in (("comparison_rules", DEFAULT_RULES), ("export_rules", DEFAULT_EXPORT_RULES)):
        assert antwort[feld] == std, antwort[feld]
        for k in ENTFALLEN:
            assert k not in antwort[feld], (feld, k)


@pytest.mark.parametrize("profil", ["inland", "export"])
def test_08_manuelle_suche_mit_altregeln_ohne_filter_und_hinweise(welt, profil):
    import routes.manual_search as ms
    w = welt
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$set": {"active_profile": profil}}))
    body = ms.ManualSearchIn(make="BMW", model="X5", fuel="Diesel", gearbox="Automatik")
    d = w.run(ms.manual_search(body, user=dict(w.chef)))
    assert d["profil"] == profil
    qm, qa = _q(d["mobile_url"]), _q(d["autoscout_url"])
    assert "c" not in qm and "climatisation" not in qm and "f" not in qm, d["mobile_url"]
    assert "NAVIGATION_SYSTEM" not in d["mobile_url"]
    assert "body" not in qa, d["autoscout_url"]
    text = " ".join(d["hinweise"])
    assert "Kategorie" not in text and "Navi" not in text and "Klima" not in text, d["hinweise"]


def test_09_leere_zahlenfelder_fallen_auf_den_standard_statt_500():
    """16.09.2026 (Pruefung 'Speichern'-Knopf): ein geleertes Zahlenfeld
    (null/"" per Schnittstelle) wurde als None gespeichert, und beide
    Portal-Bauer rechneten int(None) — jeder Vergleich brach mit 500 ab.
    Jetzt faellt der Schluessel weg und der Standard des Bauers gilt;
    offene Bereichsgrenzen (from/to/min/max) bleiben None."""
    import regeln as R
    import mobile_service as MS
    import autoscout_service as AS
    fz = {"make": "VW", "make_label": "Volkswagen", "model": "Passat", "model_label": "Passat",
          "first_registration": "05/2019", "mileage": 90000, "power_kw": 110, "power_ps": 150,
          "fuel": "DIESEL", "gearbox": "AUTOMATIC_GEAR", "doors": "FOUR_OR_FIVE", "displacement": 1968}
    gespeichert = R.regeln_validieren({
        "power": {"mode": "tolerance_ps", "value": None},
        "mileage": {"mode": "plus", "value": ""},
        "first_registration": {"mode": "year_range", "from": "", "to": None},
        "displacement": {"mode": "tolerance", "value": None},
    })
    assert gespeichert["power"] == {"mode": "tolerance_ps"}
    assert gespeichert["mileage"] == {"mode": "plus"}
    assert gespeichert["displacement"] == {"mode": "tolerance"}
    assert gespeichert["first_registration"] == {"mode": "year_range", "from": None, "to": None}
    gelesen = R.regeln_lesen(gespeichert, MS.DEFAULT_RULES)
    q = parse_qs(urlparse(MS.build_search_url(dict(fz), gelesen)).query)
    assert q["pw"] == [f"{MS.ps_to_kw(145)}:{MS.ps_to_kw(155)}"]      # Standard 5 PS
    assert q["ml"] == [":120000"]                                     # Standard +30.000 km
    qa = parse_qs(urlparse(AS.build_search_url(dict(fz), gelesen)).query)
    assert qa["powerfrom"] == [str(AS.ps_to_kw(145))]
    # Altbestand mit None heilt der Lesepfad genauso
    alt = R.regeln_lesen({"power": {"mode": "min_ps", "value": None}}, MS.DEFAULT_RULES)
    assert alt["power"] == {"mode": "min_ps", "value": 5}       # fehlender Wert aus dem Standard
    assert parse_qs(urlparse(MS.build_search_url(dict(fz), alt)).query)["pw"] == [f"{MS.ps_to_kw(145)}:"]
    # nur ein Feld gespeichert (z.B. Wert ohne Modus): Modus kommt aus dem Standard
    teil = R.regeln_lesen({"mileage": {"value": 300001}}, MS.DEFAULT_RULES)
    assert teil["mileage"] == {"mode": "plus", "value": 300001}
    teil_export = R.regeln_lesen({"mileage": {"value": 300001}}, MS.DEFAULT_EXPORT_RULES)
    assert teil_export["mileage"]["mode"] == "ignore"           # Export-Standard: kein km-Limit
    # 0 bleibt 0 (bewusst "exakt"), kein Standard
    assert R.regeln_validieren({"power": {"mode": "tolerance_ps", "value": 0}})["power"]["value"] == 0


def test_10_regelpakete_immer_vollstaendig_und_override_nur_bei_echter_abweichung(welt):
    """16.09.2026 (Pruefung 'Speichern'-Knopf): Firma OHNE gespeichertes
    Export-Paket. Die Oberflaeche bekommt trotzdem vollstaendige Pakete
    (Export-Standard: kein km-Limit, ±10 PS) — vorher zeigte sie Inland-
    Platzhalter (+30.000 km) und speicherte einen geaenderten Wert ohne Modus.
    Ein Sucher, der die vollstaendigen Pakete unveraendert zurueckschickt,
    bekommt keinen Override; ein nur teilweise gespeichertes Paket liest sich
    mit dem Standard aufgefuellt."""
    import routes.dealer as d
    from deps import effective_dealer
    from mobile_service import DEFAULT_RULES, DEFAULT_EXPORT_RULES
    w = welt
    did = f"{w.dealer_id}_ohne"
    chef = {**w.chef, "id": f"{w.chef['id']}_ohne", "dealer_id": did}
    sucher = {**w.sucher, "id": f"{w.sucher['id']}_ohne", "dealer_id": did}
    w.run(w.db.dealers.insert_one({"id": did, "user_id": chef["id"], "company_name": "Ohne Export",
                                   "comparison_rules": {"power": {"mode": "min_ps", "value": 7}},
                                   "active_profile": "inland", "created_at": "2026-09-01T10:00:00+00:00"}))
    w.run(w.db.users.insert_many([dict(chef), dict(sucher)]))
    for konto in (chef, sucher):
        eff = w.run(effective_dealer(dict(konto)))
        assert eff["export_rules"] == DEFAULT_EXPORT_RULES, eff["export_rules"]
        assert eff["comparison_rules"]["power"] == {"mode": "min_ps", "value": 7}
        assert eff["comparison_rules"]["mileage"] == DEFAULT_RULES["mileage"]
    # Datenbank unveraendert (nur Lesepfad)
    roh = w.run(w.db.dealers.find_one({"id": did}, {"_id": 0}))
    assert "export_rules" not in roh and roh["comparison_rules"] == {"power": {"mode": "min_ps", "value": 7}}
    # Sucher schickt die vollstaendigen Pakete unveraendert zurueck -> kein Override
    eff = w.run(effective_dealer(dict(sucher)))
    body = d.DealerSettingsIn(comparison_rules=copy.deepcopy(eff["comparison_rules"]),
                              export_rules=copy.deepcopy(eff["export_rules"]))
    w.run(d.update_settings(body, user=dict(sucher)))
    u = w.run(w.db.users.find_one({"id": sucher["id"]}, {"_id": 0}))
    assert not (u.get("settings_override") or {}), u.get("settings_override")
    # Chef speichert nur ein Feld ohne Modus -> wirksam mit Standard-Modus (Inland: plus)
    w.run(d.update_settings(d.DealerSettingsIn(comparison_rules={"mileage": {"value": 300001}}),
                            user=dict(chef)))
    eff = w.run(effective_dealer(dict(chef)))
    assert eff["comparison_rules"]["mileage"] == {"mode": "plus", "value": 300001}
    assert eff["comparison_rules"]["power"] == DEFAULT_RULES["power"]     # ganzes Paket ersetzt: Rest Standard
