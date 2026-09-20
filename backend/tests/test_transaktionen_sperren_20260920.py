# -*- coding: utf-8 -*-
"""Nachpruefung 20.09.2026, Nr. 30-38 und 43/44 — Transaktionen, Sperren,
Lebenszyklus.

  Nr. 30-33  `deps.transaktion()` fing JEDEN PyMongoError ab und lief dann
             einfach noch einmal OHNE Transaktion. Stand der Ausgang der
             Uebergabe aber nur nicht FEST, konnte sie geklappt haben — der
             zweite Durchlauf sah dann eine schon geaenderte Datenbank und
             meldete dem Nutzer 409 oder 404, obwohl seine Aenderung
             gespeichert war. Und ausgerechnet bei einer Stoerung lief der
             mehrschrittige Ablauf ohne Alles-oder-nichts.
  Nr. 34/35  Ging eine Job- oder Migrationssperre verloren, stand das nur im
             Protokoll — der Lauf machte weiter, waehrend der neue Besitzer
             schon anfing.
  Nr. 36     Beweisdokument-Unique-Index angeblich nicht fail-closed.
  Nr. 37/38  "erledigt" ergab je nach Weg (Buero / Fahrer-App) einen anderen
             Fahrzeugzustand.
  Nr. 43/44  Alte Stripe-Indizes konnten ein Deployment stoppen, obwohl es
             keinen Stripe-Weg mehr gibt.
"""
import asyncio
import inspect
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


# ------------------------------------------------------------- Nr. 30-33
def _fehler_mit(label):
    from pymongo.errors import PyMongoError
    exc = PyMongoError("Testfehler")
    exc._error_labels = {label}
    return exc


@pytest.fixture
def transaktions_welt(monkeypatch):
    import deps

    class _Sitzung:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def start_transaction(self):
            welt["transaktionen"] += 1
            return _Sitzung()

    class _Client:
        async def start_session(self):
            return _Sitzung()

    welt = {"transaktionen": 0, "laeufe": []}

    async def _ja():
        return True

    monkeypatch.setattr(deps, "ist_replica_set", _ja)
    monkeypatch.setattr(deps, "client", _Client())
    return welt


def test_30_unklarer_ausgang_wird_nicht_wiederholt(transaktions_welt):
    """Der Kern von Nr. 30/31/32: lieber ehrlich 'unklar' als still doppelt."""
    from fastapi import HTTPException

    import deps
    welt = transaktions_welt

    async def fn(session):
        welt["laeufe"].append(session)
        raise _fehler_mit("UnknownTransactionCommitResult")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps.transaktion(fn))
    assert exc.value.status_code == 503
    assert "nicht sicher abgeschlossen" in exc.value.detail
    assert len(welt["laeufe"]) == 1, "genau EIN Versuch — kein zweiter Lauf"
    assert welt["laeufe"] == [welt["laeufe"][0]], "und der lief MIT Sitzung"


def test_31_32_der_alte_weg_haette_409_bzw_404_erzeugt():
    """Warum das wichtig ist, in einem Satz im Quelltext."""
    import deps
    q = inspect.getsource(deps.transaktion)
    assert "UnknownTransactionCommitResult" in q
    assert "409" in q and "404" in q, \
        "die konkreten Folgen (Termin aendern/loeschen) gehoeren in die Begruendung"


def test_33_voruebergehender_fehler_wird_mit_transaktion_wiederholt(transaktions_welt):
    """TransientTransactionError: nachweislich NICHTS uebernommen."""
    import deps
    welt = transaktions_welt

    async def fn(session):
        welt["laeufe"].append(session)
        if len(welt["laeufe"]) == 1:
            raise _fehler_mit("TransientTransactionError")
        return "fertig"

    assert asyncio.run(deps.transaktion(fn)) == "fertig"
    assert len(welt["laeufe"]) == 2
    assert all(s is not None for s in welt["laeufe"]), \
        "beide Versuche MIT Transaktion — nicht der Rueckfall ohne (Nr. 33)"


