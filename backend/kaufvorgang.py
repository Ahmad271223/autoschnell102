# -*- coding: utf-8 -*-
"""Kaufvorgaenge (Umbau 09.09.2026, Wunsch Ahmad).

Bisher war `vehicle_id` zugleich das INSERAT (gemeinsam je Firma, id
v_<Inserats-ID>) und der KAUFVORGANG eines einzelnen Suchers. Sobald zwei
Sucher derselben Firma dasselbe Auto vergleichen und beide einen Vertrag
anlegen duerfen, passt das nicht mehr: Kaufpreis, Termin und Status
ueberschrieben sich gegenseitig.

Jetzt gilt:
  * `vehicles`            = Inserat/Fahrzeug, gemeinsam (Daten, Fotos,
                            Kleinanzeigen-Snapshot, organisatorischer
                            Bearbeiter owner_user_id + mitbearbeiter_ids)
  * `kaufvorgaenge`       = EIN Vorgang je Vertrag: Sucher, Fahrzeug,
                            Vertrag, Kaufpreis, Status, Termin
  * `generated_pdfs`      = Vertrag (weiter je Ersteller getrennt)
  * `appointments`        = Termin zu genau einem Vertrag/Kaufvorgang

Der Fahrzeug-Lebenszyklus (lifecycle.py) ist nur noch eine ZUSAMMENFASSUNG
aller Kaufvorgaenge des Fahrzeugs (fahrzeug_status_aggregieren): abgeholt,
sobald ein Vorgang abgeholt ist; "nicht abgeholt" erst, wenn kein Vorgang
mehr offen ist. Der realisierte Kaufpreis (vehicles.purchase_price) wird
erst beim Abholen aus dem erfolgreichen Vorgang uebernommen.
"""
import logging
import uuid
from typing import Any, Dict, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from deps import db, now_iso
from lifecycle import abholung_zuruecknehmen, try_set_lifecycle

log = logging.getLogger("autohandel")

STATUS = ("vertrag_erstellt", "gesendet", "abholung_geplant",
          "abgeholt", "nicht_abgeholt", "storniert")
OFFEN = ("vertrag_erstellt", "gesendet", "abholung_geplant")
# Terminstatus -> Vorgangsstatus
_TERMIN_ZU_STATUS = {"abgeholt": "abgeholt", "nicht abgeholt": "nicht_abgeholt",
                     "storniert": "storniert", "erledigt": "abgeholt"}
_KETTE = ["verglichen", "vertrag_erstellt", "gekauft", "abholung_geplant", "abgeholt"]
# Fahrzeug-Lebenszyklen, in denen die Zusammenfassung nichts mehr veraendert
# (Einkaufspreis/realisierter Vorgang sind dann Geschichte).
ABGESCHLOSSEN_FAHRZEUG = frozenset({"verkauft", "archiviert", "geloescht"})


def bereich(user) -> Dict[str, Any]:
    """Chef: alle Vorgaenge der Firma; Sucher: nur eigene."""
    b: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    if (user or {}).get("role") == "sucher":
        b["user_id"] = user["id"]
    return b


async def _verweis_alarm(ref: str, **details) -> None:
    """Phase 2 (2.3): Betriebsalarm bei einem Vorgangs-Verweis, der nicht zum
    Vertrag/Termin passt (beschaedigter oder alter Zeiger). Best effort."""
    try:
        import betrieb as _betrieb
        await _betrieb.alarm(db, "kaufvorgang_verweis_falsch", ref=ref, **details)
    except Exception:  # noqa: BLE001
        log.exception("Alarm kaufvorgang_verweis_falsch (%s) nicht abgesetzt", ref)


def _passt(kv: dict, **erwartet) -> bool:
    """Phase 2 (15.09.2026, 2.3 / G1 G2 G15): Ein Vorgang gehoert nur dann zum
    Vertrag bzw. Termin, wenn Firma, Vertrag und Fahrzeug uebereinstimmen.
    Leere Erwartungen (Termin ohne Fahrzeug/Vertrag) werden nicht geprueft."""
    for feld, wert in erwartet.items():
        if wert and (kv.get(feld) or None) != wert:
            return False
    return True


async def _nacharbeit_merken(kv: dict, ziel: Optional[str]) -> None:
    """Phase 2 (2.4 / E7 G4): Fahrzeug-Zusammenfassung gescheitert (None) ->
    Merker nacharbeit_offen am Vorgang, den cleanup_service.
    kaufvorgang_nacharbeit_nachholen abarbeitet; gelungen -> Merker weg.
    Best effort — der Vorgangs-Write selbst bleibt gueltig."""
    try:
        if ziel is None:
            await db.kaufvorgaenge.update_one({"id": kv["id"]},
                                              {"$set": {"nacharbeit_offen": True}})
            kv["nacharbeit_offen"] = True
        elif kv.get("nacharbeit_offen"):
            await db.kaufvorgaenge.update_one(
                {"id": kv["id"]},
                {"$unset": {"nacharbeit_offen": "", "nacharbeit_versuche": ""}})
            kv.pop("nacharbeit_offen", None)
    except Exception:  # noqa: BLE001
        log.exception("Nacharbeitsmerker fuer Kaufvorgang %s nicht geschrieben", kv.get("id"))


