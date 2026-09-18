# -*- coding: utf-8 -*-
"""Abbrechen beim Vergleich und die Navi-Regel (Wuensche Ahmad 18.09.2026).

  A  "Wenn es zu lange laedt, soll man auf X klicken koennen und direkt einen
     neuen Link eingeben." -> Der Wartende steigt aus dem Job aus; wartet
     niemand mehr und hat noch kein Worker angefangen, faellt der Abruf ganz
     weg (kein Apify-Lauf, kein Tageskontingent). Ein laufender Abruf laeuft
     zu Ende — sein Ergebnis liegt dann im Zwischenspeicher.
  B  Bricht der Nutzer mitten im Abruf ab, gibt der Server die Inserats-Sperre
     frei — sonst war derselbe Link bis zu 90 Sekunden blockiert.
  C  In den Vergleichsregeln waehlbar: "Mitfiltern, wenn im Inserat vorhanden"
     (Standard) oder "Nicht filtern".
"""
import copy
import inspect
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_beweis_service import welt  # noqa: E402,F401

import link_jobs as LJ  # noqa: E402
import listing_identity as LI  # noqa: E402
import mobile_service as M  # noqa: E402
from regeln import regeln_validieren  # noqa: E402


def _job(w, status="queued", user_ids=None, dealer_ids=None):
    doc = {"id": f"job_{w.s}_{uuid.uuid4().hex[:6]}",
           "cache_key": f"mobile:abbr{w.s}{uuid.uuid4().hex[:4]}",
           "status": status, "active": status in LJ.OFFEN,
           "user_ids": list(user_ids if user_ids is not None else [f"u_{w.s}"]),
           "dealer_ids": list(dealer_ids if dealer_ids is not None else [w.dealer_id]),
           "created_at": datetime.now(timezone.utc),
           "updated_at": datetime.now(timezone.utc)}
    w.run(w.db.link_jobs.insert_one(dict(doc)))
    return doc


def _aufraeumen(w):
    w.run(w.db.link_jobs.delete_many({"id": {"$regex": f"^job_{w.s}_"}}))


# ------------------------------------------------------------------ A
def test_01_letzter_wartender_steigt_aus_job_faellt_weg(welt):
    job = _job(welt)
    erg = welt.run(LJ.warten_beenden(welt.db, job["id"], welt.dealer_id, f"u_{welt.s}"))
    assert erg["status"] == "abgebrochen"
    assert welt.run(welt.db.link_jobs.count_documents({"id": job["id"]})) == 0, \
        "Der Abruf haette gar nicht erst laufen duerfen"
    _aufraeumen(welt)


def test_02_kollege_wartet_weiter_job_bleibt(welt):
    """EIN Job gehoert allen, die auf dasselbe Inserat warten — einer steigt
    aus, der andere bekommt sein Ergebnis trotzdem."""
    job = _job(welt, user_ids=[f"u_{welt.s}", f"u2_{welt.s}"])
    erg = welt.run(LJ.warten_beenden(welt.db, job["id"], welt.dealer_id, f"u_{welt.s}"))
    assert erg["status"] == "laeuft_weiter"
    stand = welt.run(welt.db.link_jobs.find_one({"id": job["id"]}, {"_id": 0}))
    assert stand and stand["user_ids"] == [f"u2_{welt.s}"]
    _aufraeumen(welt)


def test_03_laufender_abruf_wird_nicht_abgewuergt(welt):
    """Laeuft der Abruf schon, bleibt er: das Ergebnis landet im
    Zwischenspeicher und hilft dem naechsten sofort."""
    job = _job(welt, status="processing")
    erg = welt.run(LJ.warten_beenden(welt.db, job["id"], welt.dealer_id, f"u_{welt.s}"))
    assert erg["status"] == "laeuft_weiter" and erg["job_status"] == "processing"
    assert welt.run(welt.db.link_jobs.count_documents({"id": job["id"]})) == 1
    _aufraeumen(welt)