def test_33b_unbekannter_fehler_faellt_weiter_zurueck(transaktions_welt):
    """Transaktionen gar nicht moeglich: wie bisher einmal ohne."""
    import deps
    welt = transaktions_welt

    async def fn(session):
        welt["laeufe"].append(session)
        if session is not None:
            raise _fehler_mit("KeinBekanntesLabel")
        return "ohne transaktion"

    assert asyncio.run(deps.transaktion(fn)) == "ohne transaktion"
    assert welt["laeufe"] == [welt["laeufe"][0], None]


# --------------------------------------------------------------- Nr. 34
def test_34_verlorene_job_sperre_stoppt_den_lauf():
    import job_lock
    w = job_lock.Wache("cleanup-cycle")
    w.pruefen()
    w.verloren = True
    with pytest.raises(job_lock.SperreVerloren) as exc:
        w.pruefen()
    assert "cleanup-cycle" in str(exc.value)

    q = inspect.getsource(job_lock.heartbeat)
    assert "wache.verloren = True" in q and "yield wache" in q
    assert "continue" in q.split("except Exception")[1][:300], \
        "eine Stoerung ist kein Beweis fuer den Verlust"


def test_34b_der_aufraeumlauf_fragt_zwischen_den_schritten():
    import cleanup_service
    q = inspect.getsource(cleanup_service.run_cleanup_forever)
    assert "wache.pruefen()" in q and "SperreVerloren" in q, (
        "frueher lief der Lauf nach dem Sperrverlust weiter — zwei Prozesse "
        "loeschten dieselben Dateien (Nr. 34)")
    import routes.admin as ADMIN
    assert "SperreVerloren" in inspect.getsource(ADMIN.admin_trigger_cleanup)


# --------------------------------------------------------------- Nr. 35
def test_35_migration_bricht_bei_sperrverlust_ab():
    import migrationen
    w = migrationen._Wache()
    w.pruefen()
    w.verloren = True
    with pytest.raises(migrationen.SperreVerloren):
        w.pruefen()

    q = inspect.getsource(migrationen.ausfuehren)
    assert "wache.pruefen()" in q, \
        "vor JEDER einzelnen Migration nachsehen (Nr. 35)"
    stelle = q.split("for nr, name, fn in MIGRATIONEN:")[1]
    assert stelle.index("wache.pruefen()") < stelle.index("await fn(db)")


def test_35b_verlierer_wird_zum_wartenden_statt_abzubrechen():
    """Wichtig: der Prozess darf NICHT mit halb migrierten Daten weiterlaufen
    und in Produktion auch nicht einfach abbrechen — er wartet wie jeder
    andere Nicht-Leader auf die Zielversion."""
    import migrationen
    q = inspect.getsource(migrationen.ausfuehren_oder_warten)
    assert "except SperreVerloren" in q
    teil = q.split("except SperreVerloren")[1][:900]
    assert "verloren = True" in teil, "kein return — der Prozess faellt durch"
    assert "return" not in teil.split("verloren = True")[0], \
        "vor dem Durchfallen darf nichts zurueckgegeben werden"
    assert "if verloren:" in q, "und landet in der Warteschleife"


# --------------------------------------------------------------- Nr. 36
def test_36_beweis_index_ist_in_produktion_bereits_fail_closed():
    """GEGENGEPRUEFT — dieser Befund trifft nicht mehr zu.

    Der Bericht zitiert die Log-Zeile "doppelte Dokumente moeglich" und
    schliesst daraus, der Worker laufe ungeschuetzt weiter. Die Zeile gilt
    aber nur ausserhalb von Produktion: seit Befund 123 (16.09.2026) wird in
    Produktion OHNE den Unique-Index gar kein Beweisdokument mehr
    vorgemerkt (fail-closed), und der Betriebsalarm `unique_index_fehlt`
    steht. Dieser Test haelt genau das fest, damit es so bleibt."""
    import beweis_service
    q = inspect.getsource(beweis_service)
    stelle = q.split("_index_sicher and _produktion()")[1][:700]
    assert "return None" in stelle, "in Produktion keine Vormerkung ohne Index"
    assert "fail-closed" in stelle
    sichern = inspect.getsource(beweis_service.beweis_indizes_sichern)
    assert "betrieb.alarm(" in sichern and "unique_index_fehlt" in sichern


