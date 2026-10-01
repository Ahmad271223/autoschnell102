# -*- coding: utf-8 -*-
"""Pruefliste 01.10.2026 (externe Pruefung des Standes 2483e78, 11 Punkte — die 9 bestaetigten):

  Nr. 1  unterschrift/ und portal/ waren ueber /api/files OHNE Anmeldung abrufbar (public-Cache) — jetzt 404
         wie protocol/ und pickup/, und portal.unterschrift_key steht in keiner Vertragsantwort.
  Nr. 2  Unterschreiben nur, wenn DIESE Sitzung genau dieses Dokument (Fassung + Pruefsumme) geladen hat.
  Nr. 3  Die Domain-Pruefung spricht keine internen Adressen an (127.0.0.1, 10.x, 192.168.x, Link-Local, ...).
  Nr. 4  Das Unterschriftsbild des Chefs bekommt nur der Chef (current_chef), Sucher nur "hinterlegt ja/nein".
  Markt Nr. 1  Kostenabgleich rechnet je Lauf mit ALLEN Jobs (kein Doppel-/Zuvielbuchen an der 200er-Grenze).
  Markt Nr. 2/3  Testlauf nicht bestanden bei unzuordenbaren Zeilen oder Segmenten ohne Fahrzeugdaten.
  Markt Nr. 4  "zuviel" zaehlt im Testlauf als gelaufener, bezahlter Actor (wie im Worker).
  Markt Nr. 5  Ein durch Neustart abgebrochener Sammel-Testlauf wird beim Start wieder aufgenommen.
"""
from datetime import timedelta
from pathlib import Path

import pytest

import routes.contracts as C
import routes.kundenportal as KP

from test_kundenportal_20260929 import _fehler, _lauf, _request, _vertrag, welt  # noqa: F401
from test_portal_sitzung_20260930 import _lesen, _oeffnen, _seite_an, _unterschrift

BACKEND = Path(__file__).resolve().parent.parent


def test_01_unterschriften_nicht_ueber_api_files():
    import dateien
    import server
    assert {"unterschrift/", "portal/", "protocol/", "pickup/"} <= set(dateien.PRIVATE_PREFIXE)
    assert server._PRIVATE_FILE_PREFIXES == dateien.PRIVATE_PREFIXE, "zwei Listen driften — siehe Nr. 1"
    import asyncio
    for key in ("unterschrift/d-1/x.png", "portal/d-1/unterschrift-verkaeufer.png", "protocol/p.pdf", "pickup/f.jpg"):
        antwort = asyncio.run(server.serve_file(key, _request(), None, None))
        assert antwort.status_code == 404, key
        assert "public" not in (antwort.headers.get("cache-control") or "")


def test_02_chef_unterschrift_nur_fuer_den_chef():
    route = next(r for r in KP.router.routes if getattr(r, "path", "") == "/dealer/unterschrift" and "GET" in r.methods)
    namen = {d.call.__name__ for d in route.dependant.dependencies}
    assert "current_chef" in namen and "current_firma" not in namen, namen
    # Hochladen/Entfernen waren schon Chefsache (ist_haupt_chef im Code) — die Vorschau jetzt auch


def test_03_kein_unterschrift_schluessel_in_vertragsantworten(welt):  # noqa: F811
    w = welt
    _seite_an(w)
    c = _vertrag(w, w.sucher)
    s = _oeffnen(_lauf(KP.portal_freigeben(c["id"], user=w.sucher))["code"])
    _lesen(s)
    assert _lauf(KP.portal_unterschreiben(s, _unterschrift(), _request()))["ok"]
    p = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "portal": 1}))["portal"]
    assert p["unterschrift_key"].startswith(f"portal/{w.dealer_id}/") and p["gelesen"]["fassung"] == 1
    for nutzer in (w.sucher, w.chef):
        erg = _lauf(C.get_contract(c["id"], user=nutzer))
        assert erg["portal"]["status"] == "unterschrieben" and erg["portal"]["code"]
        assert not ({"unterschrift_key", "anspruch", "anspruch_bis", "gelesen"} & set(erg["portal"])), erg["portal"]
    assert "unterschrift_key" not in str(_lauf(KP.portal_stand(c["id"], user=w.sucher)))
    # die Hilfsfunktion raeumt auch archivierte Fassungen
    doc = {"portal": {"unterschrift_key": "portal/x", "status": "unterschrieben"},
           "versions": [{"version": 1, "portal": {"unterschrift_key": "portal/y"}}, "kaputt"]}
    C._portal_bereinigen(doc)
    assert doc["portal"] == {"status": "unterschrieben"} and doc["versions"][0]["portal"] == {}


