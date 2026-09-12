# -*- coding: utf-8 -*-
"""Runde 29 (12.09.2026, Pruefbefunde) — Betriebspunkte:

  * Fehlt ein kritischer Unique-Index oder laeuft der Link-Arbeiter nicht,
    wurde das nur ins Protokoll geschrieben. Die Instanz bediente trotzdem
    Besucher. Jetzt meldet /ready einen FEHLER (503), der Load Balancer
    nimmt sie damit gar nicht erst in die Rotation.
  * Die Terminloeschung loeschte ZUERST den Termin und bereinigte danach
    Kaufvorgang und Vertragszeiger. Brach sie dazwischen ab, zeigten beide
    fuer immer auf einen Termin, den es nicht mehr gab.

Quellpruefung: beide Wege sind nur im laufenden Betrieb bzw. ueber HTTP
nachstellbar; die Reihenfolge und die Verdrahtung lassen sich aber
zuverlaessig am Quelltext festhalten (gleiche Bauart wie test_rollout.py).
"""
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]


def _quelle(*teile) -> str:
    return (WURZEL.joinpath(*teile)).read_text(encoding="utf-8")


def test_01_ready_macht_aus_fehlenden_kernstuecken_einen_fehler():
    s = _quelle("backend", "server.py")
    assert "BETRIEBSBEREIT: dict = {}" in s, "Zustandsspeicher fehlt"
    # Die prozesslokalen Teile melden ihren Zustand vom Start ...
    for schluessel in ("index_vehicles", "index_kaufvorgaenge",
                       "link_worker", "anbieter_grenze"):
        assert f'BETRIEBSBEREIT["{schluessel}"]' in s, schluessel
    block = s[s.index("kritische_indizes = {"):s.index("bereit = not fehler")]
    # ... und /ready macht daraus einen Fehler, keine blosse Warnung.
    assert "fehler.append(" in block, block
    assert 'BETRIEBSBEREIT.get(schluessel) is False' in block
    # Der Start bricht bewusst NICHT ab (sonst waere der Server unbedienbar).
    assert "abbruch_in_produktion=False" in s


def test_01b_kritische_indizes_werden_live_geprueft():
    """Gegenpruefung 12.09.2026: Ein Merker vom Start haette bedeutet, dass
    /ready nach dem Bereinigen der Dubletten weiter 503 meldet — und weil
    rollout.sh und freigeben.sh genau diese Route abfragen, waere der Server
    nicht mehr aus dem Drain zu holen gewesen."""
    s = _quelle("backend", "server.py")
    block = s[s.index("kritische_indizes = {"):s.index("bereit = not fehler")]
    assert "index_information()" in block, "Indizes muessen live geprueft werden"
    assert 'i.get("unique")' in block
    assert "dubletten_pruefen.py" in block, "die Meldung muss den Weg nennen"
    assert "kein Neustart noetig" in block


def test_02_ready_liefert_503_wenn_ein_fehler_vorliegt():
    s = _quelle("backend", "server.py")
    stelle = s.index("bereit = not fehler")
    schwanz = s[stelle:stelle + 400]
    assert "response.status_code = 503" in schwanz
    assert '"ready": bereit' in schwanz


def test_03_terminloeschung_loest_erst_die_verweise():
    s = _quelle("backend", "routes", "appointments.py")
    block = s[s.index('@router.delete("/appointments/{appt_id}")'):]
    block = block[:block.index('@router.get("/appointments/{appt_id}/pickup-order.pdf")')]
    loesen = block.index("_kv.termin_loesen(appt_id)")
    zeiger = block.index('"appointment_id": None')
    loeschen = block.index("db.appointments.delete_one(")
    assert loesen < loeschen, "Kaufvorgang zuerst loesen, dann den Termin loeschen"
    assert zeiger < loeschen, "Vertragszeiger zuerst loesen, dann den Termin loeschen"
    # Der Audit-Eintrag bleibt am Ende (er braucht das Ergebnis).
    assert loeschen < block.index('"termin.geloescht"')


def test_04_vertragsdetails_liefern_die_pdfs_nicht_mit():
    """Pruefbefund: GET /contracts/{id} lieferte das komplette Dokument samt
    beider PDFs als Base64 — mehrere hundert Kilobyte je Aufruf."""
    s = _quelle("backend", "routes", "contracts.py")
    block = s[s.index('@router.get("/contracts/{contract_id}")'):]
    block = block[:block.index('@router.get("/contracts/{contract_id}/pdf")')]
    assert '"pdf_b64": 0' in block and '"pdf_digital_b64": 0' in block, block
    # Die Dateien selbst gibt es weiterhin ueber die eigene Route.
    assert '/contracts/{contract_id}/pdf' in s
