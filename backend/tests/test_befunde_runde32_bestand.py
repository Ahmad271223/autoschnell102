# -*- coding: utf-8 -*-
"""Runde 32 (12.09.2026, Wunsch Ahmad): Der Bestand zeigt nur noch

  * Fahrzeuge, zu denen ein Kaufvertrag gespeichert oder verschickt wurde,
  * dazu die von Hand hinzugefuegten (Entscheidung Ahmad).

Vorher stand dort jedes nicht geloeschte Fahrzeug — auch jedes, das nur
verglichen wurde. Fuer die Akte nur verglichener Autos braucht der Chef keinen
eigenen Weg (Entscheidung Ahmad).

SUCHER: eigener Vertrag, eigene Abholung, oder von Hand angelegtes Auto, das
ihm gehoert. CHEF: das fuer die ganze Firma plus alles, was per Termin abgeholt
wurde oder auf dem Hof bzw. im Weiterverkauf steht.

Zwei Gegenpruefungen haben Luecken der ersten Fassungen bestaetigt; jede ist
hier mit einem Test festgehalten.

In-Prozess gegen eine Wegwerf-DB; kein Server.
"""
import asyncio
import importlib
import inspect
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = "mongodb://127.0.0.1:27017"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _modul(name):
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    namen = ["deps", "routes.bestand", "routes.contracts", "kaufvorgang", "lifecycle",
             "cleanup_service"]
    mods = [_modul(n) for n in namen]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_r32_{s}"
    w.chef = {"id": f"chef_r32_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.a = {"id": f"sa_r32_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.b = {"id": f"sb_r32_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_r32_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_many([
        {**w.chef, "active": True, "first_name": "Chef", "last_name": "C", "created_at": _jetzt()},
        {**w.a, "active": True, "first_name": "Anna", "last_name": "A", "created_at": _jetzt()},
        {**w.b, "active": True, "first_name": "Ben", "last_name": "B", "created_at": _jetzt()}]))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   "company_name": "Firma R32", "created_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _fahrzeug(w, name, besitzer, **extra):
    doc = {"id": f"v_{name}_{w.s}", "dealer_id": w.dealer_id, "owner_user_id": besitzer["id"],
           "lifecycle": "verglichen", "status": "verglichen", "source": "plattform",
           "data": {"make_label": "VW", "model_label": name},
           "created_at": _jetzt(), "updated_at": _jetzt(), "lifecycle_changed_at": _jetzt()}
    doc.update(extra)
    return doc


def _vertrag_und_vorgang(w, fahrzeug, von, status="vertrag_erstellt", **vertrag_extra):
    cid = f"c_{fahrzeug['id']}_{von['id']}"
    vertrag = {"id": cid, "dealer_id": w.dealer_id, "user_id": von["id"],
               "vehicle_id": fahrzeug["id"], "contract_no": "R32-1",
               "send_status": [], "status": "erstellt", "created_at": _jetzt()}
    vertrag.update(vertrag_extra)
    vorgang = {"id": f"k_{cid}", "dealer_id": w.dealer_id, "user_id": von["id"],
               "vehicle_id": fahrzeug["id"], "contract_id": cid, "status": status,
               "purchase_price": 10000, "created_at": _jetzt(), "updated_at": _jetzt()}
    return vertrag, vorgang


async def _anlegen(w, fahrzeuge=(), paare=(), nur_vertraege=(), nur_vorgaenge=(), termine=()):
    if fahrzeuge:
        await w.db.vehicles.insert_many([dict(f) for f in fahrzeuge])
    for vertrag, vorgang in paare:
        await w.db.generated_pdfs.insert_one(dict(vertrag))
        await w.db.kaufvorgaenge.insert_one(dict(vorgang))
    for vertrag in nur_vertraege:
        await w.db.generated_pdfs.insert_one(dict(vertrag))
    for vorgang in nur_vorgaenge:
        await w.db.kaufvorgaenge.insert_one(dict(vorgang))
    for termin in termine:
        await w.db.appointments.insert_one(dict(termin))


def _ids(liste):
    return {i["id"] for i in liste["items"]}


async def _alle(w):
    B = _modul("routes.bestand")
    return (await B.list_bestand(w.chef), await B.list_bestand(w.a), await B.list_bestand(w.b))


# ------------------------------------------------------------ die Regel
def test_01_nur_verglichen_steht_nicht_im_bestand(welt):
    w = welt
    vgl = _fahrzeug(w, "verglichen", w.a)

    async def lauf():
        await _anlegen(w, [vgl])
        return await _alle(w)

    chef, a, _ = w.run(lauf())
    assert vgl["id"] not in _ids(chef) and vgl["id"] not in _ids(a)
    assert chef["counts"] == {} and chef["gesamt"] == 0