# ------------------------------------------------------------ Nr. 37/38
@pytest.mark.parametrize("status,erwartet", [
    ("abgeholt", "abgeholt"),
    ("erledigt", "abgeholt"),          # frueher: Buero nichts, Fahrer "nicht_abgeholt"
    ("nicht abgeholt", "nicht_abgeholt"),
    ("nicht_abgeholt", "nicht_abgeholt"),
    ("storniert", None),               # bewusst ohne Zustand
    ("offen", None),
    ("bestätigt", None),
    ("", None),
    (None, None),
])
def test_37_ein_status_ein_fahrzeugzustand(status, erwartet):
    from lifecycle import zustand_fuer_terminstatus
    assert zustand_fuer_terminstatus(status) == erwartet


def test_38_buero_und_fahrer_app_nutzen_dieselbe_tabelle():
    import routes.appointments as A
    import routes.drivers as D
    fahrer = inspect.getsource(D)
    assert '"abgeholt" if body.status == "abgeholt" else "nicht_abgeholt"' not in fahrer, (
        "genau diese Zeile machte aus 'erledigt' und 'storniert' ein "
        "'nicht_abgeholt' (Nr. 38)")
    assert "zustand_fuer_terminstatus(body.status)" in fahrer
    buero = inspect.getsource(A)
    assert 'zustand_fuer_terminstatus(update["status"])' in buero


def test_38b_erledigt_hat_auch_eine_loeschfrist():
    """Gegenprobe: die Frist war schon da, nur das Mapping fehlte."""
    import cleanup_service
    fristen = dict(cleanup_service.CLEANUP_RULES)
    assert fristen["erledigt"] == fristen["abgeholt"] == 7
    assert "storniert" in fristen


# ------------------------------------------------------------ Nr. 43/44
def test_43_alte_stripe_indizes_stoppen_kein_deployment():
    import server
    q = inspect.getsource(server)
    for feld in ("subscriptions.session_id", "zugang_grants.session_id"):
        stelle = q.split(f'_in_produktion_abbrechen(f"{feld}')
        assert len(stelle) == 1, (
            f"{feld} bricht Produktion weiterhin ab — der Index schuetzt aber "
            f"nur noch Altdaten aus der entfernten Stripe-Zahlung (Nr. 43/44)")
    # Sichtbar bleibt es trotzdem:
    assert q.count('alarm(db, "unique_index_fehlt", ref="subscriptions.session_id"') == 1
    assert q.count('alarm(db, "unique_index_fehlt", ref="zugang_grants.session_id"') == 1


def test_44_kein_heutiger_weg_schreibt_diese_felder():
    """Gegenprobe zum Befund: stimmt es, dass Stripe wirklich weg ist?"""
    import re
    treffer = []
    for datei in sorted(BACKEND.glob("**/*.py")):
        if "tests" in datei.parts or "scripts" in datei.parts:
            continue
        text = datei.read_text(encoding="utf-8", errors="replace")
        for zeile in text.splitlines():
            # Schreibende Verwendung von subscriptions/zugang_grants mit session_id
            if re.search(r'"session_id"\s*:', zeile) and "current_session_id" not in zeile:
                treffer.append(f"{datei.name}: {zeile.strip()[:90]}")
    assert not treffer, ("Es schreibt doch noch jemand session_id — dann war "
                         f"die Abstufung voreilig: {treffer}")
