# -*- coding: utf-8 -*-
"""Nach der Abholung: neuer Kaufvertrag mit den Daten von vor Ort
(Wunsch Ahmad 19.09.2026) und alle Ausstattungen im Fahrer-Protokoll.

Der Ablauf stand schon: Fahrer schickt das Protokoll ab -> der Chef sieht es
(mit Zaehler im Menue) -> Fahrer oder Chef traegt den neuen Preis ein -> der
Chef gibt frei -> erst dann darf vor Ort unterschrieben werden. Neu ist, was
DANACH passiert:

  * Was der Fahrer vor Ort anders vorgefunden hat (Erstzulassung, FIN, KM,
    Farbe, ... ) und die neu aufgenommenen Schaeden stehen im neuen Vertrag
    anstelle der alten Angaben.
  * Die bisherige Fassung bleibt als Nachweis im Archiv, die neue ist die
    gueltige — und die App fragt, ob sie per WhatsApp/E-Mail rausgeht.
  * Gab es NICHTS zu aendern, bleibt die alte Fassung unangetastet.
  * Das Online-Protokoll zeigt alle Ausstattungen des Inserats (vorher 20).
"""
import inspect
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import protokoll_vergleich as PV  # noqa: E402

FRONT = BACKEND.parent / "frontend" / "src"


def _zeilen(check, condition=None, vertrag=None, fahrzeug=None):
    werte = PV.vertragswerte(fahrzeug or {"make_label": "VW", "model_label": "Golf",
                                          "first_registration": "06/2019",
                                          "mileage": 90000, "fuel_label": "Benzin",
                                          "exterior_color": "Blau"},
                             vertrag or {"purchase_price": 14500})
    return PV.vergleich(PV.FELDER, check, condition or {}, werte)


# ----------------------------------------------------- Ausstattung
def test_01_alle_ausstattungen_statt_nur_zwanzig():
    import routes.protocols as P
    assert P.AUSSTATTUNG_MAX >= 60, "gut ausgestattete Wagen haben 30-60 Zeilen"
    q = inspect.getsource(P.get_protocol)
    assert '[:AUSSTATTUNG_MAX]' in q and '[:20]' not in q
    # Die Antwort des Fahrers muss ebenso viele Zeilen tragen duerfen
    assert P.FELD_MAX > P.AUSSTATTUNG_MAX


def test_02_deckel_gilt_auch_im_pdf():
    quelle = (BACKEND / "routes" / "protocols.py").read_text(encoding="utf-8")
    assert quelle.count('features") or [])[:AUSSTATTUNG_MAX]') == 3, \
        "Online-Protokoll und beide PDF-Wege"
    assert 'features") or [])[:20]' not in quelle


# ----------------------------------------------------- Korrekturen
def test_03_korrigierte_werte_werden_zu_vertragsfeldern():
    zeilen = _zeilen({"first_registration": {"status": "weicht ab", "value": "03/2018"},
                      "vin": {"status": "weicht ab", "value": "wvwzzz 1kz aw 123456"},
                      "mileage_contract": {"status": "weicht ab", "value": "118500"},
                      "previous_owners": {"status": "weicht ab", "value": "3"},
                      "accident_free": {"status": "Nein"},
                      "make": {"status": "stimmt"}},
                     condition={"mileage": "118500"})
    k = PV.vertrags_korrekturen(zeilen)
    assert k["vehicle_first_registration"] == "03/2018"
    assert k["vehicle_vin"] == "WVWZZZ1KZAW123456", "FIN normalisiert"
    assert k["vehicle_mileage"] == 118500 and isinstance(k["vehicle_mileage"], int)
    assert k["previous_owners"] == 3
    assert k["accident_free"] == "Nein"
    assert "vehicle_make" not in k, '"stimmt" aendert nichts'


def test_04_halbe_eingaben_aendern_den_vertrag_nicht():
    """Ein 'weicht ab' ohne Wert, eine unlesbare Zahl oder ein halbes Datum
    bleiben im Freigabe-Kasten sichtbar — aber sie ueberschreiben nichts."""
    zeilen = _zeilen({"color": {"status": "weicht ab", "value": ""},
                      "mileage_contract": {"status": "weicht ab", "value": "keine Ahnung"},
                      "first_registration": {"status": "weicht ab", "value": "2018"},
                      "commercial": {"status": "unbekannt"}})
    k = PV.vertrags_korrekturen(zeilen)
    assert k == {}, k
    # Der Chef sieht sie trotzdem
    felder = [a["feld"] for a in PV.abweichungen(zeilen)]
    assert "Farbe" in felder and "KM-Stand laut Vertrag" in felder