def test_04_unterschrift_nur_nach_gelesenem_dokument(welt):  # noqa: F811
    import base64
    import portal_pdf
    w = welt
    _seite_an(w)
    c = _vertrag(w, w.sucher)
    s = _oeffnen(_lauf(KP.portal_freigeben(c["id"], user=w.sucher))["code"])
    f = _fehler(KP.portal_unterschreiben(s, _unterschrift(), _request()))
    assert f.status_code == 409 and "zuerst den Vertrag laden" in f.detail
    assert w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "portal.status": 1}))["portal"]["status"] == "offen"
    _lesen(s)
    g = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "portal.gelesen": 1, "pdf_b64": 1}))
    assert g["portal"]["gelesen"]["sha256"] == portal_pdf.pruefsumme(base64.b64decode(g["pdf_b64"]))
    # neuer Code (Sitzung s stirbt): die neue Sitzung muss selbst lesen — der alte Vermerk gilt nicht
    _lauf(KP.portal_zurueckziehen(c["id"], user=w.sucher))
    s2 = _oeffnen(_lauf(KP.portal_freigeben(c["id"], user=w.sucher))["code"])
    assert _fehler(KP.portal_unterschreiben(s2, _unterschrift(), _request())).status_code == 409
    _lesen(s2)
    # Vermerk passt nicht zum Dokument (andere Pruefsumme): keine Unterschrift
    w.run(w.db.generated_pdfs.update_one({"id": c["id"]}, {"$set": {"portal.gelesen.sha256": "0" * 64}}))
    assert _fehler(KP.portal_unterschreiben(s2, _unterschrift(), _request())).status_code == 409
    _lesen(s2)
    erg = _lauf(KP.portal_unterschreiben(s2, _unterschrift(), _request()))
    assert erg["ok"]
    doc = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "portal": 1}))
    assert doc["portal"]["gelesen_sha256"] == doc["portal"]["gelesen"]["sha256"]
    # nach der Unterschrift liefert die PDF-Route die unterschriebene Fassung und vermerkt nichts Neues
    assert _lauf(KP.portal_sitzung_pdf(s2)).body[:4] == b"%PDF"
    assert w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "portal.gelesen": 1}))["portal"]["gelesen"] == doc["portal"]["gelesen"]


def test_05_domainpruefung_spricht_keine_internen_adressen_an(welt, monkeypatch):  # noqa: F811
    assert KP.adresse_oeffentlich("104.21.6.253") and KP.adresse_oeffentlich("2606:4700:3030::ac43:d2fd")
    for ip in ("127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "0.0.0.0", "::1", "fe80::1",
               "::ffff:192.168.1.1", "::ffff:127.0.0.1", "100.64.0.1", "224.0.0.1", "quatsch", ""):
        assert not KP.adresse_oeffentlich(ip), ip
    aufrufe = []

    async def https(host):
        aufrufe.append(host)
        return {"status": 200, "cloudflare": True, "slug": "kfz-mueller"}
    monkeypatch.setattr(KP, "_https_firma", https)

    def dns_mit(*ips):
        async def dns(host):
            return list(ips)
        return dns
    monkeypatch.setattr(KP, "_dns", dns_mit("10.0.0.2"))
    e = _lauf(KP.domain_pruefen("intern.example", "kfz-mueller"))
    assert e["ok"] is False and e["schritte"][0]["ok"] is False and "interne Adresse" in e["schritte"][0]["text"]
    assert [s["schritt"] for s in e["schritte"]] == ["dns", "proxy"] and aufrufe == []
    assert "öffentliche Adresse" in e["naechster_schritt"]
    # gemischt (eine oeffentliche, eine interne): auch nicht
    monkeypatch.setattr(KP, "_dns", dns_mit("104.21.6.253", "127.0.0.1"))
    e = _lauf(KP.domain_pruefen("intern.example", "kfz-mueller"))
    assert e["schritte"][0]["ok"] is False and aufrufe == []
    # oeffentlich: wie bisher
    monkeypatch.setattr(KP, "_dns", dns_mit("104.21.6.253"))
    e = _lauf(KP.domain_pruefen("kfz-mueller.de", "kfz-mueller"))
    assert aufrufe == ["kfz-mueller.de"] and e["schritte"][0]["ok"] is True
    # direkte Adressen und localhost bleiben schon vorher draussen
    for d in ("127.0.0.1", "localhost", "intern.localhost"):
        assert _fehler(KP.domain_pruefen(d, "kfz-mueller")).status_code == 400


# ---------------------------------------------------------------- Markt
from test_markt_20260926 import _item, _vorbereiten, welt as markt_welt  # noqa: E402,F401,F811
from test_markt_20260926 import APIFY, BUD, JOBS, K, NORM, _module  # noqa: E402

