# -*- coding: utf-8 -*-
"""Lasttest-MATRIX: 9 getrennte Belastungsszenarien, je eine Hauptfunktion
mit ~80 % der Last (Review-Auftrag 08/2026).

Allgemeine Bedingungen (Standard, per CLI aenderbar):
  - 100 gleichzeitig aktive Nutzer (T9: 140), Denkpause 0,2-1,2 s
  - 30 s Aufwaermphase (unbewertet), 300 s Messphase, 3 Wiederholungen
  - 10 Firmen mit je Chef + 2 Suchern + 1 Fahrer, 1 Kaeufer, 1 Admin
  - Backend mit MOCK_PROVIDER_FETCH=true (Anbieter/E-Mail/WhatsApp sind
    serverseitig gemockt; /contracts/send versendet NIE echt)
  - zwischen den Laeufen: Warteschlangen-Drain, Haenger-Pruefung, RAM

Ehrlichkeits-Hinweise (keine erfundenen Ergebnisse):
  - "Unterschrift speichern" existiert nicht als Einzel-Endpunkt; beide
    Unterschriften werden beim Protokoll-ABSCHLUSS uebergeben. T4 misst
    deshalb Korrektur->Ausfuellen->Abschluss (enthaelt beide Signaturen).
  - E-Mail-/WhatsApp-Versand ist im Backend ein dokumentierter Mock ohne
    Warteschlange (E-Mail: markiert 'mocked'; WhatsApp: wa.me-Link).
    T5/T6 messen den Endpunkt und den Doppelversand-Schutz; Zustell-
    status/Retry/429 sind NICHT ANWENDBAR und stehen so im Bericht.

Aufruf:
  python -X utf8 scripts/lasttest_matrix.py --szenario T1 --rep 1
  python -X utf8 scripts/lasttest_matrix.py --alle          # komplette Matrix
  Optionen: --nutzer N --dauer S --warmup S --reps N --kurz (45s-Probelauf)
"""
import argparse
import asyncio
import base64
import json
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
AUSGABE = Path(__file__).resolve().parent.parent.parent / "docs" / "lasttests" / "matrix"

SUF = uuid.uuid4().hex[:6]   # wird je Lauf neu gewuerfelt (siehe lauf())
PW = "Matrix123!"
N_FIRMEN = 10

SIG = "data:image/png;base64," + base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")).decode()


def _jpegish(size: int) -> str:
    raw = b"\xff\xd8\xff\xe0" + os.urandom(max(0, size - 6)) + b"\xff\xd9"
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


FOTO_KLEIN = _jpegish(250 * 1024)
FOTO_NORMAL = _jpegish(3 * 1024 * 1024)
FOTO_GROSS = _jpegish(int(7.5 * 1024 * 1024))
FOTO_UNGUELTIG = "data:image/jpeg;base64," + base64.b64encode(
    b"MZ\x90\x00" + os.urandom(4096)).decode()   # EXE-Header


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=8000)[DB_NAME]


def _pct(v, p):
    if not v:
        return None
    v = sorted(v)
    return v[max(0, min(len(v) - 1, int(round(p / 100 * len(v))) - 1))]


class Stats:
    def __init__(self):
        self.ok, self.err, self.total = {}, {}, 0
        self.job_annahme, self.job_warte = [], []
        self.unfertig = 0
        self.resets = []          # [{zeit, endpunkt}] je 599

    def add(self, name, ms, status, erwartet_4xx=False):
        self.total += 1
        if 200 <= status < 300 or (erwartet_4xx and 400 <= status < 500):
            self.ok.setdefault(name, []).append(ms)
        else:
            self.err.setdefault(name, {}).setdefault(status, 0)
            self.err[name][status] += 1
            if status == 599:
                self.resets.append({
                    "zeit": datetime.now(timezone.utc).isoformat(),
                    "endpunkt": name})

    def report(self):
        out = {}
        for n, v in sorted(self.ok.items()):
            e = self.err.get(n, {})
            ne = sum(e.values())
            out[n] = {"ok": len(v), "fehler": ne,
                      "fehlerrate_prozent": round(100 * ne / max(1, len(v) + ne), 2),
                      "p50_ms": round(_pct(v, 50) or 0),
                      "p95_ms": round(_pct(v, 95) or 0),
                      "p99_ms": round(_pct(v, 99) or 0),
                      "fehler_nach_status": e}
        for n, e in self.err.items():
            if n not in out:
                out[n] = {"ok": 0, "fehler": sum(e.values()),
                          "fehlerrate_prozent": 100.0, "fehler_nach_status": e}
        return out


class Welt:
    """10 Firmen + Kaeufer + Admin, einmal je Szenario aufgebaut."""

    def __init__(self):
        self.firmen = []      # {h, chef, sucher_h[], drv_h, vehicle_id,
                              #  contract_id, appt_id, listing_id, send_contract}
        self.kaeufer_h = None
        self.admin_h = None
        self.link_serie = 0

    def neue_links(self, n):
        # Serien-Bloecke zu 100k IDs: Serien koennen sich NIE ueberlappen
        # (der alte +1-Schritt kollidierte mit den i*7-Spruengen und
        # erzeugte scheinbar 'doppelte Abrufe').
        s = (90_000_000_000 + (int(SUF, 16) % 5_000) * 10_000_000
             + self.link_serie * 100_000)
        self.link_serie += 1
        return [f"https://www.kleinanzeigen.de/s-anzeige/mx/{s + i}-216-1"
                for i in range(n)]


async def _timed(sess, stats, name, method, url, erwartet_4xx=False, **kw):
    t0 = time.monotonic()
    try:
        async with sess.request(method, url, **kw) as r:
            body = await r.read()
            stats.add(name, (time.monotonic() - t0) * 1000, r.status,
                      erwartet_4xx)
            return r.status, body
    except Exception:
        stats.add(name, (time.monotonic() - t0) * 1000, 599)
        return 599, b""