async def anlegen(*, dealer_id: str, user_id: str, vehicle_id: str, contract_id: str,
                  purchase_price=None, status: str = "vertrag_erstellt",
                  kaufvorgang_id: Optional[str] = None,
                  appointment_id: Optional[str] = None) -> dict:
    """Vorgang anlegen; je Vertrag genau einer (Unique-Index contract_id).
    Existiert er schon (Wiederholung), wird der vorhandene geliefert."""
    doc = {"id": kaufvorgang_id or str(uuid.uuid4()), "dealer_id": dealer_id,
           "user_id": user_id, "vehicle_id": vehicle_id, "contract_id": contract_id,
           "purchase_price": purchase_price, "status": status,
           "appointment_id": appointment_id,
           "created_at": now_iso(), "updated_at": now_iso()}
    try:
        await db.kaufvorgaenge.insert_one(dict(doc))
    except DuplicateKeyError:
        alt = await db.kaufvorgaenge.find_one({"contract_id": contract_id}, {"_id": 0})
        if alt:
            return alt
        raise
    doc.pop("_id", None)
    return doc


async def _vertragszeiger_nachziehen(contract: dict, kv: Optional[dict]) -> None:
    """Pruefbericht 20.09.2026 (R2-01): Die Selbstheilung fand bzw. legte den
    Vorgang an, schrieb seine ID aber nie an den Vertrag zurueck — jeder
    weitere Aufruf alarmierte erneut, und neue Termine kopierten den alten,
    falschen Zeiger. Jetzt per Compare-and-Set (nur der gelesene Stand)."""
    if not contract or not kv or not contract.get("id") \
            or contract.get("kaufvorgang_id") == kv.get("id"):
        return
    try:
        await db.generated_pdfs.update_one(
            {"id": contract["id"], "kaufvorgang_id": contract.get("kaufvorgang_id")},
            {"$set": {"kaufvorgang_id": kv["id"]}})
    except Exception:  # noqa: BLE001  (der naechste Aufruf versucht es erneut)
        log.exception("Vertragszeiger %s -> Kaufvorgang %s nicht nachgezogen",
                      contract.get("id"), kv.get("id"))


async def fuer_vertrag(contract: dict) -> Optional[dict]:
    if not contract:
        return None
    zeiger_falsch = False
    if contract.get("kaufvorgang_id"):
        kv = await db.kaufvorgaenge.find_one({"id": contract["kaufvorgang_id"]}, {"_id": 0})
        if kv and _passt(kv, contract_id=contract.get("id"), dealer_id=contract.get("dealer_id"),
                         vehicle_id=contract.get("vehicle_id")):
            return kv
        if kv:
            # Phase 2 (2.3, G1): der Zeiger fuehrt zu einem fremden Vorgang —
            # nicht folgen, melden, ueber den Vertrag selbst suchen.
            zeiger_falsch = True
            log.warning("Vertrag %s zeigt auf fremden Kaufvorgang %s",
                        contract.get("id"), kv.get("id"))
            await _verweis_alarm(f"vertrag:{contract.get('id')}", kaufvorgang_id=kv.get("id"))
    if contract.get("id"):
        kv = await db.kaufvorgaenge.find_one({"contract_id": contract["id"]}, {"_id": 0})
        if kv:
            if _passt(kv, dealer_id=contract.get("dealer_id"), vehicle_id=contract.get("vehicle_id")):
                await _vertragszeiger_nachziehen(contract, kv)
                return kv
            # Phase 2 (2.3, G15): der Vorgang zum Vertrag gehoert einer anderen
            # Firma / einem anderen Fahrzeug — nicht als gueltig uebernehmen.
            log.error("Kaufvorgang %s passt nicht zu Vertrag %s (Firma/Fahrzeug)",
                      kv.get("id"), contract["id"])
            await _verweis_alarm(f"vertrag:{contract['id']}", kaufvorgang_id=kv.get("id"),
                                 grund="firma_oder_fahrzeug")
            return None
        # Selbstheilung: der Vertrag traegt eine kaufvorgang_id, der Vorgang
        # fehlt (Anlage nach dem Vertrags-Insert gescheitert) -> nachlegen.
        if contract.get("dealer_id") and contract.get("vehicle_id") and contract.get("user_id"):
            neu = await anlegen(
                dealer_id=contract["dealer_id"], user_id=contract["user_id"],
                vehicle_id=contract["vehicle_id"], contract_id=contract["id"],
                purchase_price=contract.get("purchase_price"),
                kaufvorgang_id=None if zeiger_falsch else (contract.get("kaufvorgang_id") or None),
                appointment_id=contract.get("appointment_id"))
            await _vertragszeiger_nachziehen(contract, neu)
            return neu
    return None