ENTWURF = {"make": "BMW", "model": "320", "variant": "320d", "fuel": "DIESEL", "ez_years": [2019],
           "km_buckets": [{"min_km": 10000, "max_km": 30000}], "rows": 20}


def test_06_kostenabgleich_rechnet_je_lauf_mit_allen_jobs(markt_welt, monkeypatch):
    """Markt Nr. 1: zwei Jobs des Laufs sind schon abgeglichen (0,02 + 0,02), einer noch nicht (0,01);
    Apify meldet 0,06. Richtig: Differenz 0,01 auf alle drei — Summe 0,06. Vorher: 0,06 - 0,01 = 0,05
    nur auf den dritten (Summe 0,10) und beim naechsten Lauf noch einmal."""
    welt = markt_welt
    s, seg = _vorbereiten(welt, monkeypatch)
    db = welt.db
    heute = K.heute_tag()
    vorhin = (K.jetzt() - timedelta(minutes=20)).isoformat()
    run = f"r-voll-{s}"
    jobs = []
    for i, (kosten, abgeglichen) in enumerate(((0.02, True), (0.02, True), (0.01, False))):
        j = {**JOBS._job_doc(seg, f"{heute}#v{i}{s}", vorhin, "manual"), "status": "completed", "actual_cost": kosten,
             "actor_run_id": run, "finished_at": vorhin}
        if abgeglichen:
            j.update({"kosten_abgeglichen": True, "kosten_abgleich_diff": 0.01})
        jobs.append(j)
    welt.run(db[K.JOBS].insert_many([dict(j) for j in jobs]))
    welt.run(BUD.dokument(db, f"test-{s}"))

    async def _holen(rid):
        if rid != run:
            raise KeyError(rid)
        return {"usageTotalUsd": 0.06}
    erg = welt.run(BUD.kosten_abgleich(db, lauf_dokument=_holen))
    assert erg["gebucht"] >= 3 and round(erg["differenz_usd"], 4) == 0.01
    docs = [welt.run(db[K.JOBS].find_one({"id": j["id"]}, {"_id": 0})) for j in jobs]
    assert all(d["kosten_abgeglichen"] for d in docs)
    assert round(sum(d["actual_cost"] for d in docs), 4) == 0.06, [d["actual_cost"] for d in docs]
    assert round(docs[2]["actual_cost"], 4) == 0.012 and round(docs[0]["actual_cost"], 4) == 0.024
    assert round(welt.run(BUD.dokument(db, f"test-{s}"))["used_usd"], 4) == 0.01
    # zweiter Lauf: nichts mehr offen, nichts doppelt
    erg2 = welt.run(BUD.kosten_abgleich(db, lauf_dokument=_holen))
    assert erg2["gebucht"] == 0 and round(welt.run(BUD.dokument(db, f"test-{s}"))["used_usd"], 4) == 0.01
    assert round(sum(welt.run(db[K.JOBS].find_one({"id": j["id"]}, {"_id": 0}))["actual_cost"] for j in jobs), 4) == 0.06