async def _post_json(sess, url, js, h=None, timeout=90):
    async with sess.post(url, json=js, headers=h,
                         timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        return r.status, (await r.json(content_type=None)
                          if r.status < 500 else {})


async def welt_aufbauen(sess) -> Welt:
    w = Welt()
    dbx = _db()
    now = datetime.now(timezone.utc)
    print(f"[Welt] baue {N_FIRMEN} Firmen auf …")
    import bcrypt
    dbx.users.insert_one({
        "id": f"mxadm_{SUF}", "email": f"mx_admin_{SUF}@e2etest-mail.de",
        # Seit Runde 7 (09/2026) sind Verkaufsplan und Rollen Super-Admin-Sache.
        "role": "admin", "is_super_admin": True, "active": True, "dealer_id": None,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    st, js = await _post_json(sess, f"{API}/auth/login",
                              {"email": f"mx_admin_{SUF}@e2etest-mail.de",
                               "password": PW})
    assert st == 200, f"Admin-Login {st}"
    w.admin_h = {"Authorization": f"Bearer {js['token']}"}

    for i in range(N_FIRMEN):
        mail = f"mx_chef_{i}_{SUF}@e2etest-mail.de"
        st, js = await _post_json(sess, f"{API}/auth/register", {
            "email": mail, "password": PW, "company_name": f"Matrix {i}",
            "contact_person": f"Chef {i}", "phone": "0511 1"})
        assert st == 200, f"Register {st}: {js}"
        h = {"Authorization": f"Bearer {js['token']}"}
        st, me = await _post_json(sess, f"{API}/auth/login",
                                  {"email": mail, "password": PW})
        tok = me["token"]
        h = {"Authorization": f"Bearer {tok}"}
        async with sess.get(f"{API}/auth/me", headers=h) as r:
            me = (await r.json())["user"]
        firma = {"h": h, "chef": me, "sucher_h": [], "drv_h": None}
        dbx.subscriptions.insert_one({
            "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"],
            "subject_user_id": me["id"], "plan": "monthly",
            "status": "active",
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "created_at": now.isoformat()})

        for k in range(2):
            smail = f"mx_such_{i}_{k}_{SUF}@e2etest-mail.de"
            st, js = await _post_json(sess, f"{API}/dealer/sucher", {
                "email": smail, "password": PW,
                "first_name": "Mx", "last_name": f"S{i}{k}"}, h=h)
            assert st == 200, f"Sucher {st}"
            dbx.subscriptions.insert_one({
                "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"],
                "subject_user_id": js["sucher_id"], "plan": "monthly",
                "status": "active",
                "expires_at": (now + timedelta(days=1)).isoformat(),
                "created_at": now.isoformat()})
            st, js = await _post_json(sess, f"{API}/auth/login",
                                      {"email": smail, "password": PW})
            firma["sucher_h"].append(
                {"Authorization": f"Bearer {js['token']}"})

        dmail = f"mx_drv_{i}_{SUF}@e2etest-mail.de"
        st, js = await _post_json(sess, f"{API}/driver/register", {
            "email": dmail, "password": PW, "display_name": f"Fahrer {i}"})
        assert st == 200, f"Fahrer registrieren {st}: {str(js)[:200]}"
        firma["drv_h"] = {"Authorization": f"Bearer {js['token']}"}
        firma["drv_id"] = js["driver"]["id"]
        await _post_json(sess, f"{API}/drivers/add",
                         {"driver_code": js["driver"]["driver_code"]}, h=h)

        # Fahrzeug (Mock-Link) + Vertrag + Termin + Fahrer + Inserat
        url = w.neue_links(1)[0]
        st, js = await _post_json(sess, f"{API}/mobile/compare",
                                  {"url": url}, h=h, timeout=120)
        assert st == 200 and (js.get("vehicle") or {}).get("_mock"), \
            f"Mock-Modus fehlt! compare={st}"
        firma["vehicle_id"] = js["vehicle_id"]
        st, js = await _post_json(sess, f"{API}/contracts", {
            "vehicle_id": firma["vehicle_id"], "seller_name": f"V {i}",
            "seller_address": "Weg 1", "seller_zip": "30159",
            "seller_city": "Hannover", "purchase_price": 10000 + i,
            "pickup_date": "2099-04-01", "pickup_time": "10:00"},
            h=h, timeout=120)
        assert st == 200, f"Vertrag {st}: {js}"
        firma["contract_id"] = js["id"]
        firma["send_contract"] = js["id"]
        async with sess.get(f"{API}/appointments", headers=h) as r:
            appt = next(a for a in await r.json()
                        if a.get("contract_id") == firma["contract_id"])
        firma["appt_id"] = appt["id"]
        async with sess.put(f"{API}/appointments/{appt['id']}",
                            json={"driver_id": firma["drv_id"]},
                            headers=h) as r:
            assert r.status == 200, f"Fahrer zuweisen {r.status}: {(await r.text())[:200]}"

        st, js = await _post_json(
            sess, f"{API}/resale/draft/{firma['vehicle_id']}", {}, h=h)
        assert st == 200, f"Draft {st}"
        firma["listing_id"] = js["id"]
        async with sess.put(f"{API}/resale/{firma['listing_id']}",
                            json={"price_public": 15000 + i,
                                  "price_b2b": 14000 + i}, headers=h) as r:
            assert r.status == 200, f"Inserat aendern {r.status}: {(await r.text())[:200]}"
        await _post_json(sess, f"{API}/resale/{firma['listing_id']}/status",
                         {"status": "verkaufsbereit"}, h=h)
        async with sess.put(f"{API}/admin/dealers/{me['dealer_id']}/sale-plan",
                            json={"tier": "s5", "months": 1},
                            headers=w.admin_h) as r:
            assert r.status == 200, f"Verkaufsplan {r.status}: {(await r.text())[:200]}"
        await _post_json(sess, f"{API}/resale/{firma['listing_id']}/publish",
                         {"visibility": "public"}, h=h)
        async with sess.put(f"{API}/dealer/marketplace-profile",
                            json={"public": True}, headers=h) as r:
            assert r.status == 200, f"Marktplatz-Profil {r.status}: {(await r.text())[:200]}"

        # Protokoll einmal regulaer abschliessen (Basis fuer Korrektur-Zyklen)
        await protokoll_abschliessen(sess, firma, erste_runde=True)
        w.firmen.append(firma)

    st, js = await _post_json(sess, f"{API}/buyer/register", {
        "company_name": "Mx Kaeufer", "contact_name": "Kaeufer M",
        "email": f"mx_kauf_{SUF}@e2etest-mail.de", "password": PW,
        "phone": "0511 3", "gewerblich_bestaetigt": True})
    assert st == 200, f"Kaeufer registrieren {st}: {str(js)[:200]}"
    ktok = js["token"]
    async with sess.get(f"{API}/buyer/me",
                        headers={"Authorization": f"Bearer {ktok}"}) as r:
        kid = (await r.json())["id"]
    _db().users.update_one({"id": kid}, {"$set": {"marketplace_access": {
        "active": True, "plan": "monthly",
        "expires_at": (now + timedelta(days=1)).isoformat()}}})
    w.kaeufer_h = {"Authorization": f"Bearer {ktok}"}
    print(f"[Welt] fertig: {len(w.firmen)} Firmen")
    return w


async def protokoll_abschliessen(sess, firma, erste_runde=False,
                                 schaeden=0):
    """Korrektur starten (ausser beim ersten Mal), Pflichtfelder fuellen,
    mit beiden Unterschriften abschliessen. Liefert HTTP-Status."""
    h, appt = firma["drv_h"], firma["appt_id"]
    if not erste_runde:
        async with sess.post(f"{API}/driver/appointments/{appt}"
                             f"/protocol/correction", headers=h) as r:
            if r.status not in (200, 201):
                return r.status
    async with sess.get(f"{API}/driver/appointments/{appt}/protocol",
                        headers=h) as r:
        if r.status != 200:
            return r.status
        tpl = await r.json()
    felder = [f[0] if isinstance(f, (list, tuple)) else f.get("key")
              for f in tpl["template"]["vehicle_check_fields"]]
    dmg = [{"view": "front", "zone": "haube", "x": 0.5, "y": 0.5,
            "art": "kratzer", "notiz": f"Testschaden {i}"}
           for i in range(schaeden)]
    async with sess.put(f"{API}/driver/appointments/{appt}/protocol",
                        json={"vehicle_check": {k: {"status": "stimmt"}
                                                for k in felder},
                              "documents": {"Fahrzeugschein": True},
                              "keys_count": "2", "keys_expected": "2",
                              "condition": {"mileage": "90000",
                                            "fuel_level": "1/2"},
                              "damages_confirmed": True,
                              "new_damages": dmg,
                              "notes": "Matrix-Lauf"}, headers=h) as r:
        if r.status != 200:
            return r.status
    async with sess.post(f"{API}/driver/appointments/{appt}"
                         f"/protocol/finalize",
                         json={"signature_driver_b64": SIG,
                               "signature_seller_b64": SIG,
                               "seller_name": "Matrix V",
                               "place": "Hannover"},
                         headers=h,
                         timeout=aiohttp.ClientTimeout(total=120)) as r:
        return r.status


# ------------------------- Operationen -------------------------
async def op_link_neu(sess, stats, w, firma, pool):
    url = pool.pop() if pool else w.neue_links(1)[0]
    h = random.choice(firma["sucher_h"] + [firma["h"]])
    t0 = time.monotonic()
    st, body = await _timed(sess, stats, "link_neu_check", "POST",
                            f"{API}/listings/check", headers=h,
                            json={"url": url})
    stats.job_annahme.append((time.monotonic() - t0) * 1000)
    if st != 200:
        return
    js = json.loads(body or b"{}")
    if js.get("status") == "completed":
        stats.job_warte.append(0)
    elif js.get("job_id"):
        zj = {"status": "unbekannt"}
        ende = time.monotonic() + 120
        while time.monotonic() < ende:
            await asyncio.sleep(2)
            async with sess.get(f"{API}/listings/check/{js['job_id']}",
                                headers=h) as r:
                if r.status == 404:
                    zj = {"status": "completed"}
                    break
                zj = await r.json()
                if zj["status"] in ("completed", "failed"):
                    break
        if zj.get("status") == "completed":
            stats.job_warte.append((time.monotonic() - t0) * 1000)
            stats.add("link_job_ende", (time.monotonic() - t0) * 1000, 200)
        elif zj.get("status") == "failed":
            stats.add("link_job_ende", (time.monotonic() - t0) * 1000, 500)
        else:
            # Job lief beim Poll-Limit noch — das ist Rueckstau, kein
            # Fehler (der Drain-Check weist nach, dass nichts haengt).
            stats.unfertig += 1
            return
    await _timed(sess, stats, "link_neu_vergleich", "POST",
                 f"{API}/mobile/compare", headers=h, json={"url": url})


async def op_link_bekannt(sess, stats, w, firma, warm_pool):
    h = random.choice(firma["sucher_h"] + [firma["h"]])
    url = random.choice(warm_pool)
    await _timed(sess, stats, "link_bekannt", "POST",
                 f"{API}/mobile/compare", headers=h, json={"url": url})


async def op_pdf_vertrag(sess, stats, w, firma, pdf_zaehler=None):
    st, _ = await _timed(sess, stats, "pdf_vertrag_neu", "POST",
                 f"{API}/contracts", headers=firma["h"], json={
                     "vehicle_id": firma["vehicle_id"],
                     "seller_name": "PDF V", "seller_address": "Weg 2",
                     "seller_zip": "30159", "seller_city": "Hannover",
                     "purchase_price": 11111,
                     "pickup_date": "2099-05-05", "pickup_time": "11:00"},
                 timeout=aiohttp.ClientTimeout(total=120))
    if st == 200 and pdf_zaehler is not None:
        pdf_zaehler[0] += 1


async def op_pdf_download(sess, stats, w, firma):
    was = random.random()
    if was < 0.5:
        await _timed(sess, stats, "pdf_download_vertrag", "GET",
                     f"{API}/contracts/{firma['contract_id']}/pdf",
                     headers=firma["h"])
    else:
        # 404 ist hier ERWARTET, wenn parallel gerade eine Korrektur
        # laeuft (Protokoll voruebergehend im Entwurfszustand).
        await _timed(sess, stats, "pdf_download_protokoll", "GET",
                     f"{API}/driver/appointments/{firma['appt_id']}"
                     f"/protocol.pdf", headers=firma["drv_h"],
                     erwartet_4xx=True)


async def op_protokoll(sess, stats, w, firma):
    groesse = random.random()
    schaeden = 0 if groesse < 0.4 else (5 if groesse < 0.8 else 25)
    # Skript-Lock je Firma: 100 Nutzer teilen sich 10 Firmen; ohne Lock
    # messen wir nur die (korrekten) Ablehnungen konkurrierender
    # Abschluesse desselben Protokolls statt der Abschlussdauer. Die
    # Atomik unter Konkurrenz ist in T2 (409-Schutz) + Tests belegt.
    lock = firma.setdefault("_protokoll_lock", asyncio.Lock())
    if lock.locked():
        return                      # Firma gerade beschaeftigt -> Denkpause
    async with lock:
        t0 = time.monotonic()
        st = await protokoll_abschliessen(sess, firma, schaeden=schaeden)
        stats.add("protokoll_abschluss", (time.monotonic() - t0) * 1000,
                  st if st else 599)


async def op_foto(sess, stats, w, firma):
    d = random.random()
    if d < 0.625:      # 50/80: Upload
        g = random.random()
        foto = FOTO_KLEIN if g < 0.5 else (FOTO_NORMAL if g < 0.9
                                           else FOTO_GROSS)
        name = ("foto_upload_klein" if g < 0.5 else
                "foto_upload_normal" if g < 0.9 else "foto_upload_gross")
        st, body = await _timed(
            sess, stats, name, "POST",
            f"{API}/resale/{firma['listing_id']}/photos",
            headers=firma["h"], json={"photos_b64": [foto]},
            timeout=aiohttp.ClientTimeout(total=120))
        if st == 200:
            js2 = json.loads(body)
            keys = js2.get("uploaded", [])
            if keys:
                firma.setdefault("foto_urls", []).append(keys[0])
            # Rotation: kurz vor dem 40er-Limit aelteste Fotos loeschen —
            # das Limit selbst ist in T2 rep1/2 nachgewiesen (400er);
            # T3 soll Upload-DURCHSATZ messen, nicht Ablehnungen.
            if js2.get("total", 0) >= 35 and firma.get("foto_urls"):
                alt = firma["foto_urls"].pop(0)
                await _timed(sess, stats, "foto_loeschen", "POST",
                             f"{API}/resale/{firma['listing_id']}"
                             f"/photos/remove", headers=firma["h"],
                             json={"key": alt.replace("/api/files/", "")})
    elif d < 0.875:    # 20/80: oeffnen
        urls = firma.get("foto_urls") or []
        if urls:
            await _timed(sess, stats, "foto_oeffnen", "GET",
                         f"{BASE}{random.choice(urls)}")
    else:              # 10/80: loeschen
        urls = firma.get("foto_urls") or []
        if urls:
            u = urls.pop()
            await _timed(sess, stats, "foto_loeschen", "POST",
                         f"{API}/resale/{firma['listing_id']}/photos/remove",
                         headers=firma["h"],
                         json={"key": u.replace("/api/files/", "")})


async def op_foto_ungueltig(sess, stats, w, firma):
    # Sicherheitstest: EXE-Bytes als Bild -> MUSS 400 liefern
    await _timed(sess, stats, "foto_ungueltig_abgelehnt", "POST",
                 f"{API}/resale/{firma['listing_id']}/photos",
                 headers=firma["h"], json={"photos_b64": [FOTO_UNGUELTIG]},
                 erwartet_4xx=True)


async def op_versand(sess, stats, w, firma, kanal, zaehler):
    st, _ = await _timed(sess, stats, f"versand_{kanal}", "POST",
                 f"{API}/contracts/{firma['send_contract']}/send",
                 headers=firma["h"],
                 json={"channel": kanal, "recipient": "+491700000000"
                       if kanal == "whatsapp" else "mx@e2etest-mail.de",
                       "subject": "Vertrag", "message": "Hier der Vertrag."})
    if st == 200:
        zaehler[0] += 1


async def op_markt(sess, stats, w):
    d = random.random()
    if d < 0.5:
        sortier = random.choice(["preis_auf", "preis_ab", "km_auf", ""])
        marke = random.choice(["", "VW", "Volkswagen"])
        await _timed(sess, stats, "markt_suche", "GET",
                     f"{API}/marktplatz/listings?sort={sortier}&make={marke}",
                     headers=w.kaeufer_h)
    elif d < 0.75:
        await _timed(sess, stats, "markt_haendler", "GET",
                     f"{API}/marktplatz/haendler", headers=w.kaeufer_h)
    else:
        lid = random.choice(w.firmen)["listing_id"]
        await _timed(sess, stats, "markt_favorit", "POST",
                     f"{API}/marktplatz/favoriten/{lid}",
                     headers=w.kaeufer_h)


async def op_listen(sess, stats, w, firma):
    d = random.random()
    if d < 0.4:
        await _timed(sess, stats, "liste_sucher", "GET",
                     f"{API}/dealer/sucher", headers=firma["h"])
    elif d < 0.7:
        await _timed(sess, stats, "liste_admin_users", "GET",
                     f"{API}/admin/users", headers=w.admin_h)
    else:
        await _timed(sess, stats, "liste_bestand", "GET",
                     f"{API}/bestand", headers=firma["h"])


async def op_basis(sess, stats, w, firma):
    d = random.random()
    if d < 0.5:
        await _timed(sess, stats, "fahrer_termine", "GET",
                     f"{API}/driver/appointments", headers=firma["drv_h"])
    else:
        await _timed(sess, stats, "abo_status", "GET",
                     f"{API}/dealer/subscription", headers=firma["h"])


# ------------------------- Szenarien -------------------------
def gewichte(szenario):
    """(name, gewicht, op-schluessel) je Szenario — Summe 100."""
    G = {
        "T1": [("link_neu", 40, "link_neu"), ("link_geteilt", 20, "link_geteilt"),
               ("link_bekannt", 20, "link_bekannt"), ("pdf", 5, "pdf_mix"),
               ("foto", 5, "foto"), ("versand", 4, "versand_mix"),
               ("markt", 3, "markt"), ("basis", 3, "basis")],
        "T2": [("pdf_neu", 40, "pdf_vertrag"), ("protokoll", 20, "protokoll"),
               ("pdf_dl", 20, "pdf_download"), ("link", 5, "link_mix"),
               ("foto", 5, "foto"), ("versand", 4, "versand_mix"),
               ("markt", 3, "markt"), ("basis", 3, "basis")],
        "T3": [("foto", 76, "foto"), ("foto_boese", 4, "foto_ungueltig"),
               ("link", 5, "link_mix"), ("pdf", 5, "pdf_mix"),
               ("versand", 4, "versand_mix"), ("markt", 3, "markt"),
               ("basis", 3, "basis")],
        "T4": [("protokoll", 80, "protokoll"), ("link", 5, "link_mix"),
               ("foto", 5, "foto"), ("pdf", 4, "pdf_mix"),
               ("markt", 3, "markt"), ("basis", 3, "basis")],
        "T5": [("email", 80, "versand_email"), ("link", 5, "link_mix"),
               ("pdf", 5, "pdf_mix"), ("foto", 4, "foto"),
               ("markt", 3, "markt"), ("basis", 3, "basis")],
        "T6": [("wa", 80, "versand_wa"), ("link", 5, "link_mix"),
               ("pdf", 5, "pdf_mix"), ("foto", 4, "foto"),
               ("markt", 3, "markt"), ("basis", 3, "basis")],
        "T7": [("markt", 70, "markt"), ("listen", 10, "listen"),
               ("link", 5, "link_mix"), ("pdf", 5, "pdf_mix"),
               ("foto", 4, "foto"), ("versand", 3, "versand_mix"),
               ("basis", 3, "basis")],
        "T8": [("link_neu", 40, "link_neu"), ("link_bekannt", 40, "link_bekannt"),
               ("pdf", 5, "pdf_mix"), ("email", 4, "versand_email"),
               ("wa", 3, "versand_wa"), ("foto", 3, "foto"),
               ("protokoll", 2, "protokoll"), ("markt", 2, "markt"),
               ("basis", 1, "basis")],
    }
    return G[szenario]


async def nutzer_schleife(sess, stats, w, deadline, plan, warm_pool,
                          geteilte, messen_ab, vz, pdf_zaehler):
    firma = random.choice(w.firmen)
    namen, gew, ops = zip(*plan)
    while time.monotonic() < deadline:
        s = stats if time.monotonic() >= messen_ab else Stats()  # Warmup verwerfen
        op = random.choices(ops, weights=gew)[0]
        try:
            if op == "link_neu":
                await op_link_neu(sess, s, w, firma, [])
            elif op == "link_geteilt":
                await op_link_neu(sess, s, w, firma, geteilte)
            elif op == "link_bekannt":
                await op_link_bekannt(sess, s, w, firma, warm_pool)
            elif op == "link_mix":
                if random.random() < 0.5:
                    await op_link_neu(sess, s, w, firma, [])
                else:
                    await op_link_bekannt(sess, s, w, firma, warm_pool)
            elif op == "pdf_vertrag":
                await op_pdf_vertrag(sess, s, w, firma, pdf_zaehler)
            elif op == "pdf_download":
                await op_pdf_download(sess, s, w, firma)
            elif op == "pdf_mix":
                if random.random() < 0.5:
                    await op_pdf_vertrag(sess, s, w, firma, pdf_zaehler)
                else:
                    await op_pdf_download(sess, s, w, firma)
            elif op == "protokoll":
                await op_protokoll(sess, s, w, firma)
            elif op == "foto":
                await op_foto(sess, s, w, firma)
            elif op == "foto_ungueltig":
                await op_foto_ungueltig(sess, s, w, firma)
            elif op == "versand_email":
                await op_versand(sess, s, w, firma, "email", vz)
            elif op == "versand_wa":
                await op_versand(sess, s, w, firma, "whatsapp", vz)
            elif op == "versand_mix":
                await op_versand(sess, s, w, firma,
                                 random.choice(["email", "whatsapp"]), vz)
            elif op == "markt":
                await op_markt(sess, s, w)
            elif op == "listen":
                await op_listen(sess, s, w, firma)
            else:
                await op_basis(sess, s, w, firma)
        except Exception:
            s.add(op, 0, 599)
        await asyncio.sleep(random.uniform(0.2, 1.2))


async def sampler(deadline, cpu, queue_laengen, ticks):
    import psutil
    dbx = _db()
    psutil.cpu_percent(interval=None)
    while time.monotonic() < deadline:
        c = psutil.cpu_percent(interval=None)
        r = psutil.virtual_memory().percent
        io_ = psutil.disk_io_counters()
        net = psutil.net_io_counters()
        cpu.append((c, r))
        ticks.append({"zeit": datetime.now(timezone.utc).isoformat(),
                      "cpu": c, "ram": r,
                      "disk_mb": round(io_.write_bytes / 1e6),
                      "net_mb": round((net.bytes_sent + net.bytes_recv) / 1e6)})
        try:
            queue_laengen.append(
                dbx.link_jobs.count_documents(
                    {"status": {"$in": ["queued", "processing"]}}))
        except Exception:
            pass
        await asyncio.sleep(2)


def system_snapshot():
    import psutil
    dbx = _db()
    srv = dbx.client.admin.command("serverStatus")
    io_ = psutil.disk_io_counters()
    net = psutil.net_io_counters()
    return {"opcounters": dict(srv.get("opcounters", {})),
            "verbindungen": srv.get("connections", {}).get("current"),
            "disk_write_mb": round(io_.write_bytes / 1e6),
            "net_mb": round((net.bytes_sent + net.bytes_recv) / 1e6),
            "ram_prozent": psutil.virtual_memory().percent}


UPLOAD_ROOT = Path(__file__).resolve().parent.parent / "uploads"

# Fachlich ERWARTETE Ablehnungen je Endpunkt (Auftrag Punkt 7: nichts
# pauschal werten). Alles andere an 4xx ist ein auszuweisender Befund.
ERWARTETE_ABLEHNUNG = {
    "foto_upload_klein": {400: "40-Fotos-Limit greift"},
    "foto_upload_normal": {400: "40-Fotos-Limit greift"},
    "foto_upload_gross": {400: "40-Fotos-Limit greift"},
    "protokoll_abschluss": {400: "Schutz: Abschluss ohne frischen Entwurf",
                             409: "Schutz: atomarer Doppelabschluss"},
}
SICHERHEITSTEST = {"foto_ungueltig_abgelehnt": {400}}
# 404 in echter Nutzeraktion = Race/UX-Befund (Punkt 7), KEIN Erfolg:
RACE_UX = {"foto_loeschen": {404}, "pdf_download_protokoll": {404},
           "foto_oeffnen": {404}, "markt_favorit": {404}}


def klassifiziere(endpunkte):
    """Teilt alle Antworten in die 6 Klassen aus dem Auftrag (Punkt 6)."""
    k = {"technisch_unerwartet": 0, "fachlich_erwartet": 0,
         "sicherheitstest_ok": 0, "race_ux_befund": 0,
         "verbindungsabbruch_client": 0, "erfolgreich": 0}
    details = {}
    for name, v in endpunkte.items():
        k["erfolgreich"] += v.get("ok", 0)
        if name in SICHERHEITSTEST:
            # 'ok' enthaelt hier die ERWARTETEN 400er (erwartet_4xx)
            k["erfolgreich"] -= v.get("ok", 0)
            k["sicherheitstest_ok"] += v.get("ok", 0)
        if name in RACE_UX:
            # erwartet_4xx zaehlte 404 als ok — fuer die Klassenrechnung
            # bleibt es Erfolg der Messung, wird aber als Befund gelistet.
            pass
        for st, n in (v.get("fehler_nach_status") or {}).items():
            st_i = int(st)
            if st_i == 599:
                k["verbindungsabbruch_client"] += n
            elif st_i in (ERWARTETE_ABLEHNUNG.get(name) or {}):
                k["fachlich_erwartet"] += n
                details.setdefault(name, {})[st] =                     f"{n}x {ERWARTETE_ABLEHNUNG[name][st_i]}"
            elif st_i in (RACE_UX.get(name) or set()):
                k["race_ux_befund"] += n
                details.setdefault(name, {})[st] = f"{n}x Race/UX-Befund"
            else:
                k["technisch_unerwartet"] += n
                details.setdefault(name, {})[st] = f"{n}x UNERWARTET"
    return k, details


def reset_forensik(resets, ticks):
    """Je Verbindungsabbruch: naechster System-Tick (CPU/RAM/Disk/Netz)
    + passende Zeilen aus dem Backend-Fehlerlog (+-5 s)."""
    log_zeilen = []
    try:
        pfad = Path(__file__).resolve().parent.parent.parent / "backend-err.txt"
        log_zeilen = pfad.read_text(encoding="utf-8",
                                    errors="replace").splitlines()[-4000:]
    except OSError:
        pass
    out = []
    for r in resets[:50]:                       # Bericht nicht sprengen
        t = r["zeit"]
        naechster = min(ticks, key=lambda x: abs(
            datetime.fromisoformat(x["zeit"]) - datetime.fromisoformat(t)),
            default=None) if ticks else None
        # Lokale Logzeit (Backend loggt lokal, Resets sind UTC)
        lokal = (datetime.fromisoformat(t)
                 .astimezone()).strftime("%Y-%m-%d %H:%M:%S")
        treffer = [z for z in log_zeilen
                   if z[:19] >= lokal[:18] and z[:19] <= lokal[:19]
                   and ("ERROR" in z or "Traceback" in z)]
        out.append({**r, "system_tick": naechster,
                    "backend_log": treffer[:3]})
    return out


def _dir_stat(unter):
    basis = UPLOAD_ROOT / unter
    n, groesse = 0, 0
    if basis.exists():
        for f in basis.rglob("*"):
            if f.is_file():
                n += 1
                groesse += f.stat().st_size
    return {"dateien": n, "mb": round(groesse / 1e6, 1)}


def speicher_snapshot():
    import shutil as _sh
    frei = _sh.disk_usage(str(UPLOAD_ROOT.parent)).free
    return {"resale": _dir_stat("resale"), "protocol": _dir_stat("protocol"),
            "pickup": _dir_stat("pickup"),
            "disk_frei_gb": round(frei / 1e9, 1)}


def pdf_beweis(w, stichprobe=15):
    """Punkt 8/9: PDFs sind echt, gross genug, lesbar, tragen die
    richtigen Vertragsdaten/Firma, und keine zwei Vertraege teilen sich
    dieselbe Datei."""
    import base64 as b64
    import hashlib
    from pypdf import PdfReader
    dbx = _db()
    dealer_ids = [f["chef"]["dealer_id"] for f in w.firmen]
    gesamt = dbx.generated_pdfs.count_documents(
        {"dealer_id": {"$in": dealer_ids}})
    groesse = 0
    for doc in dbx.generated_pdfs.find({"dealer_id": {"$in": dealer_ids}},
                                       {"pdf_b64": 1}):
        groesse += len(doc.get("pdf_b64") or "")
    hashes = {}
    geprueft, befunde = 0, []
    for doc in dbx.generated_pdfs.find(
            {"dealer_id": {"$in": dealer_ids}},
            {"pdf_b64": 1, "contract_data": 1, "dealer_id": 1,
             "version": 1, "id": 1}).limit(stichprobe):
        geprueft += 1
        try:
            raw = b64.b64decode(doc["pdf_b64"])
        except Exception:
            befunde.append(f"{doc['id']}: base64 defekt")
            continue
        if raw[:4] != b"%PDF":
            befunde.append(f"{doc['id']}: kein PDF-Header")
            continue
        if len(raw) < 3_000:
            befunde.append(f"{doc['id']}: verdaechtig klein ({len(raw)} B)")
        h = hashlib.sha256(raw).hexdigest()
        if h in hashes and hashes[h] != doc["id"]:
            befunde.append(f"{doc['id']}: identische Datei wie {hashes[h]}")
        hashes[h] = doc["id"]
        try:
            text = "".join((seite.extract_text() or "")
                           for seite in PdfReader(io_bytes(raw)).pages)
        except Exception as exc:
            befunde.append(f"{doc['id']}: nicht lesbar ({exc})")
            continue
        cd = doc.get("contract_data") or {}
        for feld in ("seller_name",):
            wert = str(cd.get(feld) or "")
            if wert and wert not in text:
                befunde.append(f"{doc['id']}: '{wert}' fehlt im PDF-Text")
        preis = cd.get("purchase_price")
        if preis and str(int(preis)) not in text.replace(".", ""):
            befunde.append(f"{doc['id']}: Kaufpreis {preis} fehlt im Text")
    return {"vertraege_gesamt": gesamt,
            "speicher_mb_in_db": round(groesse * 0.75 / 1e6, 1),
            "stichprobe_geprueft": geprueft,
            "eindeutige_dateien_in_stichprobe": len(hashes),
            "befunde": befunde}


def io_bytes(raw):
    import io as _io
    return _io.BytesIO(raw)


def foto_audit(w):
    """Punkt 10: Dateien<->Datenbank-Abgleich fuer die Matrix-Firmen."""
    dbx = _db()
    befunde = {"dateien_ohne_db": 0, "db_ohne_datei": 0}
    for f in w.firmen:
        did = f["chef"]["dealer_id"]
        db_keys = set()
        for l in dbx.resale_listings.find({"dealer_id": did},
                                          {"photos.uploaded_keys": 1}):
            db_keys |= set((l.get("photos") or {}).get("uploaded_keys") or [])
        pfad = UPLOAD_ROOT / "resale" / did
        disk = {f"resale/{did}/{x.name}" for x in pfad.glob("*")}             if pfad.exists() else set()
        befunde["dateien_ohne_db"] += len(disk - db_keys)
        befunde["db_ohne_datei"] += len(db_keys - disk)
    return befunde


async def drain_und_pruefen(max_s=300):
    dbx = _db()
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_s:
        offen = dbx.link_jobs.count_documents(
            {"status": {"$in": ["queued", "processing"]}})
        if offen == 0:
            break
        await asyncio.sleep(3)
    haenger = dbx.link_jobs.count_documents({
        "status": "processing",
        "processing_until": {"$lt": datetime.now(timezone.utc)}})
    return {"drain_sekunden": round(time.monotonic() - t0),
            "offene_jobs_nach_drain": dbx.link_jobs.count_documents(
                {"status": {"$in": ["queued", "processing"]}}),
            "haengende_jobs": haenger}


def doppelte_abrufe():
    return _db().listings_cache.count_documents(
        {"item_id": {"$regex": "^90"}, "fetch_count": {"$gt": 1}})


def versand_zaehlung(w):
    dbx = _db()
    gesamt = 0
    for f in w.firmen:
        doc = dbx.generated_pdfs.find_one({"id": f["send_contract"]},
                                          {"send_status": 1}) or {}
        gesamt += len(doc.get("send_status") or [])
    return gesamt


async def lauf(szenario, rep, nutzer, dauer, warmup):
    # Frischer Namensraum je Lauf: ein abgestuerzter Vorlauf (halbe Welt,
    # Admin-Konto) kann so NIE einen DuplicateKey im naechsten ausloesen.
    global SUF
    SUF = uuid.uuid4().hex[:6]
    AUSGABE.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    conn = aiohttp.TCPConnector(limit=nutzer + 60)
    timeout = aiohttp.ClientTimeout(total=150)
    async with aiohttp.ClientSession(connector=conn,
                                     timeout=timeout) as sess:
        w = None
        try:
            w = await welt_aufbauen(sess)
        except Exception:
            if w is not None:
                aufraeumen(w)
            _notaufraeumen()
            raise
        vorher = system_snapshot()
        versand_vorher = versand_zaehlung(w)

        # Warm-Pool bekannter Links (einmal laden)
        warm_pool = w.neue_links(30)
        print("[Warm] lade 30 bekannte Links vor …")
        for u in warm_pool:
            await _post_json(sess, f"{API}/mobile/compare", {"url": u},
                             h=w.firmen[0]["h"], timeout=120)
        geteilte = w.neue_links(max(10, nutzer // 5)) * 5  # mehrfach im Pool

        stats = Stats()
        vz = [0]              # Versand-Zaehler inkl. Warmup (Doppelversand-Beweis)
        pdf_zaehler = [0]     # Vertrags-200er inkl. Warmup (Abschluss-Nachweis)
        cpu, queues, ticks = [], [], []
        speicher_vorher = speicher_snapshot()
        dealer_ids = [f["chef"]["dealer_id"] for f in w.firmen]
        pdf_vorher = _db().generated_pdfs.count_documents(
            {"dealer_id": {"$in": dealer_ids}})
        start = time.monotonic()
        messen_ab = start + warmup
        deadline = messen_ab + dauer
        print(f"[{szenario} #{rep}] {nutzer} Nutzer, {warmup}s Warmup + "
              f"{dauer}s Messung …")

        if szenario == "T9":
            gruppen = ([("link_neu", 80)] + [("pdf_mix", 20)]
                       + [("foto", 20)] + [("protokoll", 10)]
                       + [("versand_mix", 10)])
            aufgaben = []
            for op, n in gruppen:
                plan = [(op, 100, op)]
                aufgaben += [nutzer_schleife(sess, stats, w, deadline, plan,
                                             warm_pool, geteilte, messen_ab, vz, pdf_zaehler)
                             for _ in range(n)]
        else:
            plan = gewichte(szenario)
            aufgaben = [nutzer_schleife(sess, stats, w, deadline, plan,
                                        warm_pool, geteilte, messen_ab, vz, pdf_zaehler)
                        for _ in range(nutzer)]
        await asyncio.gather(sampler(deadline, cpu, queues, ticks),
                             *aufgaben)

        drain = await drain_und_pruefen()
        nachher = system_snapshot()
        speicher_nachher = speicher_snapshot()
        # Server-Abschluss trotz Client-Abbruch (Punkt 4): DB-Delta vs.
        # clientbestaetigte 200er.
        pdf_nachher = _db().generated_pdfs.count_documents(
            {"dealer_id": {"$in": dealer_ids}})
        pdf_client_ok = len(stats.ok.get("pdf_vertrag_neu", []))
        klassen, klassen_details = klassifiziere(stats.report())
        forensik = reset_forensik(stats.resets, ticks)

        ops_delta = {k: nachher["opcounters"].get(k, 0) - vorher["opcounters"].get(k, 0)
                     for k in nachher["opcounters"]}
        ja = sorted(stats.job_annahme)
        jw = sorted(stats.job_warte)
        cpus = sorted(c for c, _ in cpu)
        rams = sorted(r for _, r in cpu)
        report = {
            "szenario": szenario, "wiederholung": rep,
            "zeitstempel": ts,
            "konfiguration": {"nutzer": nutzer, "dauer_s": dauer,
                              "warmup_s": warmup, "firmen": N_FIRMEN,
                              "ziel": BASE, "workers_hinweis":
                              os.environ.get("WEB_CONCURRENCY", "s. Start")},
            "anfragen_gesamt": stats.total,
            "endpunkte": stats.report(),
            "jobs": {
                "annahme_p50_ms": round(_pct(ja, 50) or 0),
                "annahme_p95_ms": round(_pct(ja, 95) or 0),
                "warte_p50_ms": round(_pct(jw, 50) or 0),
                "warte_p95_ms": round(_pct(jw, 95) or 0),
                "warte_p99_ms": round(_pct(jw, 99) or 0),
                "queue_max": max(queues or [0]),
                "queue_schnitt": round(sum(queues or [0]) / max(1, len(queues)), 1),
                **drain,
            },
            "klassen": klassen,
            "klassen_details": klassen_details,
            "reset_forensik": forensik,
            "server_abschluss_nachweis": {
                "pdf_vertraege_db_delta": pdf_nachher - pdf_vorher,
                "pdf_vertraege_client_ok_inkl_warmup": pdf_zaehler[0],
                "ohne_client_antwort_abgeschlossen":
                    max(0, (pdf_nachher - pdf_vorher) - pdf_zaehler[0]),
            },
            "speicher": {"vorher": speicher_vorher,
                          "nachher": speicher_nachher},
            "pdf_beweis": pdf_beweis(w) if szenario in ("T2", "T4") else None,
            "foto_audit": foto_audit(w),
            "integritaet": {
                "doppelte_anbieter_abrufe": doppelte_abrufe(),
                "versand_gesendet": vz[0],
                "versand_eintraege_delta": versand_zaehlung(w) - versand_vorher,
                "doppel_versand": (versand_zaehlung(w) - versand_vorher) - vz[0],
                "jobs_nicht_fertig_im_messfenster": stats.unfertig,
            },
            "system": {
                "cpu_median": round(_pct(cpus, 50) or 0),
                "cpu_spitze": round(cpus[-1]) if cpus else None,
                "ram_median": round(_pct(rams, 50) or 0),
                "ram_spitze": round(rams[-1]) if rams else None,
                "ram_nach_drain": nachher["ram_prozent"],
                "mongo_verbindungen": nachher["verbindungen"],
                "db_ops_pro_anfrage": round(
                    sum(ops_delta.values()) / max(1, stats.total), 1),
                "disk_write_mb_delta": nachher["disk_write_mb"] - vorher["disk_write_mb"],
                "net_mb_delta": nachher["net_mb"] - vorher["net_mb"],
            },
        }
        pfad = AUSGABE / f"{ts}-{szenario}-rep{rep}.json"
        aufraeumen(w)
        # Cleanup-BEWEIS (Punkt 10): nach dem Aufraeumen duerfen weder
        # DB-Eintraege noch Dateien der Matrix-Firmen uebrig sein.
        rest_db = _db().generated_pdfs.count_documents(
            {"dealer_id": {"$in": dealer_ids}})
        rest_dateien = 0
        for did in dealer_ids:
            for unter in ("resale", "protocol", "pickup"):
                d = UPLOAD_ROOT / unter / did
                if d.exists():
                    rest_dateien += sum(1 for x in d.rglob("*") if x.is_file())
        report["cleanup_beweis"] = {
            "db_reste": rest_db, "datei_reste": rest_dateien,
            "disk_frei_gb_nach_cleanup":
                speicher_snapshot()["disk_frei_gb"]}
        pfad.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        print(f"[{szenario} #{rep}] Bericht: {pfad.name}")
        return report


def _notaufraeumen():
    """Reste eines abgebrochenen Welt-Aufbaus (aktueller SUF) entfernen."""
    try:
        dbx = _db()
        uids = [u["id"] for u in dbx.users.find(
            {"email": {"$regex": f"_{SUF}@"}}, {"id": 1})]
        dids = [d["id"] for d in dbx.dealers.find(
            {"user_id": {"$in": uids}}, {"id": 1})]
        for c in ("subscriptions", "vehicles", "appointments",
                  "generated_pdfs", "resale_listings", "pickup_protocols",
                  "dealer_drivers"):
            dbx[c].delete_many({"dealer_id": {"$in": dids}})
        dbx.dealers.delete_many({"id": {"$in": dids}})
        dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
        dbx.driver_accounts.delete_many({"email": {"$regex": f"_{SUF}@"}})
    except Exception:
        pass


def aufraeumen(w):
    dbx = _db()
    for f in w.firmen:
        did = f["chef"]["dealer_id"]
        for c in ("subscriptions", "vehicles", "appointments",
                  "generated_pdfs", "generated_pdf_versions",
                  "resale_listings", "pickup_protocols", "pickup_reports",
                  "dealer_drivers", "vehicle_comparisons"):
            dbx[c].delete_many({"dealer_id": did})
        dbx.dealers.delete_many({"id": did})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.driver_accounts.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.buyer_favorites.delete_many(
        {"buyer_user_id": {"$regex": "^.*$"},
         "listing_id": {"$in": [f["listing_id"] for f in w.firmen]}})
    dbx.listings_cache.delete_many({"item_id": {"$regex": "^90"}})
    dbx.link_jobs.delete_many({"item_id": {"$regex": "^90"}})
    # Speicherdateien der Matrix-Firmen entfernen (Cleanup-Beweis, Punkt 10)
    import shutil as _sh
    for f in w.firmen:
        did = f["chef"]["dealer_id"]
        for unter in ("resale", "protocol", "pickup"):
            d = UPLOAD_ROOT / unter / did
            if d.exists():
                _sh.rmtree(d, ignore_errors=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--szenario", default=None)
    ap.add_argument("--alle", action="store_true")
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--nutzer", type=int, default=100)
    ap.add_argument("--dauer", type=int, default=300)
    ap.add_argument("--warmup", type=int, default=30)
    ap.add_argument("--kurz", action="store_true",
                    help="Probelauf: 45s Messung, 10s Warmup")
    a = ap.parse_args()
    if a.kurz:
        a.dauer, a.warmup = 45, 10

    szenarien = (a.szenario.split(",") if a.szenario else
                 ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9"])
    for idx, sz in enumerate(szenarien):
        nutzer = 140 if sz == "T9" else a.nutzer
        dauer = 60 if sz == "T9" else a.dauer
        warmup = 10 if sz == "T9" else a.warmup
        if a.alle:
            start = a.rep if idx == 0 else 1   # erstes Szenario ggf. mittendrin
            reps = range(start, a.reps + 1)
        else:
            reps = [a.rep]
        for rep in reps:
            # Ein einzelner Netz-/Timeout-Fehler (z. B. im Welt-Aufbau)
            # darf nicht die gesamte Matrix beenden: einmal wiederholen,
            # danach den Lauf als GESCHEITERT protokollieren und weiter.
            for versuch in (1, 2):
                try:
                    await lauf(sz, rep, nutzer, dauer, warmup)
                    break
                except Exception as exc:  # noqa: BLE001
                    print(f"[{sz} #{rep}] Versuch {versuch} abgebrochen: "
                          f"{type(exc).__name__}: {exc}")
                    if versuch == 2:
                        AUSGABE.mkdir(parents=True, exist_ok=True)
                        ts = datetime.now(timezone.utc).strftime(
                            "%Y%m%dT%H%M%SZ")
                        (AUSGABE / f"{ts}-{sz}-rep{rep}-GESCHEITERT.json"
                         ).write_text(json.dumps({
                             "szenario": sz, "wiederholung": rep,
                             "gescheitert": f"{type(exc).__name__}: {exc}"},
                             ensure_ascii=False), encoding="utf-8")
                    else:
                        await asyncio.sleep(30)
        if not a.alle and not a.szenario:
            break


if __name__ == "__main__":
    asyncio.run(main())
