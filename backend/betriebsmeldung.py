# -*- coding: utf-8 -*-
"""Betriebsmeldungen per E-Mail (20.09.2026, Wunsch Ahmad).

Bis heute landete jeder Fehler NUR in der Datenbank: ein 500er in
`error_logs`, ein schwerer Vorgang zusaetzlich als Betriebsalarm. Sichtbar
war beides auf der Betriebs-Seite und in /api/ready — aber niemand erfuhr
davon. Ging Samstagnacht etwas kaputt, wusste es bis zum naechsten
Hinsehen keiner.

Zwei Meldungen, beide an BETRIEB_MELDUNG_AN:

  1. SOFORT — sobald ein NEUER Betriebsalarm entsteht. Selten, immer ernst
     (bezahlt ohne Zugang, Sicherung unvollstaendig, Vertrag ohne
     Datensatz, Datei nicht loeschbar). Mit Sammelfrist: entstehen zehn
     Alarme in derselben Minute, kommt EINE Mail mit allen zehn.
  2. TAGESBERICHT — einmal taeglich. Der ist ausdruecklich auch dann
     faellig, wenn alles in Ordnung ist: eine Plattform, die schweigt,
     ist von einer toten nicht zu unterscheiden. Bleibt die Mail aus,
     weiss Ahmad, dass etwas nicht stimmt.

Zwei Server mit je vier Prozessen — ohne Sperre kaeme jede Meldung
achtmal. Deshalb laeuft jede Runde unter einer Job-Sperre, und jeder
gemeldete Alarm bekommt `gemeldet_am`: scheitert der Versand, bleibt die
Markierung aus und die naechste Runde versucht es erneut.

Umgebung:
  BETRIEB_MELDUNG_AN            Empfaenger; LEER = alles aus (Standard)
  BETRIEB_MELDUNG_SOFORT_MIN    Sammelfrist in Minuten (Standard 10)
  BETRIEB_TAGESBERICHT_STUNDE   Stunde des Tagesberichts (Standard 8);
                                -1 schaltet nur den Tagesbericht ab
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger("autohandel.betriebsmeldung")

#: Hoechstens so viele Alarme stehen einzeln in einer Mail — der Rest wird
#: gezaehlt. Sonst wird die Mail bei einem Sturm unlesbar.
MAX_EINZELN = 25


def empfaenger() -> str:
    return os.environ.get("BETRIEB_MELDUNG_AN", "").strip()


def sammelfrist_minuten() -> int:
    try:
        return max(1, int(os.environ.get("BETRIEB_MELDUNG_SOFORT_MIN", "").strip() or 10))
    except ValueError:
        return 10


def bericht_stunde() -> int:
    """Stunde des Tagesberichts; -1 = aus."""
    try:
        wert = int(os.environ.get("BETRIEB_TAGESBERICHT_STUNDE", "").strip() or 8)
    except ValueError:
        return 8
    return wert if -1 <= wert <= 23 else 8


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _kurz(wert, laenge: int = 120) -> str:
    text = str(wert if wert is not None else "")
    return text if len(text) <= laenge else text[:laenge - 1] + "…"


def _zeitpunkt(iso) -> str:
    """ISO-Zeit -> "20.09.2026 13:40" in der Zeit des Servers.

    Der Rohwert (2026-09-20T11:40:37.754523+00:00) ist fuer einen Bericht
    unbrauchbar — abgeschnitten erst recht."""
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone().strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return _kurz(iso, 30)


# ------------------------------------------------------------ Alarm-Mail
def alarm_text(alarme: list, gesamt_offen: int) -> tuple:
    """(Betreff, Text) fuer die Sofortmeldung — rein, damit pruefbar."""
    n = len(alarme)
    if n == 1:
        betreff = f"AutoSchnell: Betriebsalarm — {alarme[0].get('typ')}"
    else:
        betreff = f"AutoSchnell: {n} neue Betriebsalarme"
    zeilen = [
        "Es " + ("ist ein neuer Betriebsalarm" if n == 1
                 else f"sind {n} neue Betriebsalarme") + " entstanden.",
        "",
    ]
    for a in alarme[:MAX_EINZELN]:
        zeilen.append(f"• {a.get('typ')}")
        if a.get("ref"):
            zeilen.append(f"    betrifft: {_kurz(a.get('ref'))}")
        for schluessel, wert in sorted((a.get("details") or {}).items()):
            zeilen.append(f"    {schluessel}: {_kurz(wert)}")
        if int(a.get("anzahl") or 1) > 1:
            zeilen.append(f"    bereits {a['anzahl']}x aufgetreten")
        zeilen.append(f"    seit: {_zeitpunkt(a.get('created_at'))}")
        zeilen.append("")
    if n > MAX_EINZELN:
        zeilen.append(f"… und {n - MAX_EINZELN} weitere.")
        zeilen.append("")
    zeilen += [
        f"Offene Alarme insgesamt: {gesamt_offen}",
        "",
        "Nachsehen und abhaken: Betrieb-Seite im Admin-Bereich.",
        "Diese Mail kommt nur bei NEUEN Alarmen — ein bereits gemeldeter",
        "Alarm meldet sich nicht noch einmal, auch wenn er oefter auftritt.",
    ]
    return betreff, "\n".join(zeilen)


async def _offene_unbemerkte(db, limit: int = 200) -> list:
    return await db.betriebsalarme.find(
        {"offen": True, "gemeldet_am": {"$exists": False}},
        {"_id": 0, "id": 1, "typ": 1, "ref": 1, "details": 1,
         "anzahl": 1, "created_at": 1},
        sort=[("created_at", 1)]).to_list(limit)


async def neue_alarme_melden(db) -> int:
    """Eine Mail fuer alle noch nicht gemeldeten offenen Alarme.

    Liefert die Zahl der gemeldeten Alarme (0 = nichts zu tun). Wirft nie —
    eine Betriebsmeldung darf den Betrieb nicht stoeren."""
    ziel = empfaenger()
    if not ziel:
        return 0
    try:
        alarme = await _offene_unbemerkte(db)
        if not alarme:
            return 0
        gesamt = await db.betriebsalarme.count_documents({"offen": True})
        betreff, text = alarm_text(alarme, gesamt)
        from email_service import send_email
        # Idempotenz-Schluessel aus den Alarm-IDs: ein Wiederholungsversuch
        # nach einem abgebrochenen Versand stellt nicht doppelt zu.
        import hashlib
        schluessel = "betriebsalarm-" + hashlib.sha256(
            ",".join(sorted(str(a.get("id")) for a in alarme)).encode()
        ).hexdigest()[:24]
        ok = await send_email(ziel, betreff, text, idempotency_key=schluessel)
        if not ok:
            log.error("[betriebsmeldung] Alarm-Mail an %s nicht zugestellt — "
                      "die Alarme bleiben unmarkiert und werden erneut versucht",
                      ziel)
            return 0
        await db.betriebsalarme.update_many(
            {"id": {"$in": [a.get("id") for a in alarme]}},
            {"$set": {"gemeldet_am": _jetzt().isoformat()}})
        log.info("[betriebsmeldung] %d neue Alarme an %s gemeldet",
                 len(alarme), ziel)
        return len(alarme)
    except Exception:  # noqa: BLE001
        log.exception("[betriebsmeldung] Alarm-Mail fehlgeschlagen")
        return 0


# --------------------------------------------------------- Tagesbericht
async def tagesbericht_daten(db) -> dict:
    """Die Zahlen des Berichts — getrennt vom Text, damit pruefbar."""
    seit = (_jetzt() - timedelta(days=1)).isoformat()
    daten = {"seit": seit}
    daten["alarme_offen"] = await db.betriebsalarme.count_documents({"offen": True})
    daten["alarme_neu"] = await db.betriebsalarme.count_documents(
        {"offen": True, "created_at": {"$gte": seit}})
    daten["fehler"] = await db.error_logs.count_documents(
        {"created_at": {"$gte": seit}})
    # Welche Wege haben Fehler gemacht? Das sagt mehr als eine blosse Zahl.
    daten["fehler_wege"] = [
        {"weg": z["_id"], "anzahl": z["n"]}
        async for z in db.error_logs.aggregate([
            {"$match": {"created_at": {"$gte": seit}}},
            {"$group": {"_id": "$path", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}}, {"$limit": 5}])]
    try:
        from backup_service import letztes_backup_info_global
        daten["backup"] = await letztes_backup_info_global(db)
    except Exception as exc:  # noqa: BLE001
        daten["backup"] = {"hinweis": f"nicht lesbar ({exc})"}
    daten["link_jobs_haengend"] = await db.link_jobs.count_documents(
        {"status": "queued",
         "created_at": {"$lt": _jetzt() - timedelta(minutes=15)}})
    return daten


def bericht_text(daten: dict, tag: str) -> tuple:
    """(Betreff, Text) des Tagesberichts — rein, damit pruefbar."""
    alarme = int(daten.get("alarme_offen") or 0)
    fehler = int(daten.get("fehler") or 0)
    b = daten.get("backup") or {}
    sicherung_ok = bool(b.get("vollstaendig")) and (b.get("alter_stunden") or 99) <= 26
    alles_gut = not alarme and not fehler and sicherung_ok \
        and not daten.get("link_jobs_haengend")

    kopf = "alles in Ordnung" if alles_gut else "mit Auffaelligkeiten"
    betreff = f"AutoSchnell Tagesbericht {tag} — {kopf}"

    zeilen = [f"Tagesbericht vom {tag} (letzte 24 Stunden)", ""]
    if alles_gut:
        zeilen += ["Alles in Ordnung. Keine Alarme, keine Fehler, "
                   "Sicherung aktuell.", ""]

    zeilen.append(f"Offene Betriebsalarme: {alarme}"
                  + (f" (davon {daten.get('alarme_neu')} neu)"
                     if daten.get("alarme_neu") else ""))
    zeilen.append(f"Fehler bei Nutzern:   {fehler}")
    for eintrag in (daten.get("fehler_wege") or []):
        zeilen.append(f"    {eintrag['anzahl']}x  {_kurz(eintrag['weg'], 60)}")

    if b.get("hinweis") and not b.get("vollstaendig"):
        zeilen.append(f"Sicherung:            {_kurz(b.get('hinweis'), 80)}")
    else:
        alter = b.get("alter_stunden")
        zeilen.append(
            "Sicherung:            "
            + (f"vor {alter:.0f} Stunden" if isinstance(alter, (int, float))
               else "unbekannt")
            + (", vollstaendig" if b.get("vollstaendig") else ", UNVOLLSTAENDIG")
            + (", Kopie auswaerts" if b.get("offsite") else ", OHNE Kopie auswaerts"))
        if b.get("stichtagsgenau") is False:
            zeilen.append("                      (nicht stichtagsgenau — "
                          "Replica Set pruefen)")
    if daten.get("link_jobs_haengend"):
        zeilen.append(f"Abrufe in der Warteschlange > 15 min: "
                      f"{daten['link_jobs_haengend']}")

    zeilen += [
        "",
        "Bleibt diese Mail einmal aus, stimmt etwas nicht — sie kommt auch",
        "dann, wenn nichts passiert ist.",
    ]
    return betreff, "\n".join(zeilen)


async def tagesbericht_senden(db, tag: str = "") -> bool:
    """Den Tagesbericht verschicken. Wirft nie."""
    ziel = empfaenger()
    if not ziel or bericht_stunde() < 0:
        return False
    try:
        tag = tag or datetime.now().strftime("%d.%m.%Y")
        betreff, text = bericht_text(await tagesbericht_daten(db), tag)
        from email_service import send_email
        ok = await send_email(ziel, betreff, text,
                              idempotency_key=f"tagesbericht-{tag}")
        if ok:
            log.info("[betriebsmeldung] Tagesbericht %s an %s", tag, ziel)
        else:
            log.error("[betriebsmeldung] Tagesbericht %s NICHT zugestellt", tag)
        return ok
    except Exception:  # noqa: BLE001
        log.exception("[betriebsmeldung] Tagesbericht fehlgeschlagen")
        return False


# ------------------------------------------------------------- Schleife
async def run_betriebsmeldung_forever(db) -> None:
    """Sofortmeldungen im Takt der Sammelfrist, Tagesbericht einmal taeglich.

    Beide unter einer Job-Sperre: zwei Server mit je vier Prozessen wuerden
    sonst achtmal dasselbe verschicken."""
    from job_lock import acquire, release
    await asyncio.sleep(45)          # Backend erst in Ruhe hochfahren lassen
    if not empfaenger():
        # NICHT einfach return: ein beendeter Hintergrundjob zaehlt in
        # /api/ready als FEHLER, und der Server flaege aus dem
        # Lastverteiler — nur weil niemand Meldungen haben will.
        # server.py startet diesen Dienst ohne Adresse gar nicht erst;
        # das hier ist das zweite Netz.
        log.info("[betriebsmeldung] BETRIEB_MELDUNG_AN ist leer — keine "
                 "Meldungen. Fehler bleiben nur auf der Betriebs-Seite "
                 "sichtbar.")
        while True:
            await asyncio.sleep(3600)
    log.info("[betriebsmeldung] aktiv: Alarme alle %d min, Tagesbericht %s",
             sammelfrist_minuten(),
             "aus" if bericht_stunde() < 0 else f"{bericht_stunde():02d}:00 Uhr")
    while True:
        takt = sammelfrist_minuten() * 60
        try:
            # Sofortmeldung: die Sperre laeuft mit dem Takt ab, damit nach
            # einem Ausfall der naechste Prozess uebernimmt.
            token = await acquire(db, "betriebsmeldung", ttl_seconds=takt)
            if token:
                try:
                    await neue_alarme_melden(db)
                finally:
                    await release(db, "betriebsmeldung", token=token)
        except Exception:  # noqa: BLE001
            log.exception("[betriebsmeldung] Runde fehlgeschlagen")
        # Tagesbericht: die Tagessperre allein entscheidet, dass er genau
        # EINMAL faellt — auf beiden Servern zusammen. Geprueft wird nur,
        # ob die Stunde schon da ist; wer zuerst die Sperre bekommt,
        # schickt. Kein Zeitfenster-Rechnen, das man falsch verstehen kann.
        try:
            jetzt = datetime.now()
            if bericht_stunde() >= 0 and jetzt.hour >= bericht_stunde():
                tag = jetzt.strftime("%Y-%m-%d")
                bericht_token = await acquire(db, f"tagesbericht-{tag}",
                                              ttl_seconds=20 * 3600)
                if bericht_token:
                    await tagesbericht_senden(db, jetzt.strftime("%d.%m.%Y"))
        except Exception:  # noqa: BLE001
            log.exception("[betriebsmeldung] Tagesbericht-Runde fehlgeschlagen")
        await asyncio.sleep(takt)