async def fuer_termin(appt: dict) -> Optional[dict]:
    if not appt:
        return None
    # Phase 2 (2.3): Aufrufer geben oft nur id/contract_id/kaufvorgang_id mit —
    # Firma, Fahrzeug und Vertrag fuer die Gegenpruefung nachladen.
    if appt.get("id") and not all(k in appt for k in ("dealer_id", "vehicle_id", "contract_id")):
        voll = await db.appointments.find_one(
            {"id": appt["id"]},
            {"_id": 0, "dealer_id": 1, "vehicle_id": 1, "contract_id": 1, "kaufvorgang_id": 1})
        if voll:
            appt = {**voll, **{k: v for k, v in appt.items() if v is not None}}
    erwartet = {"dealer_id": appt.get("dealer_id"), "vehicle_id": appt.get("vehicle_id"),
                "contract_id": appt.get("contract_id")}
    if appt.get("kaufvorgang_id"):
        kv = await db.kaufvorgaenge.find_one({"id": appt["kaufvorgang_id"]}, {"_id": 0})
        if kv and _passt(kv, **erwartet):
            return kv
        if kv:
            # Phase 2 (2.3, G2/G15): Zeiger auf einen fremden Vorgang — melden,
            # am Termin loesen und ueber den Vertrag neu ermitteln.
            log.warning("Termin %s zeigt auf fremden Kaufvorgang %s", appt.get("id"), kv.get("id"))
            await _verweis_alarm(f"termin:{appt.get('id')}", kaufvorgang_id=kv.get("id"))
            if appt.get("id"):
                await db.appointments.update_one(
                    {"id": appt["id"], "kaufvorgang_id": kv["id"]},
                    {"$unset": {"kaufvorgang_id": ""}})
    if appt.get("contract_id"):
        kv = await db.kaufvorgaenge.find_one({"contract_id": appt["contract_id"]}, {"_id": 0})
        if kv:
            if _passt(kv, dealer_id=appt.get("dealer_id"), vehicle_id=appt.get("vehicle_id")):
                if appt.get("id") and appt.get("kaufvorgang_id") != kv["id"]:
                    # Zeiger am Termin nachziehen (fehlte oder war falsch)
                    await db.appointments.update_one({"id": appt["id"]},
                                                     {"$set": {"kaufvorgang_id": kv["id"]}})
                return kv
            log.error("Kaufvorgang %s passt nicht zu Termin %s (Firma/Fahrzeug)",
                      kv.get("id"), appt.get("id"))
            await _verweis_alarm(f"termin:{appt.get('id')}", kaufvorgang_id=kv.get("id"),
                                 grund="firma_oder_fahrzeug")
            return None
        # Runde 18: Der Termin zeigt auf einen Vertrag, dessen Vorgang fehlt
        # (Anlage nach dem Vertrags-Insert gescheitert; der Auto-Termin trug
        # trotzdem die kaufvorgang_id). Ueber den Vertrag nachlegen und den
        # Termin auf den echten Vorgang zeigen lassen — vorher fiel der
        # Aufrufer dauerhaft auf die direkte Fahrzeugstatus-Aenderung zurueck.
        # Phase 2 (2.3, G3): den Vertrag nur innerhalb der Firma laden.
        vertrag_filt: Dict[str, Any] = {"id": appt["contract_id"]}
        if appt.get("dealer_id"):
            vertrag_filt["dealer_id"] = appt["dealer_id"]
        contract = await db.generated_pdfs.find_one(
            vertrag_filt,
            {"_id": 0, "id": 1, "dealer_id": 1, "user_id": 1, "vehicle_id": 1,
             "purchase_price": 1, "kaufvorgang_id": 1, "appointment_id": 1})
        kv = await fuer_vertrag(contract) if contract else None
        if kv and appt.get("id"):
            await db.appointments.update_one({"id": appt["id"]},
                                             {"$set": {"kaufvorgang_id": kv["id"]}})
            if not kv.get("appointment_id"):
                await db.kaufvorgaenge.update_one(
                    {"id": kv["id"]}, {"$set": {"appointment_id": appt["id"]}})
                kv["appointment_id"] = appt["id"]
        return kv
    return None


async def status_setzen(kaufvorgang_id: str, status: str, *, user: Optional[dict] = None,
                        appointment_id=..., extra: Optional[dict] = None,
                        von: Optional[str] = None) -> Optional[dict]:
    """Status des Vorgangs setzen und den Fahrzeugstatus neu zusammenfassen.
    `appointment_id` nur mitgeben, wenn er sich aendert (auch None).
    Runde 13 (Liste 3 Nr. 14): mit `von` nur, wenn der Vorgang noch diesen
    Ausgangsstatus hat (Compare-and-Set) — sonst None, der zweite Folgeschritt
    ueberschreibt den ersten nicht mehr still."""
    if status not in STATUS:
        raise ValueError(f"unbekannter Kaufvorgang-Status {status!r}")
    setzen: Dict[str, Any] = {"status": status, "updated_at": now_iso(), **(extra or {})}
    if appointment_id is not ...:
        setzen["appointment_id"] = appointment_id
    filt: Dict[str, Any] = {"id": kaufvorgang_id}
    if von is not None:
        filt["status"] = von
    doc = await db.kaufvorgaenge.find_one_and_update(
        filt, {"$set": setzen},
        projection={"_id": 0}, return_document=ReturnDocument.AFTER)
    if doc is None and von is not None:
        log.warning("Kaufvorgang %s: Status %s -> %s nicht gesetzt (Ausgangsstatus inzwischen anders)",
                    kaufvorgang_id, von, status)
    if doc:
        # Phase 2 (2.4, E7/G4): Ergebnis der Zusammenfassung nicht mehr
        # verwerfen — None heisst "Fahrzeug stimmt noch nicht", Merker setzen.
        ziel = await fahrzeug_status_aggregieren(doc["vehicle_id"], doc["dealer_id"], user=user)
        doc["fahrzeug_status"] = ziel
        await _nacharbeit_merken(doc, ziel)
    return doc