def test_02_gespeicherter_vertrag_bringt_das_auto_in_den_bestand(welt):
    """Sucher: nur mit EIGENEM Vertrag — Mitbearbeiter ohne Vertrag nicht."""
    w = welt
    auto = _fahrzeug(w, "gemeinsam", w.a, lifecycle="vertrag_erstellt",
                     mitbearbeiter_ids=[w.b["id"]])

    async def lauf():
        await _anlegen(w, [auto], [_vertrag_und_vorgang(w, auto, w.a)])
        return await _alle(w)

    chef, a, b = w.run(lauf())
    assert _ids(chef) == {auto["id"]} and chef["counts"] == {"vertrag_erstellt": 1}
    assert _ids(a) == {auto["id"]}
    assert _ids(b) == set(), "B arbeitet mit, hat aber keinen eigenen Vertrag"


def test_03_verschickter_vertrag_ebenso(welt):
    w = welt
    auto = _fahrzeug(w, "verschickt", w.a, lifecycle="vertrag_erstellt")
    paar = _vertrag_und_vorgang(w, auto, w.a,
                                send_status=[{"channel": "email", "status": "ok", "sent_at": _jetzt()}])

    async def lauf():
        await _anlegen(w, [auto], [paar])
        return await _alle(w)

    assert _ids(w.run(lauf())[1]) == {auto["id"]}


def test_04_chef_sieht_die_vertraege_aller_sucher(welt):
    """B legt einen Vertrag an einem Auto an, dessen Hauptbearbeiter A ist:
    Chef und B sehen es, A (ohne eigenen Vertrag) nicht."""
    w = welt
    auto = _fahrzeug(w, "von_b", w.a, lifecycle="vertrag_erstellt",
                     mitbearbeiter_ids=[w.b["id"]])

    async def lauf():
        await _anlegen(w, [auto], [_vertrag_und_vorgang(w, auto, w.b)])
        return await _alle(w)

    chef, a, b = w.run(lauf())
    assert _ids(chef) == {auto["id"]} and _ids(b) == {auto["id"]} and _ids(a) == set()


def test_05_von_hand_hinzugefuegte_bleiben(welt):
    w = welt
    B = _modul("routes.bestand")
    hand = _fahrzeug(w, "hand", w.chef, source="manuell", lifecycle="bestand")
    mit_vertrag = _fahrzeug(w, "vertrag", w.a, lifecycle="gekauft")

    async def lauf():
        await _anlegen(w, [hand, mit_vertrag], [_vertrag_und_vorgang(w, mit_vertrag, w.a)])
        return (await B.list_bestand(w.chef), await B.list_bestand(w.chef, source="manuell"),
                await B.list_bestand(w.chef, source="plattform"), await B.list_bestand(w.a))

    alle, nur_hand, nur_plattform, a = w.run(lauf())
    assert _ids(alle) == {hand["id"], mit_vertrag["id"]}
    assert _ids(nur_hand) == {hand["id"]} and _ids(nur_plattform) == {mit_vertrag["id"]}
    assert _ids(a) == {mit_vertrag["id"]}


def test_06_vertrag_ohne_vorgang_zaehlt_trotzdem(welt):
    """Scheitert nach dem Speichern das Anlegen des Vorgangs, gibt es den
    Vertrag trotzdem — das Auto gehoert in den Bestand."""
    w = welt
    auto = _fahrzeug(w, "ohne_vorgang", w.a, lifecycle="vertrag_erstellt")
    vertrag, _ = _vertrag_und_vorgang(w, auto, w.a)

    async def lauf():
        await _anlegen(w, [auto], nur_vertraege=[vertrag])
        return await _alle(w)

    chef, a, _ = w.run(lauf())
    assert _ids(chef) == {auto["id"]} and _ids(a) == {auto["id"]}


def test_07_vertrag_im_grabstein_zaehlt_nicht(welt):
    w = welt
    auto = _fahrzeug(w, "grabstein", w.a, lifecycle="vertrag_erstellt")
    vertrag, _ = _vertrag_und_vorgang(w, auto, w.a, loeschung={"status": "laeuft"})

    async def lauf():
        await _anlegen(w, [auto], nur_vertraege=[vertrag])
        return await _alle(w)

    assert auto["id"] not in _ids(w.run(lauf())[0])


