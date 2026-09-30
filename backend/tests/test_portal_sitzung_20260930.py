# -*- coding: utf-8 -*-
"""Pruefliste 30.09.2026 (Kundenportal, vor dem ersten Rollout):

  * Die Sitzung des Kunden gilt nur fuer GENAU den Code, mit dem sie entstand — ein zurueckgezogener
    Code lebt nicht wieder auf, wenn fuer dieselbe Fassung ein neuer erzeugt wird.
  * Firmenseite abgeschaltet, Firma gesperrt (gesperrter Chef) oder in Loeschung: Seite, Code-Eingabe
    UND laufende Sitzungen enden.
  * Die Sitzung reist in der Kopfzeile X-Portal-Sitzung, nicht mehr in der Adresse.
  * Die Bremsen zaehlen nur Fehlversuche: zehn richtige Codes aus demselben WLAN sperren niemanden aus;
    ein richtiger Code loescht aber auch keine Fehlversuche.
  * Scheitert die Datenbank NACH dem Speichern einer Datei (Bild, Chef-Unterschrift, Kunden-Unterschrift),
    bleibt keine Datei ohne Verweis liegen.
"""
import pytest

import rate_limiter
import routes.kundenportal as KP

from test_kundenportal_20260929 import _b64, _fehler, _lauf, _png, _request, _vertrag, welt  # noqa: F401


def _seite_an(w):
    _lauf(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller", aktiv=True), user=w.chef))


def _oeffnen(code, ip="203.0.113.7"):
    return _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=code, slug="kfz-mueller"), _request(ip)))["sitzung"]


def _unterschrift(name="Erika Mustermann", ok=True):
    return KP.UnterschreibenIn(signature_b64=_b64(_png()), name=name, einverstanden=ok)


