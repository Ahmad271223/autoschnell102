# -*- coding: utf-8 -*-
"""Kostendeckel je KI-Lauf — Nachbesserung nach der Pruefer-Runde 27.09.2026.
Nie ein echter Anthropic-Aufruf: Attrappe des Clients aus
test_ki_kostendeckel_20260927 (messages.create UND count_tokens), Preise fest.

(L) Lease: ein langer Lauf verlaengert sein Lease vor jedem bezahlten Aufruf
    — kein zweiter bezahlter Lauf fuer denselben Stand; ein uebernommener
    Lauf bricht vor dem naechsten Aufruf ab
(U) unsichere Aufrufe (Zeitlimit) zaehlen fuer Abrechnung, Dokument und Alarm
(R) 429/529/Verbindungsaufbau: ein kostenloser zweiter Versuch, "ueberlastet",
    nichts gebunden; Zeitlimit: kein zweiter Versuch
(M) Modell ohne Preis: kein Aufruf; Buchung unbekannter Modelle zum Hoechstpreis
(F) Fortsetzung nach pause_turn ohne weitere Suche
(A) Ausnahmepfad: Kosten bleiben am Dokument, budget.abgleichen verliert sie nicht
(N) Normalfall: Rechnung je Lauf (zwei Runden, erwartet <= Ziel, Obergrenze <= hart)
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_ki_abholbewertung_20260925 import _welt_aufbauen  # noqa: E402
from test_ki_kostendeckel_20260927 import (HAIKU, RECHERCHE_TEXT, SONNET, Klient, _abholung_daten,  # noqa: E402,F401
                                           _alarme, _alarme_weg, _antwort, _bewertung_fest, _kasse, _recherche_lauf,
                                           _usage, _vertrag_aufraeumen, _wahre_tokens, ki)
from test_ki_vertrag_20260926 import ANTWORT as ANTWORT_VERTRAG, SCHAEDEN, _fahrzeug  # noqa: E402


# Werte fuer BEIDE Positionen -> keine zweite Suchrunde (feste Kosten 4 bzw. 5,75 ct)
RECHERCHE_BEIDE = ("Delle 120-200 EUR (ADAC). Kratzer 150-250 EUR (FairGarage).\n###DATEN\n"
                   "d1|120|200|160|ADAC|https://www.adac.de/x\nd2|150|250|200|FairGarage|https://www.fairgarage.com/y\n")


def _req():
    import httpx2
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_fehler(klasse, code):
    import anthropic
    import httpx2
    return getattr(anthropic, klasse)(f"Error code: {code}", response=httpx2.Response(code, request=_req()), body=None)


def _zeitlimit():
    import anthropic
    return anthropic.APITimeoutError(request=_req())


def _verbindung_nie_aufgebaut():
    import anthropic
    import httpx2
    try:
        try:
            raise httpx2.ConnectError("connect failed")
        except Exception as inner:
            raise anthropic.APIConnectionError(request=_req()) from inner
    except anthropic.APIConnectionError as exc:
        return exc


class LangsamerKlient(Klient):
    """Wie Klient; vor der ERSTEN Recherche laeuft `waehrend` (async) —
    z. B. ein zweiter Aufruf fuer denselben Stand, waehrend der erste rechnet."""

    def __init__(self, *a, waehrend=None, **kw):
        super().__init__(*a, **kw)
        self.waehrend, self.nebenbei = waehrend, []

    async def create(self, **kw):
        if kw.get("tools") and self.waehrend is not None:
            f, self.waehrend = self.waehrend, None
            self.nebenbei.append(await f())
        return await super().create(**kw)


def _zaehler(welt, art="vertrag"):
    B = _module("ai.budget")
    w = welt.w
    return welt.run(B.zaehler_ct(user_id=w.sucher["id"], dealer_id=w.dealer_id, art=art))


# ------------------------------------------------ (L) Lease
def test_l1_abholung_langer_lauf_kein_zweiter_bezahlter_lauf(welt, ki, monkeypatch):
    """Pruefer: LEASE_S=150 s, ein Lauf dauert bis ~550 s; die Karte fragt
    alle 3 s nach und startete nach Ablauf einen ZWEITEN bezahlten Lauf.
    Nachgestellt mit LEASE_S=1 und 1,5 s Recherche: der zweite Aufruf
    bekommt 'laeuft', nur EINE Bewertung wird bezahlt."""
    K = _module("ai.pickup_assessment")
    monkeypatch.setattr(K, "LEASE_S", 1)
    w = welt.w
    _vertrag_aufraeumen(welt)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "kdl1")

    async def zweiter_aufruf():
        await asyncio.sleep(1.5)                 # Lease von 1 s waere abgelaufen
        return await K.bewertung_ausfuehren(pid, w.dealer_id)
    kl = ki(LangsamerKlient(recherche=lambda kw: _antwort(RECHERCHE_TEXT, _usage(ein=24000, aus=1200, suchen=1)),
                            bewertung=_bewertung_fest(_abholung_daten, ein=5000, aus=1800, cache_schreiben=3900),
                            zaehlen=_wahre_tokens, waehrend=zweiter_aufruf))
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "ok"
    assert kl.nebenbei and kl.nebenbei[0]["status"] == "laeuft", "zweiter Aufruf darf nicht uebernehmen"
    assert len(kl.bewertungen) == 1, "nur EIN bezahlter Lauf"
    doc = welt.run(welt.db.ki_bewertungen.find_one({"protocol_id": pid}, {"_id": 0}))
    assert doc["status"] == "ok" and doc["kostendeckel"]["uebernommen"] is False
    _vertrag_aufraeumen(welt)


def test_l2_vertrag_lesen_und_zweiter_klick_waehrend_langem_lauf(welt, ki, monkeypatch):
    """Vertrag: lesen() zeigte nach LEASE_S 'abgebrochen — bitte erneut
    versuchen', der neue Klick uebernahm den Lauf (zweimal bezahlt). Jetzt
    'laeuft' und der zweite Klick bekommt den laufenden Lauf."""
    D = _module("ai.damage_pricing")
    K = _module("ai.pickup_assessment")
    monkeypatch.setattr(K, "LEASE_S", 1)
    monkeypatch.setattr(D, "LEASE_S", 1)
    w = welt.w
    _vertrag_aufraeumen(welt)
    vid = f"v_kdl2_{w.s}"
    fz = _fahrzeug(welt, vid)

    async def waehrenddessen():
        await asyncio.sleep(1.5)
        laufend = await welt.db.ki_bewertungen.find_one({"vehicle_id": vid, "status": "laeuft"}, {"_id": 0})
        gelesen = await D.lesen(laufend["id"], w.dealer_id)
        zweiter = await D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN)
        return {"gelesen": gelesen["status"], "zweiter": zweiter["status"], "zweiter_id": zweiter["id"],
                "id": laufend["id"]}
    kl = ki(LangsamerKlient(recherche=lambda kw: _antwort(RECHERCHE_TEXT, _usage(ein=24000, aus=1200, suchen=1)),
                            bewertung=_bewertung_fest(ANTWORT_VERTRAG, ein=4000, aus=1400, cache_schreiben=3500),
                            zaehlen=_wahre_tokens, waehrend=waehrenddessen))
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    neben = kl.nebenbei[0]
    assert neben["gelesen"] == "laeuft", "kein 'abgebrochen' waehrend der Lauf noch rechnet"
    assert neben["zweiter"] == "laeuft" and neben["zweiter_id"] == neben["id"], "zweiter Klick uebernimmt nicht"
    assert erg["status"] == "ok" and erg["id"] == neben["id"]
    assert len(kl.bewertungen) == 1
    _vertrag_aufraeumen(welt)


def test_l3_uebernommener_lauf_bricht_vor_dem_naechsten_aufruf_ab(welt, ki):
    """Hat ein anderer Aufruf den Lauf uebernommen (Dokument hat eine andere
    id), macht der alte Lauf KEINEN weiteren bezahlten Aufruf; das schon
    Ausgegebene wird abgerechnet (Budget: nicht die 20 ct Reservierung)."""
    D = _module("ai.damage_pricing")
    w = welt.w
    _vertrag_aufraeumen(welt)
    vid = f"v_kdl3_{w.s}"
    fz = _fahrzeug(welt, vid)

    async def uebernehmen():
        await welt.db.ki_bewertungen.update_one({"vehicle_id": vid, "status": "laeuft"},
                                                {"$set": {"id": f"fremd_{w.s}"}})
        return True
    kl = ki(LangsamerKlient(recherche=lambda kw: _antwort(RECHERCHE_TEXT, _usage(ein=24000, aus=1200, suchen=1)),
                            bewertung=_bewertung_fest(ANTWORT_VERTRAG, ein=4000, aus=1400),
                            zaehlen=_wahre_tokens, waehrend=uebernehmen))
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert kl.bewertungen == [], "nach der Uebernahme keine bezahlte Bewertung mehr"
    assert len(kl.recherchen) == 1, "auch keine zweite Suchrunde"
    assert erg["status"] == "fehler"
    assert _zaehler(welt) == pytest.approx(4.0, abs=0.02), "abgerechnet: die eine Recherche (4 ct)"
    _vertrag_aufraeumen(welt)


# ------------------------------------------------ (U) unsichere Aufrufe
def test_u1_zeitlimit_der_bewertung_zaehlt_fuer_abrechnung_und_dokument(welt, ki):
    """Pruefer: Recherche 5,75 ct gebucht, Bewertung mit APITimeoutError
    (Obergrenze ~6 ct) nur fuer den Deckel gebunden, abgerechnet 5,75. Jetzt
    zaehlt die Obergrenze der unsicheren Anfrage auch fuer Budget/Dokument."""
    D = _module("ai.damage_pricing")
    w = welt.w
    _vertrag_aufraeumen(welt)
    vid = f"v_kdu1_{w.s}"
    _alarme_weg(welt, vid)
    fz = _fahrzeug(welt, vid)

    def _haengt(kw):
        raise _zeitlimit()
    kl = ki(Klient(recherche=lambda kw: _antwort(RECHERCHE_BEIDE, _usage(ein=40000, aus=1500, suchen=1)),
                   bewertung=_haengt, zaehlen=_wahre_tokens))
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["status"] == "zeitlimit" and len(kl.bewertungen) == 1, "kein zweiter Versuch nach Zeitlimit"
    doc = welt.run(welt.db.ki_bewertungen.find_one({"id": erg["id"]}, {"_id": 0}))
    kd = doc["kostendeckel"]
    assert kd["unsicher_ct"] > 4 and kd["kosten_sicher_ct"] == pytest.approx(5.75, abs=0.01)
    assert doc["kosten_ct"] == pytest.approx(kd["kosten_sicher_ct"] + kd["unsicher_ct"], abs=0.02)
    assert kd["kosten_ct"] == doc["kosten_ct"] and doc["kosten_summe_ct"] == pytest.approx(doc["kosten_ct"], abs=0.01)
    assert _zaehler(welt) == pytest.approx(doc["kosten_ct"], abs=0.02), "Budget sieht die unsichere Anfrage"
    _alarme_weg(welt, vid)
    _vertrag_aufraeumen(welt)


def test_u2_alarm_vergleicht_sicher_plus_unsicher(ki):
    kasse = _kasse()
    kasse.buchen("recherche#1", HAIKU, {"input_tokens": 120000, "output_tokens": 1000, "web_search_requests": 1})
    kasse.unsicher_buchen("bewertung", 6.5, "APITimeoutError")
    kosten = asyncio.new_event_loop().run_until_complete(kasse.abschliessen())
    assert kasse.kosten_ct == pytest.approx(13.5) and kosten == pytest.approx(20.0)
    kasse2 = _kasse()
    kasse2.buchen("recherche#1", HAIKU, {"input_tokens": 125000, "output_tokens": 1000, "web_search_requests": 1})
    kasse2.unsicher_buchen("bewertung", 6.5, "APITimeoutError")
    asyncio.new_event_loop().run_until_complete(kasse2.abschliessen())
    al = [a for a in kasse2.alarme if a[0] == "ki_kosten_ueberschritten"]
    assert len(al) == 1 and al[0][1]["unsicher_ct"] == 6.5 and al[0][1]["kosten_sicher_ct"] == pytest.approx(14.0)


# ------------------------------------------------ (R) kostenlose Wiederholung
def _bewerten(kasse, fn):
    P = _module("ai.provider")
    S = _module("ai.schemas")
    D = _module("ai.damage_pricing")
    return asyncio.new_event_loop().run_until_complete(
        kasse.bewerten(P.json_bewerten, modell=SONNET, system=D.SYSTEM_PROMPT, nutzer={"damages": [{"id": "d1"}]},
                       schema=S.ANTWORT_SCHEMA, zusatz_teile=["Marktpreise"], fall_texte=[""], max_tokens=P.KI_MAX_TOKENS))


def _folge(*schritte):
    """Bewertungs-Attrappe: nacheinander Ausnahme werfen oder antworten."""
    it = iter(schritte)

    def f(kw):
        s = next(it)
        if isinstance(s, BaseException):
            raise s
        return _antwort(json.dumps({"items": []}), _usage(ein=3000, aus=500))
    return f


@pytest.mark.parametrize("fehler", ["529", "429", "verbindung"])
def test_r1_kostenloser_zweiter_versuch(ki, monkeypatch, fehler):
    P = _module("ai.provider")
    monkeypatch.setattr(P, "WIEDERHOLEN_WARTEN_S", 0.0, raising=False)
    exc = {"529": lambda: _status_fehler("OverloadedError", 529), "429": lambda: _status_fehler("RateLimitError", 429),
           "verbindung": _verbindung_nie_aufgebaut}[fehler]()
    kl = ki(Klient(bewertung=_folge(exc, "ok"), zaehlen=_wahre_tokens))
    kasse = _kasse()
    antwort = _bewerten(kasse, None)
    assert antwort["status"] == "ok" and len(kl.bewertungen) == 2, "einmal selbst wiederholt"
    assert kasse.unsicher_ct == 0
    assert {"max_retries": 0} in [{k: v for k, v in o.items() if k == "max_retries"} for o in kl.optionen]


def test_r2_529_zweimal_ist_ueberlastet_und_bindet_nichts(ki, monkeypatch):
    P = _module("ai.provider")
    monkeypatch.setattr(P, "WIEDERHOLEN_WARTEN_S", 0.0, raising=False)
    kl = ki(Klient(bewertung=_folge(_status_fehler("OverloadedError", 529), _status_fehler("OverloadedError", 529)),
                   zaehlen=_wahre_tokens))
    kasse = _kasse()
    antwort = _bewerten(kasse, None)
    assert antwort["status"] == "ueberlastet" and antwort["nicht_berechnet"] is True
    assert len(kl.bewertungen) == 2 and kasse.unsicher_ct == 0 and kasse.abrechnung_ct == 0
    # 500 ebenso "ueberlastet"
    assert P._status_aus_ausnahme(_status_fehler("InternalServerError", 500)) == "ueberlastet"


def test_r3_zeitlimit_kein_zweiter_versuch_und_unsicher(ki, monkeypatch):
    P = _module("ai.provider")
    monkeypatch.setattr(P, "WIEDERHOLEN_WARTEN_S", 0.0, raising=False)
    kl = ki(Klient(bewertung=_folge(_zeitlimit(), "ok"), zaehlen=_wahre_tokens))
    kasse = _kasse()
    antwort = _bewerten(kasse, None)
    assert antwort["status"] == "zeitlimit" and len(kl.bewertungen) == 1
    assert kasse.unsicher_ct > 0 and not antwort.get("nicht_berechnet")


def test_r4_recherche_529_wiederholt_und_bindet_nichts(ki, monkeypatch):
    P = _module("ai.provider")
    monkeypatch.setattr(P, "WIEDERHOLEN_WARTEN_S", 0.0, raising=False)
    schritte = iter([_status_fehler("OverloadedError", 529), "ok"])

    def f(kw):
        s = next(schritte)
        if isinstance(s, BaseException):
            raise s
        return _antwort(RECHERCHE_TEXT, _usage(ein=20000, aus=800, suchen=1))
    kl = ki(Klient(recherche=f))
    kasse = _kasse()
    r, _plan = _recherche_lauf(kasse, None)
    assert r["status"] == "ok" and len(kl.recherchen) == 2
    assert kasse.unsicher_ct == 0 and [p["schritt"] for p in kasse.posten] == ["recherche#1"]
    # zweimal 529: Fehler, aber nichts gebunden (die Bewertung behaelt ihren Rest)
    ki(Klient(recherche=lambda kw: (_ for _ in ()).throw(_status_fehler("OverloadedError", 529))))
    kasse2 = _kasse()
    r2, _ = _recherche_lauf(kasse2, None)
    assert r2["status"] == "ueberlastet" and kasse2.unsicher_ct == 0 and kasse2.gebunden_ct == 0


# ------------------------------------------------ (M) Modell ohne Preis
def test_m1_bewertungsmodell_ohne_preis_kein_aufruf(welt, ki, monkeypatch):
    """Pruefer: KI_MODELL=claude-fable-5-1 (10/50 $) — die Kasse rechnete mit
    Opus 5/25, gebucht wurde zu Sonnet 2/10: echt 24,50 ct, kein Alarm. Jetzt
    kein Aufruf, Status kostendeckel, Alarm ki_modell_ohne_preis."""
    D = _module("ai.damage_pricing")
    monkeypatch.setenv("KI_MODELL", "claude-fable-5-1")
    kl = ki(Klient(recherche=lambda kw: _antwort(RECHERCHE_TEXT, _usage(ein=24000, aus=1200, suchen=1)),
                   bewertung=_bewertung_fest(ANTWORT_VERTRAG, ein=12000, aus=2500), zaehlen=_wahre_tokens))
    w = welt.w
    _vertrag_aufraeumen(welt)
    vid = f"v_kdm1_{w.s}"
    _alarme_weg(welt, vid)
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=_fahrzeug(welt, vid), damages=SCHAEDEN))
    assert erg["status"] == "kostendeckel" and "kein Preis" in erg["grund"]
    assert kl.bewertungen == [] and kl.recherchen == [] and kl.zaehlungen == []
    assert len(_alarme(welt, "ki_modell_ohne_preis", vid)) == 1
    assert _zaehler(welt) == 0
    _alarme_weg(welt, vid)
    _vertrag_aufraeumen(welt)


def test_m2_recherchemodell_ohne_preis_keine_websuche(welt, ki, monkeypatch):
    D = _module("ai.damage_pricing")
    monkeypatch.setenv("KI_RECHERCHE_MODELL", "claude-sonnet-4-6")      # 3/15 $, nicht in der Preisliste
    kl = ki(Klient(recherche=lambda kw: _antwort(RECHERCHE_TEXT, _usage(ein=60000, aus=3000, suchen=1)),
                   bewertung=_bewertung_fest(ANTWORT_VERTRAG, ein=4000, aus=1400), zaehlen=_wahre_tokens))
    w = welt.w
    _vertrag_aufraeumen(welt)
    vid = f"v_kdm2_{w.s}"
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=_fahrzeug(welt, vid), damages=SCHAEDEN))
    assert erg["status"] == "ok" and kl.recherchen == [] and len(kl.bewertungen) == 1
    doc = welt.run(welt.db.ki_bewertungen.find_one({"id": erg["id"]}, {"_id": 0}))
    assert any("kein Preis für Modell claude-sonnet-4-6" in h for h in doc["kostendeckel"]["hinweise"])
    _vertrag_aufraeumen(welt)


def test_m3_buchung_unbekannter_modelle_zum_hoechstpreis(ki):
    K = _module("ai.kalibrierung")
    # 60.000 ein / 3.000 aus: Opus-Preis 5/25 $ (hoechster bekannter) statt Sonnet 2/10
    assert K._kosten_usd("claude-sonnet-4-6", {"input_tokens": 60000, "output_tokens": 3000}) == pytest.approx(0.375)
    assert K._kosten_usd(SONNET, {"input_tokens": 60000, "output_tokens": 3000}) == pytest.approx(0.15)


# ------------------------------------------------ (F) Fortsetzung ohne Suche
def test_f1_fortsetzung_ohne_weitere_suche(ki):
    """Pruefer: nach einer ersten Anfrage mit Suche passte keine Fortsetzung
    (jede plante 20.000 Tokens fuer ein weiteres Ergebnis) — der ###DATEN-
    Block fehlte. Jetzt laeuft sie ohne Suche (tool_choice none)."""
    antworten = iter([_antwort("Teil 1: Delle 120-200 EUR (ADAC)", _usage(ein=30000, aus=1500, suchen=1), stop="pause_turn"),
                      _antwort("Teil 2\n###DATEN\nd1|120|200|160|ADAC|https://www.adac.de/x", _usage(ein=33000, aus=300))])
    kl = ki(Klient(recherche=lambda kw: next(antworten)))
    kasse = _kasse()
    r, _plan = _recherche_lauf(kasse, None)
    assert len(kl.recherchen) == 2 and kl.recherchen[1]["tool_choice"] == {"type": "none"}
    assert "tool_choice" not in kl.recherchen[0]
    assert "###DATEN" in r["text"] and "Teil 1" in r["text"]
    assert [p["schritt"] for p in kasse.posten] == ["recherche#1", "recherche#2"]
    assert kasse.geplant_max_ct <= 15 and kasse.obergrenze_max_ct <= kasse.hart_budget_ct


def test_f2_pause_vor_angestossener_suche_braucht_eine_suche(ki):
    """Steht die Pause vor einer schon angestossenen Suche (letzter Block
    server_tool_use), fuehrt die Fortsetzung sie aus — sie wird MIT einer
    Suche eingeplant (nicht mit tool_choice none)."""
    from test_ki_kostendeckel_20260927 import _Block
    from types import SimpleNamespace
    pause = SimpleNamespace(content=[_Block(type="text", text="Teil 1", citations=None),
                                     _Block(type="server_tool_use", text="")],
                            stop_reason="pause_turn", usage=_usage(ein=3000, aus=200, suchen=0), stop_details=None)
    antworten = iter([pause, _antwort("Teil 2", _usage(ein=9000, aus=300, suchen=1))])
    kl = ki(Klient(recherche=lambda kw: next(antworten)))
    kasse = _kasse()
    r, _plan = _recherche_lauf(kasse, None)
    assert len(kl.recherchen) == 2 and "tool_choice" not in kl.recherchen[1]
    assert kl.recherchen[1]["tools"][0]["max_uses"] >= 1


# ------------------------------------------------ (A) Ausnahmepfad
def test_a1_vertrag_ausnahme_kosten_bleiben_nach_abgleich(welt, ki, monkeypatch):
    """Pruefer: nach einer bezahlten Recherche wirft ein DB-Fehler — abrechnen
    buchte die 4 ct, das Dokument behielt kosten_ct 0, der stuendliche
    budget.abgleichen nahm sie wieder heraus."""
    D = _module("ai.damage_pricing")
    MD = _module("ai.marktdaten")
    B = _module("ai.budget")

    async def _kaputt(*a, **kw):
        raise RuntimeError("DB weg")
    monkeypatch.setattr(MD, "lernen_aus_recherche", _kaputt)
    ki(Klient(recherche=lambda kw: _antwort(RECHERCHE_BEIDE, _usage(ein=24000, aus=1200, suchen=1)),
              bewertung=_bewertung_fest(ANTWORT_VERTRAG, ein=4000, aus=1400), zaehlen=_wahre_tokens))
    w = welt.w
    _vertrag_aufraeumen(welt)
    vid = f"v_kda1_{w.s}"
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=_fahrzeug(welt, vid), damages=SCHAEDEN))
    assert erg["status"] == "fehler"
    doc = welt.run(welt.db.ki_bewertungen.find_one({"id": erg["id"]}, {"_id": 0}))
    assert doc["status"] == "fehler" and doc["kosten_ct"] == pytest.approx(4.0, abs=0.01)
    assert doc["kostendeckel"]["posten"][0]["schritt"] == "recherche#1"
    assert _zaehler(welt) == pytest.approx(4.0, abs=0.02)
    welt.run(B.abgleichen())
    assert _zaehler(welt) == pytest.approx(4.0, abs=0.02), "abgleichen behaelt die ausgegebenen 4 ct"
    _vertrag_aufraeumen(welt)


def test_a2_abholung_ausnahme_und_neu_berechnen_kosten_summieren(welt, ki, monkeypatch):
    """Abholung: im Ausnahmepfad blieb das Dokument 'laeuft' (abgleichen
    zaehlte nach dem Lease 0, die Karte startete ohne Klick einen neuen
    bezahlten Lauf); ein 'Neu berechnen' ueberschrieb kosten_ct. Jetzt:
    Status fehler, kosten_summe_ct ueber alle Laeufe, abgleichen zaehlt sie."""
    K = _module("ai.pickup_assessment")
    MD = _module("ai.marktdaten")
    B = _module("ai.budget")
    echt = MD.lernen_aus_recherche

    async def _kaputt(*a, **kw):
        raise RuntimeError("DB weg")
    monkeypatch.setattr(MD, "lernen_aus_recherche", _kaputt)
    kl = ki(Klient(recherche=lambda kw: _antwort("Nichts Verwertbares.", _usage(ein=24000, aus=1200, suchen=1)),
                   bewertung=_bewertung_fest(_abholung_daten, ein=5000, aus=1800), zaehlen=_wahre_tokens))
    w = welt.w
    _vertrag_aufraeumen(welt)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "kda2")
    assert welt.run(K.bewertung_ausfuehren(pid, w.dealer_id)) is None
    doc = welt.run(welt.db.ki_bewertungen.find_one({"protocol_id": pid}, {"_id": 0}))
    assert doc["status"] == "fehler" and "lease_until" not in doc
    c1 = doc["kosten_summe_ct"]
    assert c1 >= 4.0 and doc["kosten_ct"] == pytest.approx(c1, abs=0.01), "Recherche-Kosten am Dokument"
    # die Karte fragt nach: KEIN neuer Lauf ohne Klick
    n = len(kl.recherchen)
    welt.run(K.bewertung_lesen(pid, w.dealer_id))
    welt.run(asyncio.sleep(0.05))
    assert len(kl.recherchen) == n
    # "Neu berechnen": zweiter Lauf, die Summe waechst
    monkeypatch.setattr(MD, "lernen_aus_recherche", echt)
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id, erzwingen=True))
    assert erg["status"] == "ok"
    doc = welt.run(welt.db.ki_bewertungen.find_one({"protocol_id": pid}, {"_id": 0}))
    assert doc["kosten_summe_ct"] == pytest.approx(c1 + doc["kosten_ct"], abs=0.02)
    welt.run(B.abgleichen())
    assert _zaehler(welt, "abholung") == pytest.approx(doc["kosten_summe_ct"], abs=0.02)
    _vertrag_aufraeumen(welt)


# ------------------------------------------------ (N) Normalfall-Rechnung
def test_n1_normalfall_rechnung(welt, ki):
    """Pruefer: im Normalfall blieb nur EINE Suche (~7 ct je Lauf). Jetzt zwei
    Runden mit je einer Suche: erwartet <= 15 ct (Ziel), sichere Obergrenze
    <= 19,4 ct (hart minus Abstand); tatsaechlich hier 4 + 4 + Bewertung."""
    D = _module("ai.damage_pricing")
    kl = ki(Klient(recherche=lambda kw: _antwort("Delle 120-200 EUR (ADAC).", _usage(ein=24000, aus=1200, suchen=1)),
                   bewertung=_bewertung_fest(ANTWORT_VERTRAG, ein=4650, aus=1500, cache_schreiben=3520),
                   zaehlen=_wahre_tokens))
    w = welt.w
    _vertrag_aufraeumen(welt)
    vid = f"v_kdn1_{w.s}"
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=_fahrzeug(welt, vid), damages=SCHAEDEN))
    doc = welt.run(welt.db.ki_bewertungen.find_one({"id": erg["id"]}, {"_id": 0}))
    kd = doc["kostendeckel"]
    print("\nNormalfall Vertrag:", {k: kd[k] for k in ("bewertung_reserve_ct", "bewertung_erwartet_ct", "geplant_ct",
                                                       "obergrenze_ct", "kosten_ct")},
          [(p["schritt"], p["ct"], p.get("obergrenze_ct")) for p in kd["posten"]])
    assert len(kl.recherchen) == 2 and doc["recherche"]["suchen"] == 2
    assert kd["geplant_ct"] <= 15 and kd["obergrenze_ct"] <= 19.4 and kd["bewertung"] == "voll"
    assert kd["kosten_ct"] < 15
    _vertrag_aufraeumen(welt)