# ------------------------------------------------------------ Loeschen
def _loeschen_lauf(w, grund, lifecycle, vorgang_status="vertrag_erstellt"):
    C = _modul("cleanup_service")
    auto = _fahrzeug(w, f"loesch_{grund}_{lifecycle}", w.a, lifecycle=lifecycle)
    vertrag, vorgang = _vertrag_und_vorgang(w, auto, w.a, status=vorgang_status)

    async def lauf():
        await _anlegen(w, [auto], [(vertrag, vorgang)])
        vorher = await _alle(w)
        ok = await C.vertrag_endgueltig_loeschen(w.db, vertrag["id"], scrub_pii=(grund == "90tage"),
                                                 grund=grund, audit=False)
        nachher = await _alle(w)
        kv = await w.db.kaufvorgaenge.find_one({"id": vorgang["id"]}, {"_id": 0})
        return vorher, ok, nachher, kv

    return auto, w.run(lauf())


def test_08_vor_der_abholung_geloeschter_vertrag_nimmt_das_auto_heraus(welt):
    """Der Kauf wurde zurueckgezogen — das Auto gehoert nicht in den Bestand."""
    auto, (vorher, ok, nachher, kv) = _loeschen_lauf(welt, "manuell", "vertrag_erstellt")
    assert auto["id"] in _ids(vorher[1]) and ok is True
    assert kv["vertrag_geloescht_grund"] == "manuell" and kv["vertrag_geloescht_am"]
    assert auto["id"] not in _ids(nachher[1]) and auto["id"] not in _ids(nachher[0])


def test_09_nach_der_abholung_geloeschter_vertrag_laesst_das_auto_im_bestand(welt):
    """Gegenpruefung (hoch): Das Auto steht auf dem Hof und wartet auf die
    Entscheidung — auch wenn danach jemand (sogar ein Sucher) den Vertrag loescht."""
    auto, (vorher, ok, nachher, kv) = _loeschen_lauf(
        welt, "manuell", "abgeholt", vorgang_status="abgeholt")
    assert auto["id"] in _ids(vorher[1]) and ok is True and kv["status"] == "abgeholt"
    assert auto["id"] in _ids(nachher[1]) and auto["id"] in _ids(nachher[0])
    assert nachher[0]["counts"].get("abgeholt") == 1, "das Banner 'Entscheidung faellig' zaehlt es"


def test_10_im_weiterverkauf_geloeschter_vertrag(welt):
    """Vorgang noch offen -> storniert, das Auto ist aber schon inseriert: Der
    Chef behaelt es (er verkauft es gerade), der Sucher hat seinen Kauf
    zurueckgezogen und sieht es nicht mehr."""
    auto, (vorher, ok, nachher, kv) = _loeschen_lauf(welt, "manuell", "veroeffentlicht")
    assert ok is True and kv["status"] == "storniert"
    assert auto["id"] in _ids(nachher[0])
    assert auto["id"] not in _ids(nachher[1])


def test_11_fristloeschung(welt):
    """Abgeholt und dann nach Frist geloescht: bleibt. Nie abgeholt: die
    Fristloeschung storniert den offenen Kauf — das Auto geht heraus."""
    auto_ab, (_, ok1, nach_ab, kv_ab) = _loeschen_lauf(welt, "90tage", "abgeholt", vorgang_status="abgeholt")
    auto_off, (vor_off, ok2, nach_off, kv_off) = _loeschen_lauf(welt, "90tage", "gekauft")
    assert ok1 and ok2
    assert kv_ab["vertrag_geloescht_grund"] == "90tage" and kv_off["status"] == "storniert"
    assert auto_ab["id"] in _ids(nach_ab[1]) and auto_ab["id"] in _ids(nach_ab[0])
    assert auto_off["id"] in _ids(vor_off[1])
    assert auto_off["id"] not in _ids(nach_off[1]) and auto_off["id"] not in _ids(nach_off[0])


def test_12_altfaelle_ohne_markierung_zaehlen_nicht(welt):
    """Vor Runde 29 blieb der Vorgang bei einer Loeschung unmarkiert.
    Massgeblich ist deshalb, ob es den VERTRAG noch gibt."""
    w = welt
    ohne_markierung = _fahrzeug(w, "alt_ohne", w.a, lifecycle="vertrag_erstellt")
    ohne_grund = _fahrzeug(w, "alt_grundlos", w.a, lifecycle="gekauft")
    _, v1 = _vertrag_und_vorgang(w, ohne_markierung, w.a)
    _, v2 = _vertrag_und_vorgang(w, ohne_grund, w.a, status="storniert")
    v2["vertrag_geloescht_am"] = _jetzt()

    async def lauf():
        await _anlegen(w, [ohne_markierung, ohne_grund], nur_vorgaenge=[v1, v2])
        return await _alle(w)

    chef, a, _ = w.run(lauf())
    for liste in (chef, a):
        assert ohne_markierung["id"] not in _ids(liste) and ohne_grund["id"] not in _ids(liste)