def test_07_testlauf_faellt_bei_unzuordenbaren_zeilen_und_ohne_fahrzeugdaten_durch(markt_welt, monkeypatch):
    A = _module("markt.auftraege")
    ML = _module("markt.masterliste")
    basis = {"gueltig_gesamt": 2, "verworfen_gesamt": 0, "sortierung_ungueltig": 0, "segmente_geprueft": 2, "segmente_gesamt": 2}
    assert A.testlauf_bestanden(basis) is True
    assert A.testlauf_bestanden({**basis, "nicht_zuordenbar": 1}) is False, "Markt Nr. 2"
    assert A.testlauf_bestanden({**basis, "segmente_ungueltig": 1}) is False, "Markt Nr. 3"
    assert "zuzuordnen" in ML._kurz({**basis, "bestanden": False, "nicht_zuordenbar": 3})["grund"]
    assert "ohne Fahrzeugdaten" in ML._kurz({**basis, "bestanden": False, "segmente_ungueltig": 1})["grund"]
    # echter Testlauf: der Actor liefert Rohzeilen, der Parser macht daraus nichts (wie im Worker: data_invalid)
    monkeypatch.setenv("MARKT_APIFY_ACTOR", "scrapesmith~mobile-de-scraper")

    async def _lauf_(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        return {"items": [_item("q1", 9000, km=20000, ez="03/2019"), _item("q2", 9500, km=21000, ez="03/2019")],
                "usd": 0.009, "run_id": "r-ohne", "status": "SUCCEEDED", "dauer_ms": 3, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf_)
    monkeypatch.setattr(NORM, "listings_aus_items", lambda roh: [])
    erg = markt_welt.run(A.testlauf(ENTWURF, n=2))
    assert erg["geliefert_gesamt"] == 0 and erg["segmente_ungueltig"] == 1 and erg["segmente"][0]["ungueltig"] is True
    assert erg["bestanden"] is False and erg["testlauf_ok_hash"] is None
    assert "ohne Fahrzeugdaten" in ML._kurz(erg)["grund"]


def test_08_testlauf_zuviel_zaehlt_als_gelaufen(markt_welt, monkeypatch):
    A = _module("markt.auftraege")
    monkeypatch.setenv("MARKT_APIFY_ACTOR", "scrapesmith~mobile-de-scraper")
    abgerechnet = []

    async def _res(db, usd):
        return {"id": "res", "usd": usd}

    async def _abr(db, res, usd, rows=0, gelaufen=True, runs=None):
        abgerechnet.append((usd, gelaufen, runs))
    monkeypatch.setattr(BUD, "reservieren", _res)
    monkeypatch.setattr(BUD, "abrechnen", _abr)
    for usd, erwartet in ((0.03, 0.03), (None, None)):
        async def _lauf_(*a, _usd=usd, **k):
            raise APIFY.ApifyFehler("zuviel", "Datensatz groesser als erwartet", usd=_usd, run_id="r-z")
        monkeypatch.setattr(APIFY, "lauf", _lauf_)
        with pytest.raises(APIFY.ApifyFehler):
            markt_welt.run(A.testlauf(ENTWURF, db=markt_welt.db))
        assert abgerechnet[-1] == (erwartet, True, 1), abgerechnet
    # ein Fehler VOR dem Start (z. B. kaputte Eingabe) kostet weiter nichts

    async def _nix(*a, **k):
        raise APIFY.ApifyFehler("eingabe", "Actor-Eingabe abgelehnt", usd=None, run_id=None)
    monkeypatch.setattr(APIFY, "lauf", _nix)
    with pytest.raises(APIFY.ApifyFehler):
        markt_welt.run(A.testlauf(ENTWURF, db=markt_welt.db))
    assert abgerechnet[-1] == (0.0, False, 0)


def test_09_sammel_testlauf_wird_nach_neustart_wieder_aufgenommen(markt_welt, monkeypatch):
    ML = _module("markt.masterliste")
    db = markt_welt.db
    monkeypatch.setattr(K, "token", lambda: "apify-test")
    gelaufen = []

    async def _ausf(db_, lauf_id, *, aktivieren, mit_review, zusatz=None):
        gelaufen.append((lauf_id, aktivieren, mit_review))
        return {}
    monkeypatch.setattr(ML, "testlauf_alle_ausfuehren", _ausf)
    dok = {"_id": ML.TESTLAUF_ALLE_DOK}
    abgelaufen = (K.jetzt() - timedelta(minutes=20)).isoformat()
    gueltig = (K.jetzt() + timedelta(minutes=10)).isoformat()
    try:
        # laeuft "noch" mit gueltiger Lease (anderer Server arbeitet): nichts tun
        markt_welt.run(db[K.KONFIG].replace_one(dok, {**dok, "laeuft": True, "lauf_id": "alt", "lease_until": gueltig,
                                                     "aktivieren": True, "mit_review": False}, upsert=True))
        assert markt_welt.run(ML.testlauf_alle_wiederaufnehmen(db)) is None and gelaufen == []
        # sauber beendet: nichts tun
        markt_welt.run(db[K.KONFIG].update_one(dok, {"$set": {"laeuft": False}}))
        assert markt_welt.run(ML.testlauf_alle_wiederaufnehmen(db)) is None and gelaufen == []
        # abgebrochen (Prozess weg, Lease abgelaufen): wieder aufnehmen — mit denselben Einstellungen
        markt_welt.run(db[K.KONFIG].update_one(dok, {"$set": {"laeuft": True, "lease_until": abgelaufen}}))
        neu = markt_welt.run(ML.testlauf_alle_wiederaufnehmen(db))
        assert neu and gelaufen == [(neu, True, False)]
        d = markt_welt.run(db[K.KONFIG].find_one(dok, {"_id": 0}))
        assert d["lauf_id"] == neu and d["wiederaufgenommen_von"] == "alt" and d["gestartet_von"].startswith("wiederaufnahme:")
        # ohne Token: nichts (Testlaeufe brauchen Apify)
        markt_welt.run(db[K.KONFIG].update_one(dok, {"$set": {"laeuft": True, "lease_until": abgelaufen}}))
        monkeypatch.setattr(K, "token", lambda: "")
        assert markt_welt.run(ML.testlauf_alle_wiederaufnehmen(db)) is None and len(gelaufen) == 1
    finally:
        markt_welt.run(db[K.KONFIG].delete_one(dok))
    quelle = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert "testlauf_alle_wiederaufnehmen" in quelle, "Wiederaufnahme beim Start eingeplant"
