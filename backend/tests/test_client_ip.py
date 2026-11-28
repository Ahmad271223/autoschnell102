# -*- coding: utf-8 -*-
"""Echte Besucher-Adresse hinter Vermittlern (Cloudflare, Load Balancer).

Hintergrund: Die Anfragesperren zaehlen je Adresse. Kommt statt der echten
Besucher-Adresse immer die des Load Balancers an, sperrt eine einzige
fehlgeschlagene Anmeldung alle anderen Nutzer aus. Umgekehrt darf ein
Besucher seine Adresse nicht faelschen koennen, sonst laufen die Sperren
ins Leere.
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _Client:
    def __init__(self, host):
        self.host = host


class _Anfrage:
    """Nachbau des Teils von Request, den client_ip liest."""

    def __init__(self, peer, **kopfzeilen):
        self.client = _Client(peer)
        self.headers = {k.lower().replace("_", "-"): v for k, v in kopfzeilen.items()}


def _modul(monkeypatch, *, trust="true", proxies=""):
    monkeypatch.setenv("TRUST_PROXY", trust)
    monkeypatch.setenv("TRUSTED_PROXIES", proxies)
    import rate_limiter
    return importlib.reload(rate_limiter)


@pytest.fixture(autouse=True)
def _zuruecksetzen():
    yield
    import rate_limiter
    importlib.reload(rate_limiter)


# ------------------------------------------------ ohne eigene Vermittler
def test_ohne_liste_gilt_der_letzte_eintrag(monkeypatch):
    """Bisheriges Verhalten bleibt: ein Proxy davor, letzter Eintrag zaehlt."""
    rl = _modul(monkeypatch)
    a = _Anfrage("127.0.0.1", x_forwarded_for="1.2.3.4, 10.0.0.4")
    assert rl.client_ip(a) == "10.0.0.4"


def test_ohne_proxy_zaehlt_der_direkte_nachbar(monkeypatch):
    rl = _modul(monkeypatch, trust="false")
    a = _Anfrage("203.0.113.9", x_forwarded_for="1.2.3.4")
    assert rl.client_ip(a) == "203.0.113.9"


# ------------------------------------------------- mit eigenen Vermittlern
def test_load_balancer_wird_uebersprungen(monkeypatch):
    """Cloudflare -> Load Balancer -> nginx: der Besucher muss ankommen."""
    rl = _modul(monkeypatch, proxies="10.0.0.0/16,127.0.0.1")
    a = _Anfrage("10.0.0.4", x_forwarded_for="203.0.113.7, 10.0.0.4, 127.0.0.1")
    assert rl.client_ip(a) == "203.0.113.7"


def test_cloudflare_kopfzeile_wird_genutzt(monkeypatch):
    rl = _modul(monkeypatch, proxies="10.0.0.0/16")
    a = _Anfrage("10.0.0.4", cf_connecting_ip="198.51.100.5",
                 x_forwarded_for="198.51.100.5, 10.0.0.4")
    assert rl.client_ip(a) == "198.51.100.5"


def test_cloudflare_kopfzeile_hinter_eigenem_nginx_nicht_faelschbar(monkeypatch):
    """Nachpruefung Runde 10: Der Nachbar ist immer der eigene nginx (also
    Vermittler). Schickt der BESUCHER selbst CF-Connecting-IP mit, darf sie
    nicht ueber die von nginx ermittelte Adresse gewinnen."""
    rl = _modul(monkeypatch, proxies="172.16.0.0/12,10.0.0.4/32")
    a = _Anfrage("172.18.0.2", x_forwarded_for="203.0.113.9", cf_connecting_ip="1.2.3.4")
    assert rl.client_ip(a) == "203.0.113.9"
    b = _Anfrage("172.18.0.2", x_real_ip="203.0.113.9", cf_connecting_ip="1.2.3.4")
    assert rl.client_ip(b) == "203.0.113.9"
    # stimmt die Kopfzeile mit der Kette ueberein (echter Cloudflare-Weg), zaehlt sie
    c = _Anfrage("172.18.0.2", x_forwarded_for="198.51.100.5", cf_connecting_ip="198.51.100.5")
    assert rl.client_ip(c) == "198.51.100.5"


def test_cloudflare_kopfzeile_von_fremd_wird_ignoriert(monkeypatch):
    """Kommt die Anfrage NICHT ueber einen eigenen Vermittler, darf die
    Kopfzeile nicht zaehlen — sonst faelscht sie jeder selbst."""
    rl = _modul(monkeypatch, proxies="10.0.0.0/16")
    a = _Anfrage("203.0.113.66", cf_connecting_ip="1.1.1.1")
    assert rl.client_ip(a) == "203.0.113.66"


def test_gefaelschte_kette_hilft_nicht(monkeypatch):
    """Der Besucher schickt selbst X-Forwarded-For mit. Unsere Vermittler
    haengen ihre Adressen HINTEN an; genommen wird der letzte fremde
    Eintrag — also die echte Adresse, nicht die erfundene."""
    rl = _modul(monkeypatch, proxies="10.0.0.0/16,127.0.0.1")
    a = _Anfrage("10.0.0.4",
                 x_forwarded_for="9.9.9.9, 203.0.113.7, 10.0.0.4, 127.0.0.1")
    assert rl.client_ip(a) == "203.0.113.7"


def test_nur_eigene_vermittler_in_der_kette(monkeypatch):
    rl = _modul(monkeypatch, proxies="10.0.0.0/16,127.0.0.1")
    a = _Anfrage("10.0.0.4", x_forwarded_for="10.0.0.4, 127.0.0.1")
    assert rl.client_ip(a) == "10.0.0.4"


def test_ohne_kopfzeilen_bleibt_der_nachbar(monkeypatch):
    rl = _modul(monkeypatch, proxies="10.0.0.0/16")
    assert rl.client_ip(_Anfrage("10.0.0.4")) == "10.0.0.4"
    assert rl.client_ip(_Anfrage("")) == "unknown"


def test_x_real_ip_als_rueckfall(monkeypatch):
    rl = _modul(monkeypatch, proxies="10.0.0.0/16")
    a = _Anfrage("10.0.0.4", x_real_ip="192.0.2.44")
    assert rl.client_ip(a) == "192.0.2.44"


def test_unsinnige_netze_werden_ignoriert(monkeypatch):
    """Ein Tippfehler in TRUSTED_PROXIES darf den Start nicht verhindern."""
    rl = _modul(monkeypatch, proxies="keine-ip, 10.0.0.0/16 , /32")
    assert len(rl._TRUSTED_PROXIES) == 1
    a = _Anfrage("10.0.0.4", x_forwarded_for="203.0.113.7, 10.0.0.4")
    assert rl.client_ip(a) == "203.0.113.7"


# ------------------------------------------ Runde 8, Befund 4
def test_fremder_nachbar_darf_keine_kopfzeilen_setzen(monkeypatch):
    """Erreicht jemand das Backend OHNE nginx davor (oeffentliche Adresse
    als direkter Nachbar), zaehlen seine Kopfzeilen nicht — er IST der
    Besucher. Vorher galt bei TRUST_PROXY=true jede Kopfzeile, egal von wem."""
    rl = _modul(monkeypatch)
    a = _Anfrage("203.0.113.9", x_forwarded_for="1.2.3.4", x_real_ip="5.6.7.8")
    assert rl.client_ip(a) == "203.0.113.9"
    rl = _modul(monkeypatch, proxies="10.0.0.0/16")
    assert rl.client_ip(a) == "203.0.113.9"


def test_unsinn_in_kopfzeilen_wird_nicht_zur_adresse(monkeypatch):
    """"not-an-ip" landete frueher als Schluessel im Zaehler."""
    rl = _modul(monkeypatch)
    a = _Anfrage("127.0.0.1", x_forwarded_for="not-an-ip", x_real_ip="auch nicht")
    assert rl.client_ip(a) == "127.0.0.1"
    a = _Anfrage("127.0.0.1", x_forwarded_for="not-an-ip, 203.0.113.9")
    assert rl.client_ip(a) == "203.0.113.9"
    a = _Anfrage("127.0.0.1", x_real_ip="203.0.113.9:4711")
    assert rl.client_ip(a) == "203.0.113.9"


def test_eigener_vermittler_aus_privatem_netz_bleibt_erlaubt(monkeypatch):
    """Docker-Netz, Hetzner-Privatnetz, Loopback: das sind die Orte, an
    denen nginx oder der Load Balancer stehen — ohne Liste gelten genau die."""
    rl = _modul(monkeypatch)
    for nachbar in ("127.0.0.1", "172.18.0.5", "10.0.1.7", "192.168.1.2"):
        a = _Anfrage(nachbar, x_forwarded_for="203.0.113.9")
        assert rl.client_ip(a) == "203.0.113.9", nachbar


# ------------------------------------------ Runde 9
def test_docker_nachbar_zaehlt_auch_mit_gesetzter_liste(monkeypatch):
    """Der Fall aus dem Pruefbericht: TRUSTED_PROXIES=127.0.0.1 (compose-
    Standard von frueher), der Nachbar ist aber der nginx-Container mit
    172.19.0.4. Vorher: Kopfzeile ignoriert, ALLE Besucher unter 172.19.0.4
    in einem Zaehler. Jetzt: die echte Besucheradresse."""
    rl = _modul(monkeypatch, proxies="127.0.0.1")
    a = _Anfrage("172.19.0.4", x_forwarded_for="203.0.113.9")
    assert rl.client_ip(a) == "203.0.113.9"
    rl = _modul(monkeypatch, proxies="10.0.0.0/16")
    b = _Anfrage("172.19.0.4", x_forwarded_for="198.51.100.7, 10.0.0.9")
    assert rl.client_ip(b) == "198.51.100.7"


# ------------------------------------------ Pruefbericht 20.09.2026, T-02
# Bisher wurde client_ip nur gegen den Nachbau _Anfrage geprueft. Hier laeuft
# die ECHTE Anmelderoute (routes.auth.login) ueber httpx.ASGITransport mit
# TRUST_PROXY=true — mit gefaelschten Kopfzeilen vom selben Nachbarn.
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"


def _mongo_da() -> bool:
    try:
        from pymongo import MongoClient
        MongoClient(MONGO_URL, serverSelectionTimeoutMS=2000).admin.command("ping")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _mongo_da(), reason="MongoDB fehlt (MONGO_URL) — der Anmelde-Limiter zaehlt in der Datenbank")
def test_gefaelschte_kopfzeilen_vom_fremden_nachbarn_loesen_dieselbe_sperre_aus(monkeypatch):
    """Ein Angreifer mit oeffentlicher Adresse schickt bei jedem Fehlversuch
    ein anderes X-Forwarded-For / X-Real-IP / CF-Connecting-IP mit. Mit
    TRUST_PROXY=true darf ihn das NICHT aus dem Zaehler befreien: nach 10
    Fehlversuchen je Konto und Adresse (login_limiter) antwortet die Route
    429 — die Kopfzeilen eines Nicht-Vermittlers zaehlen nicht.
    Gegenprobe: kommt die Kette ueber einen eigenen Vermittler (privates
    Netz), zaehlt jeder Besucher aus X-Forwarded-For fuer sich."""
    httpx = pytest.importorskip("httpx")
    import asyncio
    import uuid
    from fastapi import FastAPI

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("LOGIN_KONTO_LIMIT", "30")
    rl = _modul(monkeypatch)                       # TRUST_PROXY=true, keine Liste
    assert rl._TRUST_PROXY and rl._RATE_LIMIT_ENABLED
    import deps
    import routes.auth as A

    app = FastAPI()
    app.include_router(A.router, prefix="/api")
    suf = uuid.uuid4().hex[:6]
    # Unbekannte Kontonummern (Muster '<nr>-<zusatz>'): kein Konto, aber der
    # Zaehler laeuft trotzdem (keine Aufzaehlung der Nummern).
    kennung_a = f"7{int(suf, 16) % 900000 + 100000}-7"
    kennung_b = f"7{int(suf, 16) % 900000 + 100000}-8"
    angreifer = "203.0.113.9"          # oeffentlich = kein Vermittler
    vermittler = "10.0.0.4"            # privates Netz = eigener Vermittler

    async def lauf():
        from motor.motor_asyncio import AsyncIOMotorClient
        # Eigener Motor-Client an DIESER Schleife — der gemeinsame deps.db
        # bliebe sonst an die Test-Schleife gebunden (andere Tests brechen).
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        db = cl[DB_NAME]
        monkeypatch.setattr(deps, "db", db)
        monkeypatch.setattr(A, "db", db)
        try:
            async def versuch(peer, kennung, i):
                transport = httpx.ASGITransport(app=app, client=(peer, 40000 + i))
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                    return await c.post("/api/auth/login", json={
                        "kontonummer": kennung, "password": f"falsch-{i}!"}, headers={
                        "X-Forwarded-For": f"198.51.100.{i}",
                        "X-Real-IP": f"192.0.2.{i}",
                        "CF-Connecting-IP": f"198.51.100.{i}",
                    })

            # 1) Angreifer: 10 Fehlversuche mit je anderer gefaelschter Adresse
            codes = [(await versuch(angreifer, kennung_a, i)).status_code for i in range(1, 13)]
            assert codes[:10] == [401] * 10, codes
            r11 = await versuch(angreifer, kennung_a, 11)
            assert codes[10] == 429 and r11.status_code == 429, codes
            assert "für dieses Konto" in r11.json()["detail"], r11.text
            # ... auch mit einer Kopfzeile, die er noch nie geschickt hat
            transport = httpx.ASGITransport(app=app, client=(angreifer, 45000))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                r = await c.post("/api/auth/login", json={"kontonummer": kennung_a, "password": "x!"},
                                 headers={"X-Forwarded-For": "8.8.8.8", "CF-Connecting-IP": "8.8.8.8"})
            assert r.status_code == 429, r.text

            # 2) Gegenprobe: ueber den eigenen Vermittler zaehlt die Kette —
            #    zwoelf verschiedene Besucher, je ein Fehlversuch: nie 429.
            codes = [(await versuch(vermittler, kennung_b, i)).status_code for i in range(1, 13)]
            assert codes == [401] * 12, codes
        finally:
            for k in (kennung_a, kennung_b):
                await db.rate_limits.delete_many({"_id": {"$regex": f"[|:]{k}:"}})
                await db.activity_logs.delete_many({"action": "auth.login.fehlgeschlagen",
                                                    "meta.identifier": k})
            await db.rate_limits.delete_many({"_id": {"$regex": f"^login-ip:{angreifer}:"}})
            cl.close()

    asyncio.run(lauf())