def test_05_leistung_bleibt_aussen_vor():
    """kW und PS sind zwei Vertragsfelder — der Fahrer tippt nur eine Zahl."""
    zeilen = _zeilen({"power": {"status": "weicht ab", "value": "85"}})
    assert PV.vertrags_korrekturen(zeilen) == {}
    assert "power" not in PV.KORREKTUR_FELDER


# ----------------------------------------------------- Neuer Vertrag
def test_06_neue_fassung_traegt_korrekturen_und_schaeden():
    import routes.contracts as C
    q = inspect.getsource(C.regenerate_contract_for_pickup)
    assert "korrekturen" in q and "neue_schaeden" in q
    assert "contract_dict.update(korrigiert)" in q
    assert 'contract_dict["damages"] = schaeden_alt + schaeden_neu' in q
    # Ohne Aenderung bleibt die alte Fassung stehen
    assert "and not korrigiert and not schaeden_neu:\n        return False" in q
    # Beweissicherung der alten Fassung (bestand schon, darf nicht wegfallen)
    # Pruefbericht 20.09.2026 (V-27): per Upsert statt insert_one
    assert "generated_pdf_versions.update_one" in q and "$setOnInsert" in q
    assert '"version": alte_version + 1' in q


def test_07_merker_fuer_die_rueckfrage_nur_nach_der_abholung():
    import routes.contracts as C
    q = inspect.getsource(C.regenerate_contract_for_pickup)
    assert '"nach_abholung_versand_offen": True' in q
    assert 'grund == "abholung_abgeschlossen"' in q, \
        "eine blosse Terminverschiebung fragt nicht nach dem Versand"
    # Rollenprüfung 22.09.2026 (Review): nach einer Korrektur-Version nennt
    # "felder" die Abweichungen gegen den Vertrag VOR der Abholung
    # (Verhalten: test_rp_vertrag_welle4_20260922).
    assert "felder_geaendert = sorted(korrigiert.keys())" in q
    assert '"felder": felder_geaendert' in q
    # Nach dem Versand ist die Frage beantwortet. Nachpruefung 20.09.2026
    # (N1): das steht jetzt in _abschluss — und zwar NUR, wenn die
    # versendete Fassung auch die aktuelle ist. Ging waehrend des Versands
    # eine aeltere Fassung raus, bleibt die Frage offen.
    abschluss = inspect.getsource(C._abschluss)
    assert '"nach_abholung_versand_offen": ""' in abschluss
    assert "fassung_veraltet" in abschluss, (
        "der Merker wird auch dann geloescht, wenn der Verkaeufer die ALTE "
        "Fassung bekommen hat")


def test_08_abschluss_reicht_korrekturen_weiter():
    import routes.protocols as P
    q = inspect.getsource(P.finalize_protocol)
    assert "protokoll_korrekturen(appt, filled)" in q
    assert "korrekturen=korrekturen, neue_schaeden=neue_schaeden" in q
    helfer = inspect.getsource(P.protokoll_korrekturen)
    assert "PV.vertrags_korrekturen" in helfer and "new_damages" in helfer
    assert "except Exception" in helfer, "der Abschluss darf daran nie scheitern"


def test_09_app_fragt_nach_dem_versand():
    seite = (FRONT / "pages" / "app" / "PDFArchiv.jsx").read_text(encoding="utf-8")
    assert "NachAbholungHinweis" in seite and "nach_abholung_versand_offen" in seite
    assert "Neuen Vertrag senden" in seite and "<SendDialog" in seite
    assert "Die vorherige Fassung bleibt als Nachweis erhalten" in seite
    assert "nach-abholung-senden-" in seite


def test_10_ablauf_bleibt_wie_gebaut():
    """Fahrer gibt frei -> Chef sieht es -> Preis -> Chef bestaetigt -> erst
    dann unterschreiben. Diese Kette darf der Umbau nicht angefasst haben."""
    import routes.protocols as P
    q = inspect.getsource(P.finalize_protocol)
    assert "FREIGABE_FEHLT" in q or "Freigabe" in q
    freigabe = inspect.getsource(P.protokoll_freigeben)
    assert "_chef_dep" in inspect.getsource(P).split("protokoll_freigeben")[0]
    assert "zurueck" in freigabe and "neuer_preis" in freigabe
