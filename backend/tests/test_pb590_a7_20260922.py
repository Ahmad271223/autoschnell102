# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026 (590 Befunde), Reparaturwelle A7 (22.09.2026):
Backend-Betrieb — server.py, Betriebsalarme, Migrationen, Konfiguration,
Sicherung, Skripte, Log-Schwaerzung.

  SV-05  worker_erfolg(name): /ready meldet einen Job ohne Durchlauf seit 3 x Takt
  SV-08  Sicherheitskopfzeilen aussen (auch auf 500/503)
  SV-09  error_logs-Schreiben mit Zeitlimit vor der 500-Antwort
  SV-10  /api/files: Stroemen ab 1 MB, Range -> 206, IP-Limit
  SV-11  /client-errors: 429 bei Sperre, 507 bei vollem Archiv; Kuerzen auf 90 %
  SV-12  wartender Web-Worker legt keine Indizes an (Stichprobe), AL-18 Fehlerwert
  SV-14  Plattenpruefung im Thread mit Zeitlimit
  SV-17  Stau-Warnung zaehlt nur aktive, reife Link-Jobs
  AL-11  Dump-Gegenlesen beim Sichern; Pruefsummen-Nachrechnung mit "beschaedigt"
  AL-16  ein offener Alarm je (typ, ref) — auch bei gleichzeitigen Aufrufen
  AL-17  Alarm datei_speicher_nicht_erreichbar schliesst sich selbst
  AL-20  BACKUP_AKTIV
  AL-21  konfig.GEKLEMMT -> production_check / admin
  AL-22  BACKUP_HOUR im production_check
  AL-24  Alarm-Details gekuerzt
  P-12   Telefon/IBAN und exc_info in der Redaktion
  P-19   RESEND_* ueber konfig
  SK-09/11/12/13/14/17/18/19/21 Skripte