async def abholung_protokoll_belegt(appointment_id: str) -> bool:
    """Belegt ein unterschriebenes Abholprotokoll die Abholung dieses Termins?

    Phase 2 (D15): ein finales, nicht abgeloestes Protokoll.
    Rollenprüfung 22.09.2026 (RP-075/174): ODER eine offene Korrektur-Version
    (Entwurf/zur Freigabe/freigegeben/wird unterschrieben, mit
    corrects_version). Eine Korrektur entsteht nur aus einer finalen Version
    (start_correction); solange sie laeuft, ist diese abgeloest. Vorher setzte
    der Frischabgleich den Vorgang in genau diesem Fenster auf "Abholung
    geplant" — und "abgeholt" war danach per Hand gesperrt."""
    if not appointment_id:
        return False
    if await db.pickup_protocols.count_documents(
            {"appointment_id": appointment_id, "status": "final",
             "superseded": {"$ne": True}}, limit=1):
        return True
    return bool(await db.pickup_protocols.count_documents(
        {"appointment_id": appointment_id, "corrects_version": {"$exists": True},
         "superseded": {"$ne": True},
         "status": {"$in": ["entwurf", "zur_freigabe", "freigegeben", "wird_abgeschlossen"]}},
        limit=1))


async def fahrzeug_hat_vorgaenge(vehicle_id: Optional[str], dealer_id: str) -> bool:
    """Rollenprüfung 22.09.2026 (RP-015/114/265): Haengen am (firmenweit
    gemeinsamen) Fahrzeug Kaufvorgaenge — auch die eines Kollegen? Dann
    bestimmt allein deren Zusammenfassung (fahrzeug_status_aggregieren) den
    Lebenszyklus; ein Termin OHNE eigenen Vorgang (Terminplaner ohne Vertrag)
    darf ihn nicht direkt setzen. Vorher schob ein Mitbearbeiter mit einem
    eigenen Termin ohne Vertrag das Auto des Kollegen von "Abholung geplant"
    auf "nicht abgeholt" bzw. "abgeholt". Gemeinsame Regel fuer Terminplaner,
    Fahrer-App, Protokoll-Abschluss und Aufraeumjobs."""
    if not vehicle_id:
        return False
    return bool(await db.kaufvorgaenge.count_documents(
        {"vehicle_id": vehicle_id, "dealer_id": dealer_id}, limit=1))


async def _ki_lernfall_ausgang(appt: dict, termin_status: str) -> None:
    """Review 26.09.2026 (Nr. 76-78): Der KI-Lernfall der Abholung wird erst
    mit dem Ausgang des Termins endgueltig (abgeholt/erledigt: tatsaechlich
    erzielter Nachlass aus dem Endpreis) bzw. verworfen (storniert / nicht
    abgeholt). Diese Funktion ist der eine Punkt, den ALLE Wege passieren
    (Abschluss mit Unterschriften, Buero, Fahrer-App, Nachholer). Nie ein
    Fehler nach aussen — die KI ist Beiwerk."""
    try:
        if not appt or not appt.get("id"):
            return
        from ai import pickup_assessment as _ki
        await _ki.lernfall_ausgang(str(appt["id"]), ausgang=str(termin_status or ""),
                                   dealer_id=appt.get("dealer_id"))
    except Exception:  # noqa: BLE001
        log.exception("KI-Lernfall: Ausgang von Termin %s nicht uebernommen", (appt or {}).get("id"))