def test_sitzung_gilt_nur_fuer_ihren_code(welt):  # noqa: F811
    w = welt
    _seite_an(w)
    c = _vertrag(w, w.sucher)
    code_a = _lauf(KP.portal_freigeben(c["id"], user=w.sucher))["code"]
    sitzung_a = _oeffnen(code_a)
    assert _lauf(KP.portal_sitzung(sitzung_a))["vertrag"]["status"] == "offen"
    # zurueckgezogen: tot
    _lauf(KP.portal_zurueckziehen(c["id"], user=w.sucher))
    assert _fehler(KP.portal_sitzung(sitzung_a)).status_code == 410
    # neuer Code B fuer DIESELBE Fassung: die alte Sitzung bleibt tot (vorher lebte sie wieder auf)
    code_b = _lauf(KP.portal_freigeben(c["id"], user=w.sucher))["code"]
    doc = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "portal": 1, "version": 1}))
    assert doc["portal"]["status"] == "offen" and doc["portal"]["code"] == code_b and doc["version"] == 1
    for aufruf in (KP.portal_sitzung(sitzung_a), KP.portal_sitzung_pdf(sitzung_a),
                   KP.portal_unterschreiben(sitzung_a, _unterschrift(), _request())):
        f = _fehler(aufruf)
        assert f.status_code == 410 and "zurückgezogen oder ersetzt" in f.detail
    assert w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "portal.status": 1}))["portal"]["status"] == "offen"
    # selbst bei zufaellig GLEICHEM Code gilt die alte Sitzung nicht (Zeitpunkt der Freigabe zaehlt mit)
    assert KP._portal_kennung({"code": "ABCDEF", "erstellt_am": "1"}) != KP._portal_kennung({"code": "ABCDEF", "erstellt_am": "2"})
    # die Sitzung von Code B arbeitet normal — bis zur Unterschrift und danach (unterschriebenes PDF)
    sitzung_b = _oeffnen(code_b)
    assert _lauf(KP.portal_sitzung_pdf(sitzung_b)).body[:4] == b"%PDF"
    erg = _lauf(KP.portal_unterschreiben(sitzung_b, _unterschrift(), _request()))
    assert erg["ok"] and "sitzung" not in erg                  # die Sitzung wandert nicht mehr durch Antworten
    assert _lauf(KP.portal_sitzung(sitzung_b))["pdf_signiert"] is True
    # ein Token ohne Kennung (alte Bauart) gilt nicht
    import jwt as _jwt
    import auth as _auth
    from datetime import datetime, timedelta, timezone
    alt = _jwt.encode({"typ": "portal", "cid": c["id"], "v": 1, "d": w.dealer_id,
                       "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, _auth.JWT_SECRET, algorithm=_auth.JWT_ALG)
    assert _fehler(KP.portal_sitzung(alt)).status_code == 410
    assert _fehler(KP.portal_sitzung("")).status_code == 401


def test_firmenseite_aus_oder_firma_gesperrt_beendet_sitzungen(welt):  # noqa: F811
    w = welt
    _seite_an(w)
    c = _vertrag(w, w.sucher)
    code = _lauf(KP.portal_freigeben(c["id"], user=w.sucher))["code"]
    sitzung = _oeffnen(code)

    def alles_zu():
        assert _fehler(KP.public_firma(slug="kfz-mueller")).status_code == 404
        assert _fehler(KP.portal_oeffnen(KP.OeffnenIn(code=code, slug="kfz-mueller"), _request())).status_code == 404
        for aufruf in (KP.portal_sitzung(sitzung), KP.portal_sitzung_pdf(sitzung),
                       KP.portal_unterschreiben(sitzung, _unterschrift(), _request())):
            f = _fehler(aufruf)
            assert f.status_code == 410 and "nicht erreichbar" in f.detail

    def alles_offen():
        assert _lauf(KP.public_firma(slug="kfz-mueller"))["firma"] == "KFZ Müller GmbH"
        assert _lauf(KP.portal_sitzung(sitzung))["vertrag"]["status"] == "offen"

    alles_offen()
    # 1) Firmenseite abgeschaltet
    _lauf(KP.put_webseite(KP.WebseiteIn(aktiv=False), user=w.chef))
    alles_zu()
    _lauf(KP.put_webseite(KP.WebseiteIn(aktiv=True), user=w.chef))
    alles_offen()
    # 2) Firma gesperrt (gesperrter Chef = gesperrte Firma)
    w.run(w.db.users.update_one({"id": w.chef["id"]}, {"$set": {"active": False}}))
    alles_zu()
    w.run(w.db.users.update_one({"id": w.chef["id"]}, {"$set": {"active": True}}))
    alles_offen()
    # 3) Firma in Loeschung
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$set": {"loeschung": {"status": "laeuft"}}}))
    alles_zu()
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$unset": {"loeschung": ""}}))
    alles_offen()
    # danach laesst sich normal unterschreiben — nichts wurde in der Zwischenzeit veraendert
    assert _lauf(KP.portal_unterschreiben(sitzung, _unterschrift(), _request()))["ok"]


def test_sitzung_in_der_kopfzeile_nicht_in_der_adresse():
    pfade = {r.path: r for r in KP.router.routes}
    for pfad in ("/public/portal/vertrag", "/public/portal/vertrag/pdf", "/public/portal/vertrag/unterschreiben"):
        kopf = pfade[pfad].dependant.header_params
        assert [p.alias for p in kopf] == ["X-Portal-Sitzung"], (pfad, kopf)
        assert not pfade[pfad].dependant.path_params
    assert not [p for p in pfade if "portal" in p and "{sitzung}" in p]


def test_bremse_zaehlt_nur_fehlversuche(welt, monkeypatch):  # noqa: F811
    w = welt
    _seite_an(w)
    c = _vertrag(w, w.sucher)
    code = _lauf(KP.portal_freigeben(c["id"], user=w.sucher))["code"]
    monkeypatch.setattr(rate_limiter, "_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(KP, "_code_limiter_ip", rate_limiter.SlidingWindowRateLimiter(
        max_attempts=10, window_seconds=600, name=f"portal_ip_{w.s}", fail_closed=True))
    monkeypatch.setattr(KP, "_code_limiter_firma", rate_limiter.SlidingWindowRateLimiter(
        max_attempts=200, window_seconds=600, name=f"portal_firma_{w.s}"))
    falsch = KP.OeffnenIn(code="ZZZZZZ", slug="kfz-mueller")
    # 25 richtige Codes aus demselben WLAN: niemand wird ausgesperrt (vorher 429 ab dem elften)
    for _ in range(25):
        assert _oeffnen(code, "192.0.2.50")
    assert _lauf(KP._code_limiter_ip.stand("192.0.2.50")) == 0
    assert _lauf(KP._code_limiter_firma.stand(f"firma:{w.dealer_id}")) == 0
    # Fehlversuche zaehlen weiter — und ein richtiger Code dazwischen loescht sie NICHT
    for _ in range(9):
        assert _fehler(KP.portal_oeffnen(falsch, _request("192.0.2.51"))).status_code == 404
    for _ in range(5):
        assert _oeffnen(code, "192.0.2.51")
    assert _lauf(KP._code_limiter_ip.stand("192.0.2.51")) == 9
    assert _fehler(KP.portal_oeffnen(falsch, _request("192.0.2.51"))).status_code == 404      # der zehnte
    assert _fehler(KP.portal_oeffnen(KP.OeffnenIn(code=code, slug="kfz-mueller"), _request("192.0.2.51"))).status_code == 429
    assert _oeffnen(code, "192.0.2.52")                                  # andere Adresse unberuehrt
    assert _lauf(KP._code_limiter_firma.stand(f"firma:{w.dealer_id}")) == 10

    # Unterschrift: erfolgreiche Abschluesse zaehlen nicht, abgelehnte schon
    monkeypatch.setattr(KP, "_unterschrift_limiter", rate_limiter.SlidingWindowRateLimiter(
        max_attempts=2, window_seconds=600, name=f"portal_sig_{w.s}"))
    sitzungen = []
    for _ in range(4):
        v = _vertrag(w, w.sucher)
        sitzungen.append(_oeffnen(_lauf(KP.portal_freigeben(v["id"], user=w.sucher))["code"], "192.0.2.60"))
    for s in sitzungen[:3]:                                              # drei Kunden, eine Adresse, Grenze 2
        assert _lauf(KP.portal_unterschreiben(s, _unterschrift(), _request("192.0.2.61")))["ok"]
    assert _lauf(KP._unterschrift_limiter.stand("192.0.2.61")) == 0
    for _ in range(2):                                                   # zwei abgelehnte Versuche
        assert _fehler(KP.portal_unterschreiben(sitzungen[3], _unterschrift(ok=False), _request("192.0.2.61"))).status_code == 400
    assert _fehler(KP.portal_unterschreiben(sitzungen[3], _unterschrift(), _request("192.0.2.61"))).status_code == 429
    assert _lauf(KP.portal_unterschreiben(sitzungen[3], _unterschrift(), _request("192.0.2.62")))["ok"]


class _SammlungKaputt:
    def __init__(self, coll):
        self._coll = coll

    def __getattr__(self, name):
        return getattr(self._coll, name)

    async def update_one(self, *a, **k):
        raise RuntimeError("Datenbank weg")


class _DbMitFehler:
    """Wie die echte Datenbank, nur update_one auf EINER Sammlung scheitert."""

    def __init__(self, db, sammlung):
        self._db, self._sammlung = db, sammlung

    def __getattr__(self, name):
        coll = getattr(self._db, name)
        return _SammlungKaputt(coll) if name == self._sammlung else coll

    def __getitem__(self, name):
        return self.__getattr__(name)


def test_keine_datei_ohne_verweis_wenn_die_datenbank_scheitert(welt, monkeypatch):  # noqa: F811
    w = welt
    _seite_an(w)
    c = _vertrag(w, w.sucher)
    sitzung = _oeffnen(_lauf(KP.portal_freigeben(c["id"], user=w.sucher))["code"])
    echt = KP.db
    assert not w.ablage

    # 1) Bild der Firmenseite
    monkeypatch.setattr(KP, "db", _DbMitFehler(echt, "dealers"))
    with pytest.raises(RuntimeError):
        _lauf(KP.bild_hochladen(KP.BildIn(bild_b64=_b64(_png())), user=w.chef))
    assert not w.ablage, sorted(w.ablage)
    # 2) hinterlegte Unterschrift des Chefs
    with pytest.raises(RuntimeError):
        _lauf(KP.unterschrift_hochladen(KP.UnterschriftIn(bild_b64=_b64(_png())), user=w.chef))
    assert not w.ablage, sorted(w.ablage)
    # 3) Unterschrift des Kunden
    monkeypatch.setattr(KP, "db", _DbMitFehler(echt, "generated_pdfs"))
    with pytest.raises(RuntimeError):
        _lauf(KP.portal_unterschreiben(sitzung, _unterschrift(), _request()))
    assert not w.ablage, sorted(w.ablage)
    doc = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "portal": 1, "kunde_unterschrieben_am": 1}))
    assert doc["portal"]["status"] == "offen" and "kunde_unterschrieben_am" not in doc

    # Datenbank wieder da: alle drei Wege arbeiten normal, die Dateien haben ihren Verweis
    monkeypatch.setattr(KP, "db", echt)
    st = _lauf(KP.bild_hochladen(KP.BildIn(bild_b64=_b64(_png())), user=w.chef))
    assert st["webseite"]["bilder"][0]["key"] in w.ablage
    assert _lauf(KP.unterschrift_hochladen(KP.UnterschriftIn(bild_b64=_b64(_png())), user=w.chef))["unterschrift_vorhanden"]
    assert _lauf(KP.portal_unterschreiben(sitzung, _unterschrift(), _request()))["ok"]
    doc = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "portal": 1}))
    assert doc["portal"]["unterschrift_key"] in w.ablage and len(w.ablage) == 3