In-Prozess gegen Wegwerf-Datenbanken (autoschnell_a7_<zufall>).
"""
import asyncio
import gzip
import hashlib
import importlib
import importlib.util
import inspect
import io
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pymongo.errors import DuplicateKeyError

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND / "scripts"
WURZEL = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SCRIPTS))
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _iso(delta_s: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


def _m(name):
    return importlib.import_module(name)


def _skript(name):
    spec = importlib.util.spec_from_file_location(f"{name}_a7", SCRIPTS / f"{name}.py")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_a7_{uuid.uuid4().hex[:10]}"
    w = SimpleNamespace(db=client[name], run=loop.run_until_complete, name=name, loop=loop)
    try:
        yield w
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


class _FakeColl:
    def __init__(self):
        self.calls = []

    async def update_one(self, filt, upd, upsert=False):
        self.calls.append((filt, upd))
        return SimpleNamespace(upserted_id=None, modified_count=0, matched_count=1)

    async def update_many(self, *a, **k):
        return SimpleNamespace(modified_count=0)

    async def count_documents(self, *a, **k):
        return 0

    async def find_one(self, *a, **k):
        return None


class _FakeDb:
    def __init__(self):
        self.betriebsalarme = _FakeColl()
        self.system_flags = _FakeColl()


# ====================================================================== konfig (AL-21, P-19)
def test_kommazahl_env_und_geklemmt(monkeypatch):
    K = _m("konfig")
    monkeypatch.setenv("A7_RATE", "2,5")
    assert K.kommazahl_env("A7_RATE", 10.0) == 2.5
    monkeypatch.setenv("A7_RATE", "abc")
    try:
        assert K.kommazahl_env("A7_RATE", 10.0) == 10.0
        assert K.FEHLERHAFT["A7_RATE"] == "abc"
    finally:
        K.FEHLERHAFT.pop("A7_RATE", None)
    monkeypatch.setenv("A7_RATE", "0")
    try:
        assert K.kommazahl_env("A7_RATE", 10.0, unten=0.1) == 0.1
        assert K.GEKLEMMT["A7_RATE"] == (0.0, 0.1)
    finally:
        K.GEKLEMMT.pop("A7_RATE", None)
    monkeypatch.setenv("A7_TAGE", "900")
    try:
        assert K.zahl_env("A7_TAGE", 60, oben=60) == 60
        assert K.GEKLEMMT["A7_TAGE"] == (900, 60)
    finally:
        K.GEKLEMMT.pop("A7_TAGE", None)
    monkeypatch.setenv("A7_TAGE", "30")
    assert K.zahl_env("A7_TAGE", 60, oben=60) == 30 and "A7_TAGE" not in K.GEKLEMMT


def test_p19_resend_werte_ueber_konfig():
    q = (BACKEND / "email_service.py").read_text(encoding="utf-8")
    assert 'int(os.environ.get("RESEND' not in q and 'float(os.environ.get("RESEND' not in q
    for name in ("RESEND_PARALLEL", "RESEND_VERSUCHE", "RESEND_PROZESSE"):
        assert f'_zahl_env("{name}"' in q, name
    for name in ("RESEND_WARTEN_MAX", "RESEND_RATE"):
        assert f'_kommazahl_env("{name}"' in q, name
    E = _m("email_service")
    assert E.RESEND_PARALLEL >= 1 and E.RESEND_PROZESSE >= 1 and E.RESEND_RATE > 0


# ====================================================================== production_check (AL-21/22, P-19)
class _Log:
    def __init__(self):
        self.warnungen, self.fehler = [], []

    def warning(self, msg, *a):
        self.warnungen.append(msg % a if a else msg)

    def error(self, msg, *a):
        self.fehler.append(msg % a if a else msg)

    def info(self, *a, **k):
        pass


def _ohne_s3(monkeypatch):
    for v in ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY", "APP_ENV"):
        monkeypatch.delenv(v, raising=False)


def test_al22_p19_backup_hour_und_resend_im_produktionscheck(monkeypatch):
    PC = _m("production_check")
    K = _m("konfig")
    _ohne_s3(monkeypatch)
    monkeypatch.setenv("BACKUP_HOUR", "3x")
    monkeypatch.setenv("RESEND_PARALLEL", "viele")
    log = _Log()
    PC.pruefe_produktion(log)
    alles = "\n".join(log.warnungen)
    assert "BACKUP_HOUR='3x'" in alles and "RESEND_PARALLEL='viele'" in alles
    # Bereich 0-23
    monkeypatch.setenv("BACKUP_HOUR", "25")
    monkeypatch.delenv("RESEND_PARALLEL", raising=False)
    log = _Log()
    PC.pruefe_produktion(log)
    alles = "\n".join(log.warnungen)
    assert "BACKUP_HOUR=25" in alles and "0-23" in alles
    # AL-21: geklemmte Werte als Warnung
    K.GEKLEMMT["A7_PROBE"] = ("999", 60)
    try:
        log = _Log()
        PC.pruefe_produktion(log)
        assert any("A7_PROBE=999" in w and "wirksam ist 60" in w for w in log.warnungen)
    finally:
        K.GEKLEMMT.pop("A7_PROBE", None)


def test_al21_betriebsseite_zeigt_geklemmte_werte():
    q = inspect.getsource(_m("routes.admin").admin_betrieb)
    assert '"geklemmte_werte"' in q and "GEKLEMMT" in q


# ====================================================================== redaktion (P-12)
def test_p12_telefon_und_iban_werden_redigiert():
    R = _m("redaktion")
    s = R.redigieren("Verkaeufer 0176 1234567, Buero +49 511 123456, IBAN DE89 3704 0044 0532 0130 00")
    assert "[tel-redigiert]" in s and "1234567" not in s and "123456" not in s
    assert "[iban-redigiert]" in s and "0532" not in s
    assert R.redigieren("Tel: 0511/1234567") == "Tel: [tel-redigiert]"
    assert "[iban-redigiert]" in R.redigieren("DE89370400440532013000")
    # Zeit- und Datumsstempel, Kontonummern, IDs bleiben lesbar
    for unveraendert in ("2026-09-22 03:00:00", "2026-09-22T03:00:00.123456+00:00",
                         "Konto 10023", "Job 0f3a9c", "Preis 0,50", "20260922_0300",
                         "Version 1.2.3", "09-22"):
        assert R.redigieren(unveraendert) == unveraendert, unveraendert


def test_p12_rueckverfolgung_wird_redigiert():
    R = _m("redaktion")
    strom = io.StringIO()
    handler = logging.StreamHandler(strom)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(R.RedaktionsFilter())
    logger = logging.getLogger("a7.p12")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        try:
            raise ValueError("Kunde max@example.com Tel 0176 1234567 token=abcdef123")
        except ValueError:
            logger.exception("kaputt")
    finally:
        logger.removeHandler(handler)
    aus = strom.getvalue()
    assert "Traceback" in aus and "ValueError" in aus
    assert "max@example.com" not in aus and "m***@example.com" in aus
    assert "1234567" not in aus and "[tel-redigiert]" in aus
    assert "abcdef123" not in aus


# ====================================================================== betrieb (AL-16, AL-24)
def test_al24_details_gekuerzt(welt):
    B = _m("betrieb")
    w = welt
    viele = {f"k{i:02d}": i for i in range(30)}
    w.run(B.alarm(w.db, "a7_lang", ref="r", text="x" * 1000, zahl=7, **viele))
    doc = w.run(w.db.betriebsalarme.find_one({"typ": "a7_lang", "offen": True}))
    assert len(doc["details"]["text"]) == 500 and doc["details"]["zahl"] == 7
    assert len(doc["details"]) == B.DETAILS_MAX == 20


def test_al16_ein_offener_alarm_auch_bei_gleichzeitigen_aufrufen(welt):
    B, I = _m("betrieb"), _m("indizes")
    w = welt
    assert w.run(I.unique_anlegen(w.db.betriebsalarme, [("typ", 1), ("ref", 1)],
                                  name="alarm_offen_je_typ_ref", weich=True,
                                  partialFilterExpression={"offen": True})) is True

    async def sturm():
        await asyncio.gather(*[B.alarm(w.db, "a7_sturm", ref="gleich", n=i) for i in range(8)])
    w.run(sturm())
    offen = w.run(w.db.betriebsalarme.find({"typ": "a7_sturm", "ref": "gleich"}).to_list(10))
    assert len(offen) == 1 and offen[0]["anzahl"] == 8, offen
    # geschlossene Alarme stoeren die Eindeutigkeit nicht
    w.run(B.alarm_schliessen(w.db, "a7_sturm", ref="gleich"))
    w.run(B.alarm(w.db, "a7_sturm", ref="gleich"))
    assert w.run(w.db.betriebsalarme.count_documents({"typ": "a7_sturm", "ref": "gleich"})) == 2
    with pytest.raises(DuplicateKeyError):
        w.run(w.db.betriebsalarme.insert_one({"typ": "a7_sturm", "ref": "gleich", "offen": True}))
    q = inspect.getsource(_m("server")._alle_indexe)
    assert 'name="alarm_offen_je_typ_ref"' in q and 'partialFilterExpression={"offen": True}' in q


# ====================================================================== server (SV-05/08/09/11/14/17, AL-17/18/20)
def test_sv08_sicherheitskopfzeilen_liegen_aussen():
    server = _m("server")
    namen = [m.cls.__name__ for m in server.app.user_middleware]
    # user_middleware: zuletzt hinzugefuegte zuerst (= aussen)
    assert namen.index("SecurityHeadersMiddleware") < namen.index("ErrorReportingMiddleware") \
        < namen.index("WartungsmodusMiddleware")
    assert namen.index("CORSMiddleware") < namen.index("SecurityHeadersMiddleware")


def test_sv09_fehlerantwort_wartet_nicht_auf_die_datenbank(monkeypatch):
    server = _m("server")
    from starlette.requests import Request

    class _Langsam:
        async def find_one_and_update(self, *a, **k):
            await asyncio.sleep(4)

        async def estimated_document_count(self):
            return 0

        async def insert_one(self, doc):
            pass

    monkeypatch.setattr(server, "db", SimpleNamespace(error_logs=_Langsam()))
    mw = server.ErrorReportingMiddleware(app=lambda *a: None)

    async def kaputt(_r):
        raise RuntimeError("weg")

    req = Request({"type": "http", "method": "GET", "path": "/api/x", "client": ("1.2.3.4", 1),
                   "query_string": b"", "headers": []})
    t0 = time.monotonic()
    antwort = asyncio.run(mw.dispatch(req, kaputt))
    dauer = time.monotonic() - t0
    assert antwort.status_code == 500 and dauer < 2.5, dauer
    assert server.ErrorReportingMiddleware.DB_ZEITLIMIT_S == 1.0


def _anfrage(pfad="/api/x", headers=None, methode="GET"):
    from starlette.requests import Request
    return Request({"type": "http", "method": methode, "path": pfad, "client": ("203.0.113.9", 1),
                    "query_string": b"", "scheme": "http", "server": ("test", 80),
                    "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]})


def test_sv11_client_errors_429_und_507(monkeypatch):
    server = _m("server")

    class _Sperre:
        async def check(self, key):
            return False
    monkeypatch.setattr(server, "_client_error_limiter", _Sperre())
    body = server.ClientErrorIn(message="kaputt")
    antwort = asyncio.run(server.report_client_error(body, _anfrage()))
    assert antwort.status_code == 429

    class _Frei:
        async def check(self, key):
            return True

    class _Voll:
        async def find_one_and_update(self, *a, **k):
            return None

        async def estimated_document_count(self):
            return 5
    monkeypatch.setattr(server, "_client_error_limiter", _Frei())
    monkeypatch.setattr(server, "db", SimpleNamespace(error_logs=_Voll()))
    monkeypatch.setenv("ERROR_LOG_MAX", "5")
    antwort = asyncio.run(server.report_client_error(body, _anfrage()))
    assert antwort.status_code == 507


def test_sv11_aufraeumlauf_kuerzt_auf_90_prozent(welt, monkeypatch):
    CS = _m("cleanup_service")
    w = welt
    monkeypatch.setattr(CS, "ERROR_LOG_MAX", 100)
    w.run(w.db.error_logs.insert_many([
        {"id": f"e{i}", "status": "resolved", "created_at": _iso(-i)} for i in range(105)]))
    n = w.run(CS.fehlerlogs_begrenzen(w.db, datetime.now(timezone.utc), offen_tage=365))
    assert n == 15 and w.run(w.db.error_logs.count_documents({})) == 90


def test_sv14_platten_pruefung_im_thread():
    server = _m("server")
    fehler, info = server._platten_pruefen()
    assert isinstance(fehler, list) and "frei_mb_uploads" in info
    q = inspect.getsource(server._readiness_pruefen)
    assert "asyncio.wait_for(\n            asyncio.to_thread(_platten_pruefen), 5)" in q \
        or "asyncio.to_thread(_platten_pruefen)" in q
    assert "shutil.disk_usage" not in q, "die blockierende Arbeit liegt in _platten_pruefen"


def test_sv17_al17_quelltext_der_bereitschaft():
    q = inspect.getsource(_m("server")._readiness_pruefen)
    stau = q[q.index("haengend = await db.link_jobs.count_documents("):][:300]
    assert '"active": True' in stau and "**_reif()" in stau
    assert 'alarm_schliessen(db, "datei_speicher_nicht_erreichbar"' in q
    assert '"indizes": "Index-Anlage' in q
    assert 'info["backup_aktiv"]' in q


def test_sv05_worker_erfolg_und_bereitschaft(welt, monkeypatch):
    server = _m("server")
    w = welt
    for mod in ("deps", "server"):
        monkeypatch.setattr(_m(mod), "db", w.db)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    alt = dict(server.WORKER_STATUS)
    server.WORKER_STATUS.clear()
    server.WORKER_STATUS.update({
        "link_jobs": {"laeuft": True, "neustarts": 0, "letzter_fehler": None, "seit": _iso(-3600)},
        "aufraeumen": {"laeuft": True, "neustarts": 0, "letzter_fehler": None, "seit": _iso(-3600)},
        "beweise": {"laeuft": True, "neustarts": 0, "letzter_fehler": None, "seit": _iso(-10)},
    })
    try:
        ergebnis, code = w.run(server._readiness_pruefen())
        assert code == 503
        assert any("link_jobs: kein erfolgreicher Durchlauf" in f for f in ergebnis["fehler"]), ergebnis["fehler"]
        assert not any("aufraeumen" in f for f in ergebnis["fehler"]), "1 h < 3 x 1 h"
        assert not any("beweise" in f for f in ergebnis["fehler"])
        assert ergebnis["backup_aktiv"] is True
        server.worker_erfolg("link_jobs")
        assert server.WORKER_STATUS["link_jobs"]["letzter_erfolg"] > _iso(-5)
        ergebnis, _ = w.run(server._readiness_pruefen())
        assert not any("link_jobs" in f for f in ergebnis["fehler"]), ergebnis["fehler"]
        server.worker_erfolg("gibt_es_nicht")        # wirft nie
    finally:
        server.WORKER_STATUS.clear()
        server.WORKER_STATUS.update(alt)
    assert set(server.WORKER_TAKT_S) == {"link_jobs", "beweise", "aufraeumen"}


def test_sv05_schleifen_stempeln_ihren_erfolg():
    LJ, BS, CS = _m("link_jobs"), _m("beweis_service"), _m("cleanup_service")
    q = inspect.getsource(LJ.run_job_worker_forever)
    assert 'worker_erfolg("link_jobs")' in q.split("except Exception")[0], \
        "der Stempel muss im try stehen — nach dem gelungenen Durchlauf"
    assert 'worker_erfolg("beweise")' in inspect.getsource(BS.run_beweis_worker_forever)
    assert 'worker_erfolg("aufraeumen")' in inspect.getsource(CS.run_cleanup_forever)


def test_al20_backup_aktiv(monkeypatch):
    BK = _m("backup_service")
    monkeypatch.delenv("BACKUP_AKTIV", raising=False)
    assert BK.backup_aktiv() is True
    monkeypatch.setenv("BACKUP_AKTIV", "false")
    assert BK.backup_aktiv() is False
    q = inspect.getsource(_m("server").on_start)
    assert "if backup_aktiv():" in q and 'indexe_im_wartenden=False' in q
    assert 'BETRIEBSBEREIT["indizes"] = False' in q


# ====================================================================== SV-10 /api/files
def _datei_lesen(antwort, loop):
    if hasattr(antwort, "body_iterator"):
        async def sammeln():
            return b"".join([b async for b in antwort.body_iterator])
        return loop.run_until_complete(sammeln())
    return antwort.body


def test_sv10_dateien_stroemen_und_range(welt, tmp_path, monkeypatch):
    server, ST = _m("server"), _m("storage_service")
    w = welt
    monkeypatch.setattr(ST, "storage", ST.LocalDiskStorage(tmp_path))
    klein = b"k" * 100
    gross = bytes(range(256)) * (6 * 1024)          # 1,5 MB, erkennbarer Inhalt
    ST.storage.save("logo/d1/klein.jpg", klein)
    ST.storage.save("logo/d1/gross.mp4", gross)
    assert len(gross) > server.DATEI_STREAM_AB

    a = w.run(server.serve_file("logo/d1/klein.jpg", _anfrage("/api/files/logo/d1/klein.jpg")))
    assert a.status_code == 200 and _datei_lesen(a, w.loop) == klein
    assert a.headers["accept-ranges"] == "bytes" and a.headers["content-type"] == "image/jpeg"
    assert not hasattr(a, "body_iterator"), "klein: wie bisher am Stueck"

    b = w.run(server.serve_file("logo/d1/gross.mp4", _anfrage("/api/files/logo/d1/gross.mp4")))
    assert b.status_code == 200 and hasattr(b, "body_iterator"), "gross: gestroemt"
    assert b.headers["content-length"] == str(len(gross)) and b.headers["content-type"] == "video/mp4"
    assert _datei_lesen(b, w.loop) == gross

    c = w.run(server.serve_file("logo/d1/gross.mp4",
                                _anfrage("/api/files/logo/d1/gross.mp4", {"Range": "bytes=10-19"})))
    assert c.status_code == 206 and c.headers["content-range"] == f"bytes 10-19/{len(gross)}"
    assert c.headers["content-length"] == "10" and _datei_lesen(c, w.loop) == gross[10:20]

    d = w.run(server.serve_file("logo/d1/gross.mp4",
                                _anfrage("/api/files/logo/d1/gross.mp4", {"Range": "bytes=-5"})))
    assert d.status_code == 206 and _datei_lesen(d, w.loop) == gross[-5:]

    e = w.run(server.serve_file("logo/d1/klein.jpg",
                                _anfrage("/api/files/logo/d1/klein.jpg", {"Range": "bytes=90-"})))
    assert e.status_code == 206 and _datei_lesen(e, w.loop) == klein[90:]

    f = w.run(server.serve_file("logo/d1/gross.mp4",
                                _anfrage("/api/files/logo/d1/gross.mp4", {"Range": "bytes=99999999-"})))
    assert f.status_code == 416 and f.headers["content-range"] == f"bytes */{len(gross)}"

    g = w.run(server.serve_file("logo/d1/fehlt.jpg", _anfrage("/api/files/logo/d1/fehlt.jpg")))
    assert g.status_code == 404


def test_sv10_ip_limit_fuer_dateien(welt, tmp_path, monkeypatch):
    server, ST = _m("server"), _m("storage_service")
    monkeypatch.setattr(ST, "storage", ST.LocalDiskStorage(tmp_path))
    ST.storage.save("logo/d1/x.png", b"p")

    class _Sperre:
        async def check(self, key):
            return False
    monkeypatch.setattr(server, "_datei_limiter", _Sperre())
    a = welt.run(server.serve_file("logo/d1/x.png", _anfrage("/api/files/logo/d1/x.png")))
    assert a.status_code == 429 and a.headers["retry-after"] == "60"
    assert server._datei_limiter is not None
    q = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert 'zahl_env("DATEI_LIMIT", 3000' in q


def test_sv10_bereich_lesen():
    server = _m("server")
    assert server._bereich_lesen(None, 100) is None
    assert server._bereich_lesen("bytes=0-9", 100) == (0, 9)
    assert server._bereich_lesen("bytes=90-", 100) == (90, 99)
    assert server._bereich_lesen("bytes=-10", 100) == (90, 99)
    assert server._bereich_lesen("bytes=0-500", 100) == (0, 99)
    assert server._bereich_lesen("bytes=0-9,20-29", 100) is None
    assert server._bereich_lesen("items=0-9", 100) is None
    with pytest.raises(ValueError):
        server._bereich_lesen("bytes=100-", 100)
    with pytest.raises(ValueError):
        server._bereich_lesen("bytes=20-10", 100)


def test_sv10_s3_bloecke_lesen_range_und_schliessen():
    ST = _m("storage_service")
    speicher = object.__new__(ST.S3Storage)
    speicher.bucket = "b"
    gesehen = {}

    class _Body:
        def __init__(self, daten):
            self.daten, self.pos, self.zu = daten, 0, False

        def read(self, n):
            block = self.daten[self.pos:self.pos + n]
            self.pos += n
            return block

        def close(self):
            self.zu = True

    class _Client:
        def get_object(self, Bucket, Key, Range=None):
            gesehen["range"] = Range
            a, b = Range[6:].split("-")
            koerper = _Body(bytes(range(100))[int(a):int(b) + 1])
            gesehen["body"] = koerper
            return {"Body": koerper}

        def head_object(self, Bucket, Key):
            return {"ContentLength": 100}
    speicher.client = _Client()
    assert speicher.groesse("logo/d1/x.jpg") == 100
    assert b"".join(speicher.bloecke("logo/d1/x.jpg", 10, 19, blockgroesse=4)) == bytes(range(10, 20))
    assert gesehen["range"] == "bytes=10-19" and gesehen["body"].zu is True
    # Abbruch des Lesers schliesst den Koerper
    g = speicher.bloecke("logo/d1/x.jpg", 0, 99, blockgroesse=8)
    next(g)
    g.close()
    assert gesehen["body"].zu is True


# ====================================================================== migrationen (SV-12, AL-18)
def test_sv12_al18_wartender_prozess(welt):
    MIG, JL = _m("migrationen"), _m("job_lock")
    w = welt
    w.run(JL.ensure_lock_index(w.db))
    jetzt = datetime.now(timezone.utc)
    w.run(w.db.job_locks.insert_one({"name": MIG._SPERRE, "owner": "fremder-prozess", "token": "t",
                                     "acquired_at": jetzt, "expires_at": jetzt + timedelta(minutes=10)}))
    w.run(w.db.system_flags.update_one({"_id": "schema"}, {"$set": {"version": MIG.ZIEL_VERSION}},
                                       upsert=True))
    aufrufe = []

    async def indexe():
        aufrufe.append(1)

    async def kaputt():
        raise RuntimeError("Index-Anlage scheitert")
    # Web-Worker: keine Indexanlage, Stichprobe schlaegt fehl (Indizes fehlen)
    assert w.run(MIG.ausfuehren_oder_warten(w.db, indexe=indexe, warte_sekunden=1,
                                            indexe_im_wartenden=False)) == "gewartet_mit_fehler"
    assert aufrufe == [], "der wartende Web-Worker legt keine Indizes an (SV-12)"
    assert w.run(MIG.kritische_indizes_fehlen(w.db)) == ["vehicles (dealer_id, id)",
                                                          "kaufvorgaenge (contract_id)"]
    w.run(w.db.vehicles.create_index([("dealer_id", 1), ("id", 1)], unique=True))
    w.run(w.db.kaufvorgaenge.create_index("contract_id", unique=True))
    assert w.run(MIG.ausfuehren_oder_warten(w.db, indexe=indexe, warte_sekunden=1,
                                            indexe_im_wartenden=False)) == "gewartet"
    assert aufrufe == []
    # CLI-Lauf: legt an; ein Fehler ist kein Warnhinweis mehr (AL-18)
    assert w.run(MIG.ausfuehren_oder_warten(w.db, indexe=indexe, warte_sekunden=1)) == "gewartet"
    assert aufrufe == [1]
    assert w.run(MIG.ausfuehren_oder_warten(w.db, indexe=kaputt, warte_sekunden=1)) == "gewartet_mit_fehler"
    q = inspect.getsource(MIG._main)
    assert 'ergebnis == "gewartet_mit_fehler"' in q and "return 78" in q


# ====================================================================== backup (AL-11)
def _synth(base: Path, name: str, inhalt: bytes = b"abc") -> Path:
    p = base / name
    (p / "db").mkdir(parents=True)
    (p / "db" / "users.bson.gz").write_bytes(inhalt)
    m = {"version": 5, "db": "db", "konsistenz": "snapshot", "inkonsistent": "",
         "created_at": _iso(-3600), "collections": {"users": 1},
         "files": {"db/users.bson.gz": {"sha256": hashlib.sha256(inhalt).hexdigest(),
                                        "bytes": len(inhalt)}},
         "unvollstaendig": []}
    (p / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return p


def test_al11_pruefsummen_nachrechnung(tmp_path, monkeypatch):
    if not os.environ.get("DB_NAME"):
        monkeypatch.setenv("DB_NAME", "autoschnell_a7_backupservice")
    BK, BW = _m("backup_service"), _m("backup_bewertung")
    monkeypatch.setattr(BK, "BACKUP_DIR", tmp_path)
    p = _synth(tmp_path, "autoschnell-2026-09-20_0300")
    db = _FakeDb()
    assert BK.pruefsummen_faellig() is True
    erg = asyncio.run(BK.pruefsummen_nachrechnen(db, erzwingen=True))
    assert erg["fehler"] == [] and erg["dateien"] == 1
    assert BK.pruefsummen_faellig() is False, "gerade geprueft"
    assert db.betriebsalarme.calls == []
    # Platte kippt: Datei veraendert sich
    (p / "db" / "users.bson.gz").write_bytes(b"abX")
    assert asyncio.run(BK.pruefsummen_nachrechnen(db)) is None, "nicht faellig -> nichts"
    erg = asyncio.run(BK.pruefsummen_nachrechnen(db, erzwingen=True))
    assert erg["fehler"] == ["Pruefsumme falsch: db/users.bson.gz"]
    m = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
    assert "Pruefsumme falsch" in m["beschaedigt"]
    assert BW.ist_gut(m) is False and "beschaedigt" in BW.unvollstaendig(m)[0]
    info = BK.letztes_backup_info()
    assert info["vollstaendig"] is False and "beschaedigt" in info["hinweis"]
    assert [c[0]["typ"] for c in db.betriebsalarme.calls] == ["backup_beschaedigt"]
    assert db.betriebsalarme.calls[0][0]["ref"] == p.name
    assert BK.pruefsummen_faellig() is False, "kein gutes Backup mehr"
    q = inspect.getsource(BK.run_backup_forever)
    assert "await pruefsummen_nachrechnen(db)" in q


def test_al11_dump_gegenlesen(tmp_path):
    import bson
    BM = _m("backup_mongo")
    daten = b"".join(bson.encode({"a": i, "b": "x" * 10}) for i in range(3))
    pfad = tmp_path / "users.bson.gz"
    with gzip.open(pfad, "wb") as fh:
        fh.write(daten)
    assert BM.bson_dokumente_zaehlen(pfad) == 3
    with gzip.open(tmp_path / "leer.bson.gz", "wb") as fh:
        fh.write(b"")
    assert BM.bson_dokumente_zaehlen(tmp_path / "leer.bson.gz") == 0
    with gzip.open(tmp_path / "kaputt.bson.gz", "wb") as fh:
        fh.write(daten[:-3])
    with pytest.raises(ValueError):
        BM.bson_dokumente_zaehlen(tmp_path / "kaputt.bson.gz")
    log = tmp_path / "backup.log"
    BM.dumps_gegenlesen(tmp_path, {"users": 3}, log)
    with pytest.raises(BM.DumpGegenleseFehler) as e:
        BM.dumps_gegenlesen(tmp_path, {"users": 4, "kaputt": 1}, log)
    assert "users: 3 Dokumente gelesen, 4 geschrieben" in str(e.value) and "kaputt: nicht lesbar" in str(e.value)
    q = inspect.getsource(BM.backup_erstellen)
    assert "dumps_gegenlesen(target, counts, logfile)" in q and "except DumpGegenleseFehler" in q


def test_sk09_schreibpause_merkt_den_verlust(tmp_path, monkeypatch):
    BM = _m("backup_mongo")

    class _Db:
        def __getitem__(self, name):
            return None
    sp = BM.Schreibpause(_Db(), tmp_path / "log")
    sp.kennung = "eigene"
    monkeypatch.setattr(BM.wartung, "verlaengern", lambda *a, **k: True)
    assert sp.noch_gueltig() is True and sp.verloren is False
    monkeypatch.setattr(BM.wartung, "verlaengern", lambda *a, **k: False)
    assert sp.noch_gueltig() is False and sp.verloren is True
    assert sp.noch_gueltig() is False, "einmal verloren bleibt verloren"
    q = inspect.getsource(BM.backup_erstellen)
    assert "if pause and ruhig and schreibpause.noch_gueltig():" in q
    assert "elif pause and ruhig:" in q and "KONSISTENZ_RUECKFALL" in q.split("elif pause and ruhig:")[1][:200]
    assert "self.verloren = True" in inspect.getsource(BM.Schreibpause._verlaengern)


# ====================================================================== Skripte
def test_sk12_restore_probe_ohne_offsite():
    q = (SCRIPTS / "wiederherstellung_testen.py").read_text(encoding="utf-8")
    assert '"BACKUP_S3_BUCKET": ""' in q and '"BACKUP_DATEIEN": "aus"' in q
    assert "env=umgebung" in q
    assert "dump = next((p for p in" in q and "if dump is None:" in q


def test_sk11_inhalt_gleich(tmp_path):
    S = _skript("snapshots_nach_r2")
    pfad = tmp_path / "x.jpg"
    pfad.write_bytes(b"hallo")
    sha = hashlib.sha256(b"hallo").hexdigest()
    md5 = hashlib.md5(b"hallo").hexdigest()
    assert S.inhalt_gleich({"ContentLength": 5, "Metadata": {"sha256": sha}}, pfad, 5, sha) is True
    assert S.inhalt_gleich({"ContentLength": 5, "Metadata": {"sha256": "0" * 64}}, pfad, 5, sha) is False
    assert S.inhalt_gleich({"ContentLength": 5, "ETag": f'"{md5}"'}, pfad, 5, sha) is True
    assert S.inhalt_gleich({"ContentLength": 5, "ETag": f'"{md5}-2"'}, pfad, 5, sha) is False
    assert S.inhalt_gleich({"ContentLength": 4, "Metadata": {"sha256": sha}}, pfad, 5, sha) is False
    assert S.inhalt_gleich({"ContentLength": 5}, pfad, 5, sha) is False
    q = inspect.getsource(S.main)
    assert 'Metadata={"sha256": digest}' in q and "inhalt_gleich(kopf, pfad, groesse, digest)" in q
    assert "speicher.save(key" not in q


def test_sk13_loeschen_nur_mit_erwarteter_anzahl(welt, monkeypatch, capsys):
    A = _skript("alte_kontonummern_loeschen")
    w = welt
    monkeypatch.setattr(_m("deps"), "db", w.db)
    w.run(w.db.users.insert_one({"id": "k1", "role": "b2b_buyer", "kontonummer": "10031",
                                 "company_name": "Alt GmbH"}))
    assert w.run(A._lauf(False)) == 0
    assert w.run(A._lauf(True, None)) == 1
    assert w.run(A._lauf(True, 3)) == 1
    assert w.run(w.db.users.count_documents({"id": "k1"})) == 1, "nichts geloescht"
    aus = capsys.readouterr().out
    assert "Ziel:" in aus and "Datenbank:" in aus and "--erwartet 1" in aus
    assert A._verschleiert("mongodb://nutzer:geheim@host:27017/") == "mongodb://nutzer:***@host:27017/"
    assert "--erwartet" in inspect.getsource(A.main)


def test_sk14_fragmente_gesichert_und_gruppiert():
    S = _skript("schaeden_freitext_bereinigen")
    behalten, weg = S.eintrag_zerlegen("• Rost: Schweller links • Max Mustermann war dabei")
    assert behalten == ["Rost: Schweller links"] and weg == ["Max Mustermann war dabei"]
    assert S.eintrag_bereinigen("• Rost: Schweller links • Max Mustermann war dabei") == \
        (["Rost: Schweller links"], 1)
    neu, weg = S.datensatz_zerlegen(["Kratzer", "Fremdtext A; Fremdtext B", "Kratzer"])
    assert neu == ["Kratzer"] and weg == ["Fremdtext A", "Fremdtext B"]
    assert S.SICHERUNG_COLLECTION == "schaeden_freitext_entfernt" and S.SICHERUNG_TAGE == 90
    q = inspect.getsource(S.main)
    assert "unbekannt.most_common(args.zeige)" in q
    assert "db[SICHERUNG_COLLECTION].insert_one(" in q and "db.activity_logs.insert_one(" in q
    assert "expireAfterSeconds=SICHERUNG_TAGE * 86400" in inspect.getsource(S._sicherung_vorbereiten)


def test_sk17_sk19_restore_ohne_terminal_und_keep_old():
    q = (SCRIPTS / "restore_mongo.py").read_text(encoding="utf-8")
    stelle = q[q.index("if not args.yes:"):][:900]
    assert "sys.stdin.isatty()" in stelle and "except EOFError" in stelle and "--yes" in stelle
    assert "ohne Wirkung" in q[q.index('"--keep-old"'):][:300]


def test_sk18_zugangsdaten_nicht_als_argument(monkeypatch, capsys):
    VP = _skript("verbindung_pruefen")
    assert VP.zugangsdaten_im_argument("mongodb://nutzer:pw@host:27017/?x=1") is True
    assert VP.zugangsdaten_im_argument("mongodb+srv://nutzer@cluster.mongodb.net/") is True
    assert VP.zugangsdaten_im_argument("mongodb://host:27017/") is False
    monkeypatch.setattr(sys, "argv", ["verbindung_pruefen.py", "mongodb://nutzer:pw@host:27017/"])
    assert VP.main() == 1
    aus = capsys.readouterr().out
    assert "Zugangsdaten" in aus and "pw" not in aus.replace("passwort", "")
    assert "nutzer:passwort@cluster" not in VP.__doc__


def test_sk21_webp_bleibt_liegen_und_original_gesichert():
    B = _skript("bilder_verkleinern_nachtraeglich")
    assert ".webp" not in B.BILD_ENDUNGEN and B.SICHERUNGS_PRAEFIX == "verkleinert-original/"
    q = inspect.getsource(B.main)
    assert "storage.save(SICHERUNGS_PRAEFIX + key, roh)" in q and "--ohne-sicherung" in q
    ST = _m("storage_service")
    ST._validate_key(B.SICHERUNGS_PRAEFIX + "resale/d1/abc.jpg")   # gueltiger Schluessel


# ====================================================================== Konfiguration (AL-20, SV-10, AL-11)
def test_neue_variablen_erreichen_den_container():
    import yaml
    daten = yaml.safe_load((WURZEL / "docker-compose.yml").read_text(encoding="utf-8"))
    umgebung = {str(z).split("=", 1)[0].strip()
                for z in (daten["services"]["backend"].get("environment") or [])}
    for name in ("BACKUP_AKTIV", "BACKUP_PRUEFSUMMEN_TAGE", "DATEI_LIMIT"):
        assert name in umgebung, name
    vorlage = (WURZEL / ".env.example").read_text(encoding="utf-8")
    assert "BACKUP_AKTIV=true" in vorlage and "DATEI_LIMIT=3000" in vorlage \
        and "BACKUP_PRUEFSUMMEN_TAGE=7" in vorlage
    assert "BACKUP_AKTIV=false" in (WURZEL / "DEPLOYMENT.md").read_text(encoding="utf-8")