async def termin_status_uebernehmen(appt: dict, termin_status: str, *,
                                    user: Optional[dict] = None) -> bool:
    """Terminstatus auf den Kaufvorgang des Termins abbilden (abgeholt,
    nicht abgeholt, storniert; alles andere = Abholung geplant). Liefert
    False, wenn der Termin keinen Kaufvorgang hat (manueller Termin ohne
    Vertrag) — dann darf der Aufrufer wie frueher direkt am Fahrzeug
    arbeiten. Phase 2: scheiterte die Fahrzeug-Zusammenfassung, traegt der
    Vorgang danach nacharbeit_offen (Aufrufer lesen ihn per fuer_termin)."""
    kv = None
    await _ki_lernfall_ausgang(appt, termin_status)
    neu = _TERMIN_ZU_STATUS.get(termin_status, "abholung_geplant")
    # Befund 105 (16.09.2026): verliert der Status-CAS (paralleler Termin-/
    # Fahrerabschluss), wird der Vorgang neu gelesen und der Wechsel erneut
    # versucht (bis 3x) — vorher meldete die Funktion trotzdem Erfolg.
    for _versuch in range(3):
        kv = await fuer_termin(appt)
        if not kv:
            return False
        neu = _TERMIN_ZU_STATUS.get(termin_status, "abholung_geplant")
        if kv.get("status") == "abgeholt" and neu == "abholung_geplant" and appt.get("id") \
                and await abholung_protokoll_belegt(appt["id"]):
            # Phase 2 (2.6, D15): Wieder-Oeffnen eines abgeholten Termins mit
            # unterschriebenem Protokoll — die Abholung ist belegt, der Vorgang
            # bleibt abgeholt (eine Korrektur-Version aendert Preis/Details,
            # nicht den Kauf). Vorher fiel der Vorgang auf "Abholung geplant".
            # Rollenprüfung 22.09.2026 (RP-075/174): auch waehrend einer
            # laufenden Korrektur (siehe abholung_protokoll_belegt).
            neu = "abgeholt"
        if kv.get("status") == neu:
            # Runde 18: Vorgang steht schon richtig, aber die Fahrzeug-
            # Zusammenfassung kann beim letzten Mal gescheitert sein (wird dort
            # abgefangen). Beim Wiederholen trotzdem abgleichen — sonst blieb das
            # Fahrzeug dauerhaft falsch.
            ziel = await fahrzeug_status_aggregieren(kv["vehicle_id"], kv["dealer_id"], user=user)
            await _nacharbeit_merken(kv, ziel)
            return True
        if await status_setzen(kv["id"], neu, user=user, von=kv.get("status")) is not None:
            return True
    # Dreimal verloren: Merker am Vorgang (der Nachholer greift), Betriebsalarm —
    # der Aufrufer bekommt True (der Termin HAT einen Vorgang), aber nichts
    # wird still als erledigt gemeldet.
    log.error("Kaufvorgang %s: Terminstatus %s -> %s nach 3 Versuchen nicht uebernommen",
              kv["id"], termin_status, neu)
    await db.kaufvorgaenge.update_one({"id": kv["id"]},
                                      {"$set": {"nacharbeit_offen": True, "updated_at": now_iso()}})
    try:
        import betrieb as _betrieb
        await _betrieb.alarm(db, "kaufvorgang_status_konflikt", ref=kv["id"],
                             termin=str(appt.get("id") or ""), ziel=neu)
    except Exception:  # noqa: BLE001
        pass
    return True


def _schritte(aktuell: str, ziel: str) -> list:
    """Welche Lifecycle-Schritte fuehren von `aktuell` zu `ziel`? Leer, wenn
    der Weg nicht in der Kaufkette liegt (Bestand, Verkauf, geloescht ...)."""
    if ziel in ("nicht_abgeholt", "storniert"):
        return [ziel] if aktuell in ("vertrag_erstellt", "gekauft", "abholung_geplant",
                                     "verglichen", "besichtigung", "verhandlung") else []
    if ziel not in _KETTE:
        return []
    zi = _KETTE.index(ziel)
    if aktuell == "nicht_abgeholt":
        start = _KETTE.index("abholung_geplant")
        return _KETTE[start:zi + 1] if zi >= start else []
    if aktuell == "storniert":
        return _KETTE[:zi + 1]
    if aktuell in ("gefunden", "besichtigung", "verhandlung"):
        return _KETTE[1:zi + 1]
    if aktuell in _KETTE:
        ai = _KETTE.index(aktuell)
        return _KETTE[ai + 1:zi + 1] if zi > ai else []
    return []


async def _abholung_zuruecknehmen(v: dict, vehicle_id: str, dealer_id: str, ziel: str, *,
                                  user: Optional[dict] = None) -> Optional[bool]:
    """Pruefbericht 20.09.2026 (V-12): Fahrzeug steht auf "abgeholt", aber kein
    Vorgang ist mehr abgeholt. Zustaendig nur, wenn der am Fahrzeug
    festgehaltene Vorgang existiert und inzwischen storniert / nicht abgeholt
    ist und kein Termin am Fahrzeug mehr "abgeholt"/"erledigt" steht.
    Sonst None (bewusst: ein Verweis auf einen fehlenden Vorgang laesst das
    Fahrzeug abgeholt — Runde 27, Test 03; ebenso ein wieder geoeffneter
    Termin ohne Protokoll). True/False = Rueckweg geschrieben / Stand verloren."""
    fest = v.get("abgeholt_kaufvorgang_id")
    if not fest:
        return None
    kv = await db.kaufvorgaenge.find_one(
        {"id": fest, "vehicle_id": vehicle_id, "dealer_id": dealer_id},
        {"_id": 0, "id": 1, "status": 1, "purchase_price": 1})
    if not kv or kv.get("status") not in ("storniert", "nicht_abgeholt"):
        return None
    if await db.appointments.count_documents(
            {"vehicle_id": vehicle_id, "dealer_id": dealer_id,
             "status": {"$in": ["abgeholt", "erledigt"]}}, limit=1):
        return None
    # Der Einkaufspreis faellt nur weg, wenn er aus DIESER Abholung stammt
    # (von Hand geaenderte Preise bleiben stehen).
    preis = v.get("purchase_price")
    preis_entfernen = preis is not None and kv.get("purchase_price") is not None \
        and preis == kv.get("purchase_price")
    return await abholung_zuruecknehmen(vehicle_id, dealer_id, ziel, kaufvorgang_id=fest,
                                        preis=preis, preis_entfernen=preis_entfernen,
                                        user=user)


