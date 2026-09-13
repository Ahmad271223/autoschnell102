# -*- coding: utf-8 -*-
"""Runde 21: Resend-Versand wiederholt bei Tempo-Limit (429) und
voruebergehenden Fehlern — damit 50 gleichzeitig sendende Sucher nicht an
Resends 10 Anfragen/Sekunde scheitern. Ohne Netz: httpx wird ersetzt."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import email_service as E  # noqa: E402


class _Antwort:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self.text = str(self._body)

    def json(self):
        return self._body


def _fake_httpx(monkeypatch, antworten, aufrufe):
    import httpx

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            aufrufe.append(headers.get("Idempotency-Key"))
            a = antworten.pop(0)
            if isinstance(a, Exception):
                raise a
            return a

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


@pytest.fixture
def ohne_warten(monkeypatch):
    geschlafen = []

    async def _schlaf(s):
        geschlafen.append(s)

    monkeypatch.setattr(E.asyncio, "sleep", _schlaf)
    monkeypatch.setattr(E, "RESEND_API_KEY", "re_test")
    return geschlafen


def _senden(**extra):
    return asyncio.run(E._send_resend(
        to="v@beispiel.de", subject="Test", text="x", html=None, anhang=None,
        anhang_name="", reply_to=[], kopie=[], absender_name="Firma", **extra))


def test_01_tempolimit_wird_wiederholt_und_retry_after_beachtet(monkeypatch, ohne_warten):
    aufrufe = []
    _fake_httpx(monkeypatch, [_Antwort(429, headers={"retry-after": "2"}),
                              _Antwort(429), _Antwort(200, {"id": "abc"})], aufrufe)
    assert _senden(idempotency_key="k1") == "abc"
    assert len(aufrufe) == 3 and set(aufrufe) == {"k1"}, "gleicher Schluessel bei jedem Versuch"
    assert ohne_warten[0] == 2.0 and len(ohne_warten) == 2


def test_02_dauerhafte_ablehnung_wird_nicht_wiederholt(monkeypatch, ohne_warten):
    aufrufe = []
    _fake_httpx(monkeypatch, [_Antwort(422, {"message": "Domain nicht verifiziert"})], aufrufe)
    assert _senden(idempotency_key="k2") == ""
    assert len(aufrufe) == 1 and ohne_warten == []


def test_03_gibt_nach_hoechstzahl_auf(monkeypatch, ohne_warten):
    aufrufe = []
    _fake_httpx(monkeypatch, [_Antwort(503)] * E.RESEND_VERSUCHE, aufrufe)
    assert _senden(idempotency_key="k3") == ""
    assert len(aufrufe) == E.RESEND_VERSUCHE
    assert sum(ohne_warten) <= E.RESEND_WARTEN_MAX


def test_04_netzfehler_nur_mit_idempotency_key_wiederholen(monkeypatch, ohne_warten):
    import httpx
    aufrufe = []
    _fake_httpx(monkeypatch, [httpx.ConnectError("weg"), _Antwort(200, {"id": "ok"})], aufrufe)
    assert _senden(idempotency_key="k4") == "ok"
    aufrufe2 = []
    _fake_httpx(monkeypatch, [httpx.ConnectError("weg")], aufrufe2)
    with pytest.raises(httpx.ConnectError):
        _senden(idempotency_key=None)
    assert len(aufrufe2) == 1, "ohne Schluessel kein zweiter Versuch (keine Doppelzustellung)"
