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

SUF = uuid.uuid4().hex[:6]
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

    def add(self, name, ms, status, erwartet_4xx=False):
        self.total += 1
        if 200 <= status < 300 or (erwartet_4xx and 400 <= status < 500):
            self.ok.setdefault(name, []).append(ms)
        else:
            self.err.setdefault(name, {}).setdefault(status, 0)
            self.err[name][status] += 1

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
        "role": "admin", "active": True, "dealer_id": None,
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
        assert st == 200
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
            assert r.status == 200

        st, js = await _post_json(
            sess, f"{API}/resale/draft/{firma['vehicle_id']}", {}, h=h)
        assert st == 200, f"Draft {st}"
        firma["listing_id"] = js["id"]
        async with sess.put(f"{API}/resale/{firma['listing_id']}",
                            json={"price_public": 15000 + i,
                                  "price_b2b": 14000 + i}, headers=h) as r:
            assert r.status == 200
        await _post_json(sess, f"{API}/resale/{firma['listing_id']}/status",
                         {"status": "verkaufsbereit"}, h=h)
        async with sess.put(f"{API}/admin/dealers/{me['dealer_id']}/sale-plan",
                            json={"tier": "s5", "months": 1},
                            headers=w.admin_h) as r:
            assert r.status == 200
        await _post_json(sess, f"{API}/resale/{firma['listing_id']}/publish",
                         {"visibility": "public"}, h=h)
        async with sess.put(f"{API}/dealer/marketplace-profile",
                            json={"public": True}, headers=h) as r:
            assert r.status == 200

        # Protokoll einmal regulaer abschliessen (Basis fuer Korrektur-Zyklen)
        await protokoll_abschliessen(sess, firma, erste_runde=True)
        w.firmen.append(firma)

    st, js = await _post_json(sess, f"{API}/buyer/register", {
        "company_name": "Mx Kaeufer", "contact_name": "Kaeufer M",
        "email": f"mx_kauf_{SUF}@e2etest-mail.de", "password": PW,
        "phone": "0511 3"})
    assert st == 200
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


async def op_pdf_vertrag(sess, stats, w, firma):
    await _timed(sess, stats, "pdf_vertrag_neu", "POST",
                 f"{API}/contracts", headers=firma["h"], json={
                     "vehicle_id": firma["vehicle_id"],
                     "seller_name": "PDF V", "seller_address": "Weg 2",
                     "seller_zip": "30159", "seller_city": "Hannover",
                     "purchase_price": 11111,
                     "pickup_date": "2099-05-05", "pickup_time": "11:00"},
                 timeout=aiohttp.ClientTimeout(total=120))


async def op_pdf_download(sess, stats, w, firma):
    was = random.random()
    if was < 0.5:
        await _timed(sess, stats, "pdf_download_vertrag", "GET",
                     f"{API}/contracts/{firma['contract_id']}/pdf",
                     headers=firma["h"])
    else:
        await _timed(sess, stats, "pdf_download_protokoll", "GET",
                     f"{API}/driver/appointments/{firma['appt_id']}"
                     f"/protocol.pdf", headers=firma["drv_h"])


async def op_protokoll(sess, stats, w, firma):
    groesse = random.random()
    schaeden = 0 if groesse < 0.4 else (5 if groesse < 0.8 else 25)
    t0 = time.monotonic()
    st = await protokoll_abschliessen(sess, firma, schaeden=schaeden)
    stats.add("protokoll_abschluss", (time.monotonic() - t0) * 1000,
              st if st else 599, erwartet_4xx=False)


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
            keys = json.loads(body).get("uploaded", [])
            if keys:
                firma.setdefault("foto_urls", []).append(keys[0])
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
                          geteilte, messen_ab, vz):
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
                await op_pdf_vertrag(sess, s, w, firma)
            elif op == "pdf_download":
                await op_pdf_download(sess, s, w, firma)
            elif op == "pdf_mix":
                if random.random() < 0.5:
                    await op_pdf_vertrag(sess, s, w, firma)
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


async def sampler(deadline, cpu, queue_laengen):
    import psutil
    dbx = _db()
    psutil.cpu_percent(interval=None)
    while time.monotonic() < deadline:
        cpu.append((psutil.cpu_percent(interval=None),
                    psutil.virtual_memory().percent))
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
    AUSGABE.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    conn = aiohttp.TCPConnector(limit=nutzer + 60)
    timeout = aiohttp.ClientTimeout(total=150)
    async with aiohttp.ClientSession(connector=conn,
                                     timeout=timeout) as sess:
        w = await welt_aufbauen(sess)
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
        cpu, queues = [], []
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
                                             warm_pool, geteilte, messen_ab, vz)
                             for _ in range(n)]
        else:
            plan = gewichte(szenario)
            aufgaben = [nutzer_schleife(sess, stats, w, deadline, plan,
                                        warm_pool, geteilte, messen_ab, vz)
                        for _ in range(nutzer)]
        await asyncio.gather(sampler(deadline, cpu, queues), *aufgaben)

        drain = await drain_und_pruefen()
        nachher = system_snapshot()

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
        pfad.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        print(f"[{szenario} #{rep}] Bericht: {pfad.name}")
        aufraeumen(w)
        return report


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

    szenarien = ([a.szenario] if a.szenario else
                 ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9"])
    for sz in szenarien:
        nutzer = 140 if sz == "T9" else a.nutzer
        dauer = 60 if sz == "T9" else a.dauer
        warmup = 10 if sz == "T9" else a.warmup
        reps = range(1, a.reps + 1) if a.alle else [a.rep]
        for rep in reps:
            await lauf(sz, rep, nutzer, dauer, warmup)
        if not a.alle and not a.szenario:
            break


if __name__ == "__main__":
    asyncio.run(main())