def test_13_wiederaufgenommene_loeschung_traegt_den_grund_nach(welt):
    """Gegenpruefung (niedrig): Mit aelterem Code begonnen (Markierung ohne
    Grund, Grabstein 'laeuft'), nach dem Deploy wieder aufgenommen."""
    w = welt
    C = _modul("cleanup_service")
    auto = _fahrzeug(w, "wiederaufnahme", w.a, lifecycle="gekauft")
    vertrag, vorgang = _vertrag_und_vorgang(
        w, auto, w.a, loeschung={"status": "laeuft", "grund": "90tage", "scrub_pii": True})
    vorgang["vertrag_geloescht_am"] = _jetzt()

    async def lauf():
        await _anlegen(w, [auto], [(vertrag, vorgang)])
        await C.vertrag_endgueltig_loeschen(w.db, vertrag["id"], scrub_pii=False,
                                            grund="manuell", audit=False)
        return await w.db.kaufvorgaenge.find_one({"id": vorgang["id"]}, {"_id": 0})

    assert w.run(lauf())["vertrag_geloescht_grund"] == "90tage", "Grund aus dem Grabstein"


# ------------------------------------------------------------ Sucher-Trennung
def test_14_erstvergleicher_sieht_den_kauf_des_kollegen_nicht(welt):
    """Gegenpruefung (mittel): Besitzer wird schon, wer als Erster VERGLEICHT.
    Kauft und holt B das Auto, gehoert es nicht in As Bestand."""
    w = welt
    auto = _fahrzeug(w, "erstvergleich", w.a, lifecycle="abgeholt", mitbearbeiter_ids=[w.b["id"]])

    async def lauf():
        await _anlegen(w, [auto], [_vertrag_und_vorgang(w, auto, w.b, status="abgeholt")])
        return await _alle(w)

    chef, a, b = w.run(lauf())
    assert _ids(chef) == {auto["id"]} and _ids(b) == {auto["id"]}
    assert _ids(a) == set(), "A hat nur verglichen"


def test_15_zurueckgezogener_eigener_kauf_holt_kein_fremdes_auto(welt):
    """Gegenpruefung (mittel): B hat seinen Vertrag vor der Abholung geloescht
    (Vorgang storniert), A holt ab — B sieht das Auto nicht."""
    w = welt
    auto = _fahrzeug(w, "zurueckgezogen", w.a, lifecycle="abgeholt", mitbearbeiter_ids=[w.b["id"]])
    _, vorgang_b = _vertrag_und_vorgang(w, auto, w.b, status="storniert")
    vorgang_b.update(vertrag_geloescht_am=_jetzt(), vertrag_geloescht_grund="manuell")

    async def lauf():
        await _anlegen(w, [auto], [_vertrag_und_vorgang(w, auto, w.a, status="abgeholt")],
                       nur_vorgaenge=[vorgang_b])
        return await _alle(w)

    chef, a, b = w.run(lauf())
    assert _ids(chef) == {auto["id"]} and _ids(a) == {auto["id"]} and _ids(b) == set()


def test_16_von_hand_angelegtes_nur_fuer_den_besitzer(welt):
    """Gegenpruefung (niedrig): Ein Mitbearbeiter behielt das von Hand
    angelegte Chef-Auto dauerhaft. Jetzt nur, wem es gehoert."""
    w = welt
    B = _modul("routes.bestand")
    hand = _fahrzeug(w, "hand_mit", w.chef, source="manuell", lifecycle="bestand",
                     mitbearbeiter_ids=[w.b["id"]])

    async def lauf():
        await _anlegen(w, [hand])
        vorher = await _alle(w)
        await B.set_vehicle_owner(hand["id"], B.BesitzerIn(owner_user_id=w.a["id"]), w.chef)
        return vorher, await _alle(w)

    vorher, nachher = w.run(lauf())
    assert _ids(vorher[0]) == {hand["id"]} and _ids(vorher[2]) == set()
    assert _ids(nachher[1]) == {hand["id"]}, "dem Sucher zugewiesen"
    assert _ids(nachher[2]) == set()