# ------------------------------------------------------------------ Wunsch Ahmad 30.09.2026
def test_500_je_minute_je_adresse_dann_zwei_minuten_warten(welt, monkeypatch):  # noqa: F811
    """Ueber dieselbe Adresse 500 Aufrufe je Minute (Code eingeben, unterschreiben, Codes erzeugen),
    danach 2 Minuten warten. Geprueft mit kleinen Zahlen; die echten Werte stehen in den Konstanten."""
    import time as _time
    w = welt
    assert KP.PORTAL_IP_JE_MINUTE == 500 and KP.PORTAL_IP_WARTEN_S == 120
    for echt in (KP._portal_limiter_ip, KP._erzeugen_limiter_ip):
        assert (echt.max_attempts, echt.window_seconds, echt.sperre_sekunden) == (500, 60, 120)
    _seite_an(w)
    vertraege = [_vertrag(w, w.sucher) for _ in range(5)]
    code = _lauf(KP.portal_freigeben(vertraege[0]["id"], user=w.sucher))["code"]
    monkeypatch.setattr(rate_limiter, "_RATE_LIMIT_ENABLED", True)
    name = f"portal_min_{w.s}"
    monkeypatch.setattr(KP, "_portal_limiter_ip", rate_limiter.SlidingWindowRateLimiter(
        max_attempts=5, window_seconds=60, name=name, sperre_sekunden=120))
    for n, grenze in (("_code_limiter_ip", 10), ("_code_limiter_firma", 200), ("_unterschrift_limiter", 20)):
        monkeypatch.setattr(KP, n, rate_limiter.SlidingWindowRateLimiter(
            max_attempts=grenze, window_seconds=600, name=f"{n}_{w.s}"))
    ip = "192.0.2.70"
    sitzungen = [_oeffnen(code, ip) for _ in range(5)]                      # fuenf Aufrufe: alle durch
    f = _fehler(KP.portal_oeffnen(KP.OeffnenIn(code=code, slug="kfz-mueller"), _request(ip)))
    assert f.status_code == 429 and "in 2 Minuten" in f.detail and f.headers["Retry-After"] == "120"
    sperre = {"_id": f"{name}:sperre:{ip}"}
    bis = w.run(w.db.rate_limits.find_one(sperre))["bis"]
    assert 115 < bis - _time.time() <= 120
    # waehrend der Wartezeit: weiter 429, auch fuer die Unterschrift — und die Wartezeit verlaengert sich nicht
    assert _fehler(KP.portal_oeffnen(KP.OeffnenIn(code=code, slug="kfz-mueller"), _request(ip))).status_code == 429
    g = _fehler(KP.portal_unterschreiben(sitzungen[0], _unterschrift(), _request(ip)))
    assert g.status_code == 429 and "in 2 Minuten" in g.detail
    assert w.run(w.db.rate_limits.find_one(sperre))["bis"] == bis
    assert w.run(w.db.generated_pdfs.find_one({"id": vertraege[0]["id"]}, {"_id": 0, "portal.status": 1}))["portal"]["status"] == "offen"
    assert _oeffnen(code, "192.0.2.71")                                     # andere Adresse unberuehrt
    # Wartezeit vorbei: wieder die volle Menge (hier 5), dann erneut Pause
    w.run(w.db.rate_limits.update_one(sperre, {"$set": {"bis": _time.time() - 1}}))
    for _ in range(4):
        assert _oeffnen(code, ip)
    assert _lauf(KP.portal_unterschreiben(sitzungen[0], _unterschrift(), _request(ip)))["ok"]     # der fuenfte
    assert _fehler(KP.portal_oeffnen(KP.OeffnenIn(code=code, slug="kfz-mueller"), _request(ip))).status_code == 429

    # Codes erzeugen (Sucher/Chef): dieselbe Regel je Adresse
    monkeypatch.setattr(KP, "_erzeugen_limiter_ip", rate_limiter.SlidingWindowRateLimiter(
        max_attempts=3, window_seconds=60, name=f"portal_erz_{w.s}", sperre_sekunden=120))
    buero = "192.0.2.80"
    for v in vertraege[1:4]:
        assert _lauf(KP.portal_freigeben(v["id"], request=_request(buero), user=w.sucher))["status"] == "offen"
    h = _fehler(KP.portal_freigeben(vertraege[4]["id"], request=_request(buero), user=w.sucher))
    assert h.status_code == 429 and "in 2 Minuten" in h.detail
    assert _lauf(KP.portal_freigeben(vertraege[4]["id"], request=_request("192.0.2.81"), user=w.chef))["status"] == "offen"