async def fahrzeug_status_aggregieren(vehicle_id: str, dealer_id: str, *,
                                      user: Optional[dict] = None) -> Optional[str]:
    """Fahrzeug-Lebenszyklus aus ALLEN Kaufvorgaengen des Fahrzeugs ableiten:
      abgeholt (irgendein Vorgang)  > abholung_geplant > gekauft (Vertrag
      erstellt/gesendet) > nur beendete Vorgaenge: nicht_abgeholt bzw. storniert.
    Beim Abholen wird der realisierte Kaufpreis ans Fahrzeug geschrieben
    (juengster abgeholter Vorgang). Nie eine Exception nach aussen."""
    try:
        # Runde 23 (11.09.2026, Befund B): zwei parallele Abholabschluesse
        # verschiedener Vorgaenge lasen beide "kein massgeblicher Vorgang
        # festgelegt" und schrieben je ihren Vorgang samt Preis — der letzte
        # gewann und ueberschrieb den bereits festgehaltenen. Jetzt schreibt
        # der Fahrzeug-Write per Compare-and-Set (nur, wenn noch kein bzw.
        # derselbe oder ein nicht mehr abgeholter Vorgang festgehalten ist).
        # Scheitert er, wird EINMAL neu gelesen und neu entschieden.
        # Runde 23 (Gegenpruefung): der CAS verlangt zusaetzlich den GELESENEN
        # Fahrzeugpreis — sonst schrieb ein veralteter Lauf fuer DENSELBEN
        # Vorgang (Fahrer schliesst ab, gleichzeitig traegt der Chef den Vor-
        # Ort-Preis nach) den alten Preis zurueck (Fahrzeug X/20000, Vorgang X
        # 18000). Dafuer wird das Fahrzeug VOR den Vorgaengen gelesen: jeder
        # Lauf, der danach geschrieben hat, aendert Vorgang oder Preis am
        # Fahrzeug, und unser Write scheitert; wer vor unserem Lesen schrieb,
        # hat die Vorgaenge davor gelesen, wir sehen also mindestens deren Stand.
        umkaempft = False
        for versuch in (1, 2):
            v = await db.vehicles.find_one({"id": vehicle_id, "dealer_id": dealer_id},
                                           {"_id": 0, "lifecycle": 1, "abgeholt_kaufvorgang_id": 1,
                                            "purchase_price": 1})
            # Runde 29 (12.09.2026, Pruefbefund): Vorher wurden bis zu 500
            # Vorgaenge geladen und in Python gefiltert — bei mehr Vorgaengen
            # (viele Sucher am selben Auto) haette der entscheidende gefehlt.
            # Jetzt gezielte Abfragen: welche Stati gibt es, und welcher
            # abgeholte Vorgang ist massgeblich. Kein Deckel mehr.
            grund = {"vehicle_id": vehicle_id, "dealer_id": dealer_id}
            stati = set(await db.kaufvorgaenge.distinct("status", grund))
            if not stati:
                return ""          # nichts zu tun (kein Vorgang) — kein Fehler
            aktuell = (v or {}).get("lifecycle") or "verglichen"
            if v is None or "abgeholt" not in stati or aktuell in ABGESCHLOSSEN_FAHRZEUG:
                break
            # Runde 18: Der REALISIERTE Vorgang wird am Fahrzeug festgehalten
            # (abgeholt_kaufvorgang_id) und bleibt massgeblich, solange er
            # abgeholt ist. Ein spaeter abgeschlossener zweiter Vorgang
            # ueberschreibt weder ihn noch den Einkaufspreis — und ein bereits
            # verkauftes/archiviertes Fahrzeug wird gar nicht mehr angefasst
            # (vorher: Preis geschrieben, bevor der Lebenszyklus geprueft wurde
            # -> historische Marge verfaelscht).
            fest = v.get("abgeholt_kaufvorgang_id")
            felder = {"_id": 0, "id": 1, "status": 1, "purchase_price": 1,
                      "updated_at": 1}
            neueste = await db.kaufvorgaenge.find(
                {**grund, "status": "abgeholt"}, felder
            ).sort("updated_at", -1).limit(1).to_list(1)
            if not neueste:
                break
            abg = neueste[0]
            if fest and abg.get("id") != fest:
                # Der am Fahrzeug festgehaltene Vorgang bleibt massgeblich,
                # solange er abgeholt ist (Runde 18).
                gehalten = await db.kaufvorgaenge.find_one(
                    {**grund, "status": "abgeholt", "id": fest}, felder)
                if gehalten:
                    abg = gehalten
            setzen: Dict[str, Any] = {"abgeholt_kaufvorgang_id": abg.get("id")}
            if abg.get("purchase_price") is not None:
                setzen["purchase_price"] = abg["purchase_price"]
            # CAS: None trifft auch das fehlende Feld. Der alte Vorgang darf
            # nur abgeloest werden, wenn er nicht mehr abgeholt ist (sonst
            # waere abg == fest).
            erlaubt = [None, abg.get("id")]
            if fest and fest != abg.get("id"):
                erlaubt.append(fest)
            # purchase_price None trifft wie oben auch das fehlende Feld.
            res = await db.vehicles.update_one(
                {"id": vehicle_id, "dealer_id": dealer_id,
                 "lifecycle": {"$nin": list(ABGESCHLOSSEN_FAHRZEUG)},
                 "abgeholt_kaufvorgang_id": {"$in": erlaubt},
                 "purchase_price": v.get("purchase_price")},
                {"$set": setzen})
            if res.matched_count:
                break
            if versuch == 2:
                # Phase 2 (2.4, G6): zweimal verloren -> kein Erfolg melden; der
                # Merker am Vorgang laesst den Aufraeum-Job spaeter neu ansetzen.
                umkaempft = True
                log.warning("Massgeblicher Vorgang fuer %s nach Neulesen weiter "
                            "umkaempft — Fahrzeug bleibt beim festgehaltenen", vehicle_id)
        if "abgeholt" in stati:
            ziel = "abgeholt"
        elif "abholung_geplant" in stati:
            ziel = "abholung_geplant"
        elif stati & {"vertrag_erstellt", "gesendet"}:
            ziel = "gekauft"
        else:
            ziel = "nicht_abgeholt" if "nicht_abgeholt" in stati else "storniert"
        # Pruefbericht 20.09.2026 (V-12), Entscheidung Ahmad 21.09.2026: Der Chef
        # hat die Abholung nachtraeglich storniert / auf "nicht abgeholt" gesetzt.
        # _schritte kennt keinen Weg aus "abgeholt" — das Fahrzeug blieb samt
        # Verweis und Einkaufspreis stehen, und hier wurde trotzdem Erfolg
        # gemeldet. Jetzt ueber den streng geprueften Rueckweg; nicht zustaendig
        # (None) -> weiter wie bisher.
        if aktuell == "abgeholt" and "abgeholt" not in stati and v is not None:
            zurueck = await _abholung_zuruecknehmen(v, vehicle_id, dealer_id, ziel, user=user)
            if zurueck is not None:
                return ziel if zurueck else None
        schritt_fehlt = False
        for schritt in _schritte(aktuell, ziel):
            # Phase 2 (2.4, G5): ein uebersprungener Schritt (Stand-Pruefung
            # verloren, Uebergang nicht erlaubt) ist KEIN Erfolg mehr.
            if not await try_set_lifecycle(vehicle_id, dealer_id, schritt, user=user):
                schritt_fehlt = True
        if umkaempft or schritt_fehlt:
            return None
        return ziel
    except Exception:
        log.exception("Fahrzeugstatus fuer %s konnte nicht zusammengefasst werden", vehicle_id)
        return None