def test_04_unbekannter_job_ist_kein_fehler(welt):
    assert welt.run(LJ.warten_beenden(welt.db, "gibtesnicht", welt.dealer_id,
                                      f"u_{welt.s}"))["status"] == "weg"


def test_05_route_nur_fuer_den_eigenen_auftrag(welt):
    import routes.listings as L
    job = _job(welt)
    fremd = {"id": f"u_fremd_{welt.s}", "dealer_id": welt.anderer, "role": "dealer"}
    with pytest.raises(HTTPException) as e:
        welt.run(L.linkpruefung_abbrechen(job["id"], fremd))
    assert e.value.status_code == 404
    eigen = {"id": f"u_{welt.s}", "dealer_id": welt.dealer_id, "role": "sucher"}
    assert welt.run(L.linkpruefung_abbrechen(job["id"], eigen))["status"] == "abgebrochen"
    _aufraeumen(welt)


# ------------------------------------------------------------------ B
def test_06_abbruch_mitten_im_abruf_gibt_die_sperre_frei():
    """CancelledError ist KEINE Exception (BaseException) — ohne die
    Erweiterung blieb die Lease beim Abbruch stehen und derselbe Link war
    bis zu 90 s gesperrt."""
    q = inspect.getsource(LI.get_or_fetch_listing)
    assert "except BaseException:" in q
    assert "_aio.shield(_lease_freigeben(db, cache_key, claim))" in q
    assert "_aio.shield(release_slot(db, slot_id))" in q


# ------------------------------------------------------------------ C
def _fe(vehicle, regeln):
    q = dict(parse_qsl(urlparse(M.build_search_url(vehicle, regeln)).query))
    return q.get("fe")


def test_07_navi_regel_steuert_den_filter():
    fz = {"make_label": "VW", "model_label": "Golf", "first_registration": "06/2019",
          "mileage": 100000, "fuel": "PETROL", "gearbox": "AUTOMATIC_GEAR",
          "features": ["Klimaanlage", "Navigationssystem"]}
    assert _fe(fz, M.DEFAULT_RULES) == "NAVIGATION_SYSTEM", "Standard: mitfiltern"
    aus = copy.deepcopy(M.DEFAULT_RULES)
    aus["navi"] = {"mode": "ignore"}
    assert _fe(fz, aus) is None, "'Nicht filtern' muss den Filter weglassen"
    # Gespeicherte Altpakete kennen den Schluessel nicht -> Standardverhalten.
    alt = {k: v for k, v in M.DEFAULT_RULES.items() if k != "navi"}
    assert _fe(fz, alt) == "NAVIGATION_SYSTEM"


def test_08_navi_regel_ueberlebt_das_speichern():
    for paket in (M.DEFAULT_RULES, M.DEFAULT_EXPORT_RULES):
        assert paket["navi"] == {"mode": "wenn_vorhanden"}
        assert regeln_validieren(copy.deepcopy(paket)) == paket
    assert regeln_validieren({"navi": {"mode": "ignore"}}) == {"navi": {"mode": "ignore"}}
    with pytest.raises(Exception):
        regeln_validieren({"navi": {"mode": "quatsch"}})


def test_09_einstellungen_zeigen_die_wahl():
    seite = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "app"
             / "Einstellungen.jsx").read_text(encoding="utf-8")
    assert 'testid="rule-navi-mode"' in seite
    assert "Mitfiltern, wenn im Inserat vorhanden" in seite and "Nicht filtern" in seite


def test_10_vergleichsseite_hat_den_abbrechen_knopf():
    src = Path(__file__).resolve().parents[2] / "frontend" / "src"
    seite = (src / "pages" / "app" / "Vergleich.jsx").read_text(encoding="utf-8")
    assert 'data-testid="vergleich-abbrechen-btn"' in seite
    assert "/abbrechen" in seite and "AbortController" in seite
    pruefung = (src / "lib" / "linkCheck.js").read_text(encoding="utf-8")
    assert "export function istAbbruch" in pruefung and "opts.onJob?.(jobId)" in pruefung
