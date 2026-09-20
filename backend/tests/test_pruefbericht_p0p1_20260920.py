# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026 (P0/P1) — drei bestaetigte Befunde.

  P0  Ein zweites oder liegengebliebenes dealer-Konto derselben Firma bekam
      Chef-Rechte. `current_chef` war immer richtig (Zeiger
      dealers.user_id), aber SECHS andere Stellen fragten nur
      `role == "dealer"`: firmenweite Einstellungen, Logo, Abo-Kuendigung,
      Firmenprofil sowie Fahrer-ID/E-Mail in Termin- und Fahrerliste.
  P1  Legten zwei Sucher fast gleichzeitig einen Vertrag zum SELBEN
      Fahrzeug an, bekam der zweite nach 6 s einen sichtbaren 503 und
      musste von Hand erneut speichern.
  P1  Nach dem endgueltigen Loeschen eines Vertrags konnte der
      Fahrzeugstatus dauerhaft falsch bleiben: die Zusammenfassung wirft
      NIE, sie liefert bei einem Konflikt ein stilles None — und der
      Loeschpfad achtete nur auf eine Ausnahme.
"""
import asyncio
import inspect
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import deps  # noqa: E402

MONGO_URL = __import__("os").environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_p0_{uuid.uuid4().hex[:10]}"
    db = client[name]
    monkeypatch.setattr(deps, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


async def _firma_mit_zwei_chefs(db, mit_zeiger=True):
    """Genau die Lage aus dem Befund: eine Firma, zwei dealer-Konten."""
    await db.dealers.insert_one({
        "id": "f1", "company_name": "Norden Autoankauf", "kunden_nr": 10002,
        **({"user_id": "chef"} if mit_zeiger else {})})
    await db.users.insert_many([
        {"id": "chef", "role": "dealer", "dealer_id": "f1", "active": True,
         "created_at": "2026-01-01T10:00:00+00:00"},
        {"id": "zweiter", "role": "dealer", "dealer_id": "f1", "active": True,
         "created_at": "2026-06-01T10:00:00+00:00"},
        {"id": "sucher", "role": "sucher", "dealer_id": "f1", "active": True,
         "created_at": "2026-06-02T10:00:00+00:00"},
    ])


# ====================================================== P0
def test_01_nur_der_eingetragene_hauptchef(welt):
    db = welt.db

    async def lauf():
        await _firma_mit_zwei_chefs(db)
        return [await deps.ist_haupt_chef(await db.users.find_one({"id": i}))
                for i in ("chef", "zweiter", "sucher")]

    chef, zweiter, sucher = welt.run(lauf())
    assert chef is True
    assert zweiter is False, "genau das war der Befund"
    assert sucher is False


def test_02_altbestand_ohne_zeiger_das_aelteste_konto(welt):
    """Firmen von vor dem 15.09.2026 haben keinen dealers.user_id."""
    db = welt.db

    async def lauf():
        await _firma_mit_zwei_chefs(db, mit_zeiger=False)
        return [await deps.ist_haupt_chef(await db.users.find_one({"id": i}))
                for i in ("chef", "zweiter")]

    chef, zweiter = welt.run(lauf())
    assert chef is True, "das aelteste dealer-Konto gilt"
    assert zweiter is False


def test_03_fail_closed_bei_unsinn(welt):
    """Im Zweifel KEIN Chef — nie umgekehrt."""
    db = welt.db

    async def lauf():
        await db.dealers.insert_one({"id": "f2", "user_id": "wer-anders"})
        return [
            await deps.ist_haupt_chef({"id": "x", "role": "sucher", "dealer_id": "f2"}),
            await deps.ist_haupt_chef({"id": "x", "role": "dealer", "dealer_id": None}),
            await deps.ist_haupt_chef({"id": "x", "role": "dealer"}),
            await deps.ist_haupt_chef({"id": "x", "role": "b2b_buyer", "dealer_id": "f2"}),
            await deps.ist_haupt_chef({}),
            # Firma existiert gar nicht -> kein Zeiger, kein dealer-Konto
            await deps.ist_haupt_chef({"id": "x", "role": "dealer", "dealer_id": "gibts-nicht"}),
        ]

    aus = welt.run(lauf())
    assert aus[:5] == [False, False, False, False, False]


@pytest.mark.parametrize("datei,stelle", [
    ("routes/dealer.py", "Das Firmenlogo ändert nur der Chef."),
    ("routes/dealer.py", "Nur der Händler-Hauptaccount darf Abos verwalten"),
])
def test_04_die_schreibenden_chef_wege_pruefen_richtig(datei, stelle):
    quelle = (BACKEND / datei).read_text(encoding="utf-8")
    block = quelle[max(0, quelle.index(stelle) - 400):quelle.index(stelle)]
    assert "ist_haupt_chef(user)" in block, (
        f"'{stelle[:40]}...' haengt noch an role == dealer (P0)")


def test_05_alle_sechs_stellen_sind_umgestellt():
    """Gegenprobe: nirgends mehr `role == "dealer"` als Chef-Pruefung."""
    treffer = []
    for datei in ("routes/dealer.py", "routes/appointments.py", "routes/drivers.py"):
        for nr, zeile in enumerate((BACKEND / datei).read_text(encoding="utf-8")
                                   .splitlines(), 1):
            code = zeile.split("#", 1)[0]
            if 'role") == "dealer"' in code or "role') == 'dealer'" in code:
                treffer.append(f"{datei}:{nr}")
    assert not treffer, ("hier entscheidet weiter die blosse Rolle statt des "
                         f"Hauptchefs: {treffer}")


def test_06_die_sechs_stellen_nutzen_den_helfer():
    erwartet = {"routes/dealer.py": 4,        # settings-GET, settings-PUT, Logo, Abo, Profil
                "routes/appointments.py": 2,  # zwei Termin-Ansichten
                "routes/drivers.py": 1}       # Fahrerliste
    for datei, mindestens in erwartet.items():
        quelle = (BACKEND / datei).read_text(encoding="utf-8")
        assert quelle.count("ist_haupt_chef(user)") >= mindestens, datei


def test_07_current_chef_bleibt_wie_es_war():
    """Der bisher EINZIGE richtige Weg darf sich nicht verschlechtern."""
    q = inspect.getsource(deps.current_chef)
    assert "dealers.find_one" in q and "user_id" in q
    assert "403" in q


# ====================================================== P1: Vertragssperre
def test_08_der_server_sagt_dass_wiederholen_sicher_ist():
    import routes.contracts as C
    q = inspect.getsource(C)
    stelle = q.split("except auto_daten.SperreBelegt:")[1][:1200]
    assert '"X-Wiederholen": "1"' in stelle, (
        "an dieser Stelle steht fest, dass NICHTS geschrieben wurde — das "
        "muss die Oberflaeche erfahren (P1)")
    assert '"Retry-After"' in stelle


def test_09_die_oberflaeche_wiederholt_nur_mit_dieser_kopfzeile():
    """Wichtig: ein beliebiger 503 darf NIE wiederholt werden — sonst
    entstuende beim Vertragsanlegen ein zweiter."""
    quelle = (WURZEL / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    assert "darfWiederholen" in quelle and "x-wiederholen" in quelle
    teil = quelle.split("export function darfWiederholen")[1][:600]
    assert "status !== 503" in teil, "nur 503"
    assert 'String(kopf || "") !== "1"' in teil, "und nur mit der Kopfzeile"
    assert "__versuche" in teil, "und begrenzt oft"


def test_10_die_wartezeit_kommt_aus_retry_after():
    quelle = (WURZEL / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    teil = quelle.split("export function wiederholenNachMs")[1][:400]
    assert "retry-after" in teil
    assert "Math.min(sek, 30)" in teil, "kein unbegrenztes Warten"


# ====================================== P1: Fahrzeugstatus nach Loeschung
def test_11_ein_stilles_none_loest_jetzt_alarm_aus():
    import cleanup_service as CS
    q = inspect.getsource(CS.vertrag_endgueltig_loeschen)
    stelle = q.split("Fahrzeugstatus neu ableiten")[1][:2000]
    assert "is None" in stelle, (
        "fahrzeug_status_aggregieren wirft NIE — sie liefert bei einem "
        "Konflikt None. Nur auf eine Ausnahme zu achten reichte nicht (P1)")
    assert 'alarm(db, "fahrzeugstatus_nach_loeschung_offen"' in stelle
    assert "offen" in stelle, "und der Alarm nennt genau die Fahrzeuge"


def test_12_die_aggregation_wirft_wirklich_nie():
    """Gegenprobe zum Befund — stimmt die Annahme ueberhaupt?"""
    import kaufvorgang as KV
    q = inspect.getsource(KV.fahrzeug_status_aggregieren)
    assert "Nie eine Exception nach aussen" in q
    assert "-> Optional[str]" in q, "None ist das Signal fuer 'gescheitert'"


def test_13_die_nacharbeit_hat_es_immer_richtig_gemacht():
    """Der Beweis, dass es im Haus schon das richtige Muster gab: die
    Vertrags-Nacharbeit prueft `is None` seit Phase 2."""
    import cleanup_service as CS
    q = inspect.getsource(CS)
    assert "fahrzeug_status_aggregieren(c.get(\"vehicle_id\"), c[\"dealer_id\"]) is None" in q