async def einkaufspreis_vorschlag(vehicle_id: str, dealer_id: str,
                                  vehicle: Optional[dict] = None, *,
                                  user_id: Optional[str] = None) -> Dict[str, Any]:
    """Befund Ahmad 10.09.2026: Im Inserat stand "Einkaufspreis 0 €", obwohl
    der Vertrag 23.000 € trug — vehicles.purchase_price wird seit dem Umbau
    erst beim Abholen gesetzt. Gewuenscht: der Vertragspreis des Suchers
    gilt, solange kein Fahrer vor Ort bzw. niemand von Hand einen anderen
    Preis eingetragen hat.

    Liefert {"preis", "quelle", "kaufvorgang_id"}:
      fahrzeug   — am Fahrzeug eingetragen (Abholung oder von Hand)
      abgeholt   — aus dem abgeholten Vorgang
      vertrag    — aus dem (juengsten offenen) Vertrag
      mehrdeutig — (nur firmenweit) offene Vertraege mehrerer Konten mit
                   verschiedenen Preisen, noch nichts abgeholt: kein Preis
                   (Rollenprüfung 22.09.2026, RP-057 b)
      keiner     — nichts bekannt

    Runde 23 (11.09.2026, Befund A): mit `user_id` (Sucher) zaehlen NUR
    dessen eigene Vorgaenge — vorher sah Sucher B in seiner Akte den
    Vertragspreis von Sucher A als "Einkaufspreis (aus dem Kaufvertrag)".
    Der Fahrzeugpreis gilt fuer ihn nur, wenn der abgeholte (massgebliche)
    Vorgang sein eigener ist, und dann mit dem Preis dieses Vorgangs.
    Ohne `user_id` (Chef, Weiterverkauf) unveraendert firmenweit."""
    if vehicle is None:
        vehicle = await db.vehicles.find_one({"id": vehicle_id, "dealer_id": dealer_id},
                                             {"_id": 0, "purchase_price": 1,
                                              "abgeholt_kaufvorgang_id": 1}) or {}
    filt: Dict[str, Any] = {"vehicle_id": vehicle_id, "dealer_id": dealer_id}
    if user_id is None:
        if vehicle.get("purchase_price") is not None:
            return {"preis": vehicle["purchase_price"], "quelle": "fahrzeug",
                    "kaufvorgang_id": vehicle.get("abgeholt_kaufvorgang_id")}
    else:
        filt["user_id"] = user_id
    # Runde 29 (12.09.2026, Pruefbefund): Vorher wurden bis zu 200 Vorgaenge
    # geladen und in Python sortiert. Jetzt gezielte Abfragen (jeweils der
    # juengste passende Vorgang), damit kein Deckel etwas verschluckt.
    felder = {"_id": 0, "id": 1, "status": 1, "purchase_price": 1, "updated_at": 1}

    async def _juengster(zusatz: Dict[str, Any]) -> Optional[dict]:
        treffer = await db.kaufvorgaenge.find(
            {**filt, "purchase_price": {"$ne": None}, **zusatz}, felder
        ).sort("updated_at", -1).limit(1).to_list(1)
        return treffer[0] if treffer else None

    if user_id is not None:
        fest = vehicle.get("abgeholt_kaufvorgang_id")
        eigen = await db.kaufvorgaenge.find_one(
            {**filt, "id": fest}, felder) if fest else None
        if eigen and eigen.get("purchase_price") is not None:
            return {"preis": eigen["purchase_price"], "quelle": "fahrzeug",
                    "kaufvorgang_id": fest}
    f = await _juengster({"status": "abgeholt"})
    if f:
        return {"preis": f["purchase_price"], "quelle": "abgeholt",
                "kaufvorgang_id": f["id"]}
    # Runde 29: NUR offene Vorgaenge duerfen den Vertragspreis stellen.
    # Vorher fiel der Code ohne offenen Vorgang auf ALLE zurueck — ein
    # stornierter oder nicht abgeholter Kauf erschien dann als aktueller
    # "Vertragspreis".
    offen_filter = {"status": {"$in": list(OFFEN)}}
    f = await _juengster(offen_filter)
    if f and user_id is None and await _vertragspreise_mehrdeutig(filt, offen_filter):
        # Rollenprüfung 22.09.2026 (RP-057 b): Firmenweit (Chef, Inserat) gab
        # es bei Doppel-Vertraegen mehrerer Sucher mit VERSCHIEDENEN Preisen
        # den zuletzt geaenderten Vorgang irgendeines Kontos — der Zufall
        # entschied ueber den Einkaufspreis im Inserat und in der Akte. Welcher
        # Kauf zustande kommt, zeigt erst die Abholung; bis dahin kein Preis.
        return {"preis": None, "quelle": "mehrdeutig", "kaufvorgang_id": None}
    if f:
        return {"preis": f["purchase_price"], "quelle": "vertrag",
                "kaufvorgang_id": f["id"]}
    return {"preis": None, "quelle": "keiner", "kaufvorgang_id": None}