# ------------------------------------------------------------ Chef: Hof & Termine
def test_17_altbestand_ohne_vorgang_nur_beim_chef(welt):
    """Ein Auto im Bestand ohne jeden Vorgang (Altdaten): Der Chef sieht es;
    Sucher nicht — auch nicht der Besitzer (Besitz kommt vom Erstvergleich)."""
    w = welt
    alt = _fahrzeug(w, "altbestand", w.a, lifecycle="bestand", mitbearbeiter_ids=[w.b["id"]])

    async def lauf():
        await _anlegen(w, [alt])
        return await _alle(w)

    chef, a, b = w.run(lauf())
    assert _ids(chef) == {alt["id"]} and _ids(a) == set() and _ids(b) == set()


def test_18_per_terminplaner_abgeholt_nach_vertragsloeschung(welt):
    """Gegenpruefung (mittel): Vertrag von Hand geloescht, der Chef setzt den
    Termin trotzdem auf abgeholt — das Fahrzeug haengt auf 'storniert'. Der Chef
    muss es im Bestand finden."""
    w = welt
    storniert = _fahrzeug(w, "planer_abgeholt", w.a, lifecycle="storniert")
    erledigt = _fahrzeug(w, "planer_erledigt", w.a, lifecycle="storniert")
    offen = _fahrzeug(w, "planer_offen", w.a, lifecycle="storniert")
    termine = [{"id": f"t_{f['id']}", "dealer_id": w.dealer_id, "vehicle_id": f["id"], "status": st}
               for f, st in ((storniert, "abgeholt"), (erledigt, "erledigt"), (offen, "offen"))]

    async def lauf():
        await _anlegen(w, [storniert, erledigt, offen], termine=termine)
        return await _alle(w)

    chef, a, _ = w.run(lauf())
    assert _ids(chef) == {storniert["id"], erledigt["id"]}
    assert _ids(a) == set()


def test_19_zaehler_gesamt_und_filter_folgen_der_regel(welt):
    w = welt
    B = _modul("routes.bestand")
    drin = [_fahrzeug(w, f"drin{i}", w.a, lifecycle="bestand") for i in range(3)]
    verkauft = _fahrzeug(w, "verkauft", w.a, lifecycle="verkauft")
    draussen = [_fahrzeug(w, f"draussen{i}", w.a, lifecycle="verglichen") for i in range(4)]
    weg = _fahrzeug(w, "geloescht", w.a, lifecycle="geloescht")

    async def lauf():
        await _anlegen(w, [*drin, verkauft, *draussen, weg],
                       [_vertrag_und_vorgang(w, f, w.a) for f in (*drin, verkauft, weg)])
        return (await B.list_bestand(w.chef), await B.list_bestand(w.chef, lifecycle="bestand"),
                await B.list_bestand(w.chef, lifecycle="verkauft"), await B.list_bestand(w.a))

    alle, bestand, nur_verkauft, a = w.run(lauf())
    assert alle["counts"] == {"bestand": 3, "verkauft": 1}, "Zaehler nur fuer sichtbare Autos"
    assert alle["gesamt"] == 4 and len(alle["items"]) == 4 and alle["gekuerzt"] is False
    assert _ids(bestand) == {f["id"] for f in drin} and bestand["counts"] == alle["counts"]
    assert _ids(nur_verkauft) == {verkauft["id"]}
    assert a["counts"] == alle["counts"] and a["gesamt"] == 4


def test_20_andere_firma_bleibt_draussen(welt):
    w = welt
    auto = _fahrzeug(w, "fremd", w.a, lifecycle="vertrag_erstellt")
    vertrag, vorgang = _vertrag_und_vorgang(w, auto, w.a, status="abgeholt")
    vertrag["dealer_id"] = vorgang["dealer_id"] = "d_fremd"
    termin = {"id": "t_fremd", "dealer_id": "d_fremd", "vehicle_id": auto["id"], "status": "abgeholt"}

    async def lauf():
        await _anlegen(w, [auto], [(vertrag, vorgang)], termine=[termin])
        return await _alle(w)

    for liste in w.run(lauf()):
        assert auto["id"] not in _ids(liste)


def test_21_filter_verknuepft_den_sucher_bereich_sicher():
    """fahrzeug_bereich enthaelt fuer Sucher selbst ein $or — ein dict-Merge
    haette es still ueberschrieben und fremde Autos gezeigt."""
    B = _modul("routes.bestand")
    quelle = inspect.getsource(B.bestand_filter)
    assert '"$and": [fahrzeug_bereich(user)' in quelle
    assert "**fahrzeug_bereich" not in inspect.getsource(B.list_bestand)
