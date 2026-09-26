# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026 — Sicherheit und Betrieb.

SV-01/S1  uvicorn nahm mit --forwarded-allow-ips='*' den linkesten Eintrag aus
          X-Forwarded-For — den, den der Aufrufer selbst schickt. Jede IP-Sperre
          (Anmeldung, Konto, Fahrer) war per Kopfzeile auszuhebeln.
SV-02/B4  Die Wartungs-Middleware las bei jedem Cache-Ablauf ohne Zeitlimit und
          ohne Single-Flight — ein DB-Aussetzer legte alle Anfragen lahm.
AL-02/U4  Alarme nach erstem Auftreten sortiert, auf 200 gekappt, ohne Zahlen.
AL-04/U5  Keine Obergrenze: ein Angriff auf viele Kontonummern erzeugte einen
          dauerhaft offenen Alarm je Kennung.
"""
import ast
import asyncio
import os
import re
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _dockerfile_trust() -> str:
    text = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    cmd = [z for z in text.splitlines() if z.startswith("CMD ")][-1]
    m = re.search(r'--forwarded-allow-ips=\\?"\$\{FORWARDED_ALLOW_IPS:-([^}]*)\}', cmd)
    assert m, f"forwarded-allow-ips nicht konfigurierbar: {cmd}"
    assert "'*'" not in cmd and '"*"' not in cmd
    return m.group(1)


def test_sv01_dockerfile_vertraut_nur_eigenen_netzen():
    standard = _dockerfile_trust()
    assert "*" not in standard
    for netz in ("127.0.0.1", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
        assert netz in standard


def test_sv01_gefaelschte_adresse_bleibt_wirkungslos():
    """Nachgestellt mit dem installierten uvicorn: Kette so, wie sie hinter
    Cloudflare -> LB -> nginx ankommt ("<gefaelscht>, <echt>, <CF>, <echt>")."""
    from uvicorn.middleware.proxy_headers import _TrustedHosts
    kette = "1.2.3.4, 85.1.2.3, 162.158.1.1, 85.1.2.3"
    alt = _TrustedHosts("*").get_trusted_client_address(kette)[0]
    neu = _TrustedHosts(_dockerfile_trust()).get_trusted_client_address(kette)[0]
    assert alt == "1.2.3.4", "Nachweis des alten Fehlers"
    assert neu == "85.1.2.3", "die echte Adresse muss gewinnen"


def test_sv02_wartungsabfrage_mit_zeitlimit_und_einmal_je_takt():
    baum = ast.parse((BACKEND / "server.py").read_text(encoding="utf-8"))
    klasse = next(k for k in ast.walk(baum)
                  if isinstance(k, ast.ClassDef) and k.name == "WartungsmodusMiddleware")
    quelle = ast.unparse(klasse)
    assert "asyncio.wait_for(wartung.lesen_async(db)" in quelle
    # zuerst den naechsten Zeitpunkt setzen, dann lesen (Single-Flight)
    assert quelle.index("self._stand['bis'] = _t.monotonic() + 5") < quelle.index("wait_for(")


def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    return AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)[
        os.environ.get("DB_NAME") or "autoschnell"]


def test_al04_alarme_je_typ_gedeckelt():
    import betrieb
    typ = f"test_flut_{uuid.uuid4().hex[:8]}"

    async def lauf():
        db = _db()
        try:
            for i in range(betrieb.ALARM_JE_TYP_MAX + 25):
                await betrieb.alarm(db, typ, ref=f"konto-{i}", versuch=i)
            einzeln = await db.betriebsalarme.count_documents(
                {"typ": typ, "offen": True, "ref": {"$ne": betrieb.SAMMEL_REF}})
            sammel = await db.betriebsalarme.find_one({"typ": typ, "ref": betrieb.SAMMEL_REF})
            return einzeln, sammel
        finally:
            await db.betriebsalarme.delete_many({"typ": typ})

    einzeln, sammel = asyncio.run(lauf())
    assert einzeln == betrieb.ALARM_JE_TYP_MAX
    assert sammel and sammel["anzahl"] == 25 and sammel["offen"] is True


def test_al02_sortierung_nach_zuletzt_und_uebersicht():
    import betrieb
    typ_alt = f"test_alt_{uuid.uuid4().hex[:6]}"
    typ_neu = f"test_neu_{uuid.uuid4().hex[:6]}"

    async def lauf():
        db = _db()
        try:
            await betrieb.alarm(db, typ_alt, ref="x")
            await betrieb.alarm(db, typ_neu, ref="y")
            await betrieb.alarm(db, typ_alt, ref="x")          # tritt erneut auf
            liste = [a["typ"] for a in await betrieb.offene_alarme(db, limit=500)
                     if a["typ"] in (typ_alt, typ_neu)]
            ueb = await betrieb.alarm_uebersicht(db)
            return liste, ueb
        finally:
            await db.betriebsalarme.delete_many({"typ": {"$in": [typ_alt, typ_neu]}})

    liste, ueb = asyncio.run(lauf())
    assert liste[0] == typ_alt, "der zuletzt wieder aufgetretene Alarm gehoert nach oben"
    je = {t["typ"]: t for t in ueb["je_typ"]}
    assert je[typ_alt]["vorkommen"] == 2 and je[typ_neu]["eintraege"] == 1


def test_al04_geschlossene_alarme_verfallen():
    import betrieb
    typ = f"test_verfall_{uuid.uuid4().hex[:6]}"

    async def lauf():
        db = _db()
        try:
            await betrieb.alarm(db, typ, ref="z")
            await betrieb.alarm_schliessen(db, typ, ref="z")
            return await db.betriebsalarme.find_one({"typ": typ})
        finally:
            await db.betriebsalarme.delete_many({"typ": typ})

    doc = asyncio.run(lauf())
    assert doc["offen"] is False and doc.get("loeschen_ab") is not None
    quelle = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert 'create_index("loeschen_ab", expireAfterSeconds=0' in quelle
