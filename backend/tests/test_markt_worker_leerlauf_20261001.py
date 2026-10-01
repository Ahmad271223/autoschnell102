# -*- coding: utf-8 -*-
"""CI-Befund 01.10.2026: der Stack-Rauchtest meldete dreimal "nicht bereit: {ready: false}" — immer
dann, wenn zwischen Stack-Start und Rauchtest mehr als drei Minuten lagen.

Ursache: markt.jobs.worker_forever stempelte im Leerlauf (Crawler aus, kein APIFY_TOKEN, Wartung)
keinen Erfolg. server.WORKER_TAKT_S["markt"] = 60 -> /api/ready wertete "kein erfolgreicher
Durchlauf seit > 180 s" als Fehler, obwohl der Worker gesund und absichtlich untaetig war. In
Produktion haette dasselbe nach drei Minuten gegolten, sobald der Chef den Crawler ausschaltet.
Jetzt zaehlt auch ein Leerlauf-Takt als erfolgreicher Takt."""
import importlib

import pytest

from test_befunde_runde17_termine import welt  # noqa: F401


class _Stopp(BaseException):
    pass


@pytest.mark.parametrize("aktiv,token,wartung", [(False, "tok", False), (True, "", False), (True, "tok", True)],
                         ids=["crawler-aus", "ohne-token", "wartung"])
def test_leerlauf_takt_stempelt_erfolg(welt, monkeypatch, aktiv, token, wartung):  # noqa: F811
    K = importlib.import_module("markt.konfig")
    JOBS = importlib.import_module("markt.jobs")
    db = welt.db
    K._AUS_PROTOKOLLIERT["tag"] = ""
    welt.run(K.crawler_schalten(db, aktiv))
    monkeypatch.setenv("APIFY_TOKEN", token)
    erfolge, schlaf = [], []

    async def _schlaf(sek):
        schlaf.append(sek)
        if len(schlaf) >= 2:
            raise _Stopp()

    async def _wartung(db_):
        return wartung

    async def _einmal(*a, **k):
        raise AssertionError("im Leerlauf darf nichts gecrawlt werden")
    monkeypatch.setattr(JOBS.asyncio, "sleep", _schlaf)
    monkeypatch.setattr(JOBS, "_wartung_aktiv", _wartung)
    monkeypatch.setattr(JOBS, "einmal", _einmal)
    with pytest.raises(_Stopp):
        welt.run(JOBS.worker_forever(db, erfolg=lambda: erfolge.append(1)))
    assert schlaf == [15, 60] and erfolge == [1], "ein Leerlauf-Takt ist ein erfolgreicher Takt"


def test_ready_gilt_drei_takte():
    """Die Grenze in /api/ready bleibt 3 x Takt (60 s fuer markt) — der Worker muss sie im Leerlauf halten."""
    import server
    assert server.WORKER_TAKT_S.get("link_jobs") == 30
    quelle = open(server.__file__, encoding="utf-8").read()
    assert 'WORKER_TAKT_S["markt"] = 60' in quelle