async def _vertragspreise_mehrdeutig(filt: Dict[str, Any], zusatz: Dict[str, Any]) -> bool:
    """Rollenprüfung 22.09.2026 (RP-057 b): offene Vorgaenge mit Preis von
    MEHREREN Konten und VERSCHIEDENEN Preisen? Gleicher Preis oder nur ein
    Konto (dessen juengster Vorgang gilt wie bisher) ist nicht mehrdeutig.
    distinct statt Liste — kein Deckel (Runde 29)."""
    q = {**filt, "purchase_price": {"$ne": None}, **zusatz}
    preise = await db.kaufvorgaenge.distinct("purchase_price", q)
    if len(preise) < 2:
        return False
    return len(await db.kaufvorgaenge.distinct("user_id", q)) > 1


# Runde 23 (11.09.2026, Befund A): Felder am gemeinsamen Fahrzeug, die den
# realisierten Einkauf EINES Vorgangs (= eines Suchers) tragen.
EINKAUF_FELDER = ("purchase_price", "abgeholt_kaufvorgang_id")


async def einkauf_fuer_sucher_maskieren(user, fahrzeuge):
    """Runde 23 (11.09.2026, Befund A): Regel "Sucher sehen nur ihren eigenen
    Einkaufspreis". vehicles.purchase_price/abgeholt_kaufvorgang_id liegen am
    firmenweit gemeinsamen Fahrzeug und gingen ueber /bestand, /vehicles,
    /vehicles/{id} und die Akte an JEDEN Sucher des Fahrzeugs — auch den
    Preis aus dem Vertrag eines Kollegen bzw. einen vom Chef eingetragenen.
    Fuer Sucher bleiben die Felder nur, wenn der abgeholte Vorgang sein
    eigener ist (Preis dann aus diesem Vorgang); sonst werden sie entfernt.
    Chef: unveraendert. Nimmt ein Dokument oder eine Liste (in place)."""
    if (user or {}).get("role") != "sucher" or not fahrzeuge:
        return fahrzeuge
    items = [fahrzeuge] if isinstance(fahrzeuge, dict) else list(fahrzeuge)
    ids = list({v.get("abgeholt_kaufvorgang_id") for v in items
                if v.get("abgeholt_kaufvorgang_id")})
    eigene: Dict[str, Any] = {}
    if ids:
        async for kv in db.kaufvorgaenge.find(
                {"id": {"$in": ids}, "dealer_id": user["dealer_id"], "user_id": user["id"]},
                {"_id": 0, "id": 1, "purchase_price": 1}):
            eigene[kv["id"]] = kv.get("purchase_price")
    for v in items:
        kv_id = v.get("abgeholt_kaufvorgang_id")
        if kv_id in eigene and eigene[kv_id] is not None:
            v["purchase_price"] = eigene[kv_id]
            continue
        for feld in EINKAUF_FELDER:
            v.pop(feld, None)
    return fahrzeuge


async def termin_loesen(appointment_id: str) -> None:
    """Termin geloescht/abgehaengt: Vorgaenge, die auf ihn zeigen, verlieren
    den Verweis; ein geplanter Vorgang faellt auf 'vertrag_erstellt' zurueck."""
    async for kv in db.kaufvorgaenge.find({"appointment_id": appointment_id},
                                          {"_id": 0, "id": 1, "status": 1}):
        neu = "vertrag_erstellt" if kv.get("status") == "abholung_geplant" else kv.get("status")
        await status_setzen(kv["id"], neu, appointment_id=None)
