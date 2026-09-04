# -*- coding: utf-8 -*-
"""Echte Besucher-Adresse hinter Vermittlern (Cloudflare, Load Balancer).

Hintergrund: Die Anfragesperren zaehlen je Adresse. Kommt statt der echten
Besucher-Adresse immer die des Load Balancers an, sperrt eine einzige
fehlgeschlagene Anmeldung alle anderen Nutzer aus. Umgekehrt darf ein
Besucher seine Adresse nicht faelschen koennen, sonst laufen die Sperren
ins Leere.
"""
import importlib
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
