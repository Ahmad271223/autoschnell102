# -*- coding: utf-8 -*-
"""Kostendeckel je KI-Lauf (Betriebsalarm 27.09.2026 11:10: Vertrag
15,31 ct bei 15 ct Grenze; Vorgabe Ahmad "mach maximum 20ct aber versuchen
15ct"). Nie ein echter Anthropic-Aufruf: der Client ist eine Attrappe
(messages.create UND messages.count_tokens), Preise fest.

(a) Alarm-Szenario nachgestellt: geplant <= 15 ct, tatsaechlich <= 20 ct,
    kein Alarm zwischen 15 und 20 (Zaehler "ueber dem Ziel" statt Alarm)
(b) Fortsetzung nach pause_turn entfaellt, wenn sie nicht mehr passt
    (auch nicht ohne weitere Suche)
(c) Suchergebnisse groesser als SUCHE_TOKENS_MAX -> Bewertung gekuerzt oder
    ausgelassen, nie ueber der Grenze, Alarm ki_kostendeckel_gegriffen
(d) count_tokens scheitert -> sichere Schaetzung, nie ueber der Grenze
(e) Normalfall unveraendert (gleiche Aufrufe, gleiche Ergebnisse)
(f) Eigenschaftstest (seed fest, 500 Faelle)
"""
import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_ki_abholbewertung_20260925 import ANTWORT as ANTWORT_ABHOLUNG, _welt_aufbauen, vollstaendig  # noqa: E402
from test_ki_vertrag_20260926 import ANTWORT as ANTWORT_VERTRAG, SCHAEDEN, _fahrzeug  # noqa: E402

SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5-20251001"
PREISE = {"claude-opus-5": (5.0, 25.0), SONNET: (2.0, 10.0), HAIKU: (1.0, 5.0), "claude-haiku-4-5": (1.0, 5.0)}
GROSS_TEXT = "Delle Smart-Repair 120-200 EUR (ADAC), Kratzer Spot-Repair 150-250 EUR (FairGarage). " * 70
RECHERCHE_TEXT = ("Delle Smart-Repair 120-200 EUR (ADAC). Kratzer Spot-Repair 150-250 EUR (FairGarage).\n"
                  "###DATEN\nd1|120|200|160|ADAC|https://www.adac.de/x\n")


# ------------------------------------------------ Attrappe des Anthropic-Clients
def _usage(ein=0, aus=0, cache_schreiben=0, cache_lesen=0, suchen=None):
    stu = SimpleNamespace(web_search_requests=suchen) if suchen is not None else None
    return SimpleNamespace(input_tokens=int(ein), output_tokens=int(aus), cache_creation_input_tokens=int(cache_schreiben),
                           cache_read_input_tokens=int(cache_lesen), server_tool_use=stu)


class _Block(SimpleNamespace):
    def model_dump(self, exclude_none=True):
        return {"type": self.type, "text": getattr(self, "text", "")}


def _antwort(text, usage, stop="end_turn"):
    return SimpleNamespace(content=[_Block(type="text", text=text, citations=None)], stop_reason=stop, usage=usage,
                           stop_details=None)


class Klient:
    """messages.create: mit `tools` = Recherche, sonst Bewertung.
    recherche/bewertung/zaehlen sind Funktionen(kw) -> Antwort bzw. int."""

    def __init__(self, recherche=None, bewertung=None, zaehlen=None):
        self.recherche_fn, self.bewertung_fn, self.zaehlen_fn = recherche, bewertung, zaehlen
        self.recherchen, self.bewertungen, self.zaehlungen, self.optionen = [], [], [], []
        self.messages = self

    def with_options(self, **kw):
        self.optionen.append(kw)
        return self

    async def create(self, **kw):
        if kw.get("tools"):
            self.recherchen.append(json.loads(json.dumps(kw, default=str)))
            return self.recherche_fn(kw)
        self.bewertungen.append(kw)
        return self.bewertung_fn(kw)

    async def count_tokens(self, **kw):
        self.zaehlungen.append(kw)
        return SimpleNamespace(input_tokens=int(self.zaehlen_fn(kw)))


def _wahre_tokens(kw):
    """'Echte' Eingabe-Tokens einer Bewertung fuer die Attrappe: ~3,2 Byte je
    Token (Kostenkarte), Schema NICHT enthalten (undokumentiert)."""
    teile = [b.get("text", "") for b in kw.get("system") or []] + [m["content"] for m in kw.get("messages") or []]
    return int(sum(len(t.encode("utf-8")) for t in teile) / 3.2)


def _schema_tokens(kw):
    return int(len(json.dumps((kw.get("output_config") or {}).get("format"), ensure_ascii=False).encode("utf-8")) / 3.2)


@pytest.fixture
def ki(monkeypatch):
    """Preise, Grenzen, Schalter fest; nie der echte Client."""
    P = _module("ai.provider")
    K = _module("ai.kalibrierung")
    MD = _module("ai.marktdaten")
    monkeypatch.setattr(K, "PREIS_JE_MIO", dict(PREISE))
    monkeypatch.setenv("KI_KOSTEN_MAX_CT", "20")
    monkeypatch.setenv("KI_KOSTEN_ZIEL_CT", "15")
    for v in ("KI_MODELL", "KI_RECHERCHE_MODELL", "KI_EFFORT", "KI_DENKEN_AUS", "KI_MARKTANALYSE_VERTRAG",
              "KI_MARKTANALYSE_ABHOLUNG", "KI_MARKTANALYSE_AKTIV", "KI_BUDGET_MONAT_EUR", "KI_BUDGET_FAHRER_EUR"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(P, "ki_aktiv", lambda: True)
    monkeypatch.setattr(MD, "ki_aktiv", lambda: True)
    for name in ("ai.damage_pricing", "ai.pickup_assessment"):
        monkeypatch.setattr(_module(name), "ki_aktiv", lambda: True)
    # Sicherung: ohne gesetzte Attrappe scheitert jeder Aufruf laut
    monkeypatch.setattr(P, "_klient", lambda: (_ for _ in ()).throw(AssertionError("echter Client!")))

    def setzen(klient):
        monkeypatch.setattr(P, "_klient", lambda: klient)
        return klient
    return setzen


def _alarme(welt, typ, ref):
    return welt.run(welt.db.betriebsalarme.find({"typ": typ, "ref": ref}, {"_id": 0}).to_list(20))


def _alarme_weg(welt, ref):
    welt.run(welt.db.betriebsalarme.delete_many({"ref": ref}))


def _vertrag_aufraeumen(welt):
    db, w = welt.db, welt.w
    welt.run(db.ki_bewertungen.delete_many({"dealer_id": w.dealer_id}))
    welt.run(db.ki_budget.delete_many({"_id": {"$regex": f":({w.dealer_id}|{w.chef['id']}|{w.sucher['id']}|{w.driver_id}):"}}))
    welt.run(db.ki_reparaturpreise.delete_many({"marke": "bmw"}))


def _bewertung_fest(antwort_daten, ein, aus, cache_schreiben=0):
    def f(kw):
        daten = antwort_daten(kw) if callable(antwort_daten) else antwort_daten
        return _antwort(json.dumps(daten), _usage(ein=ein, aus=min(aus, kw["max_tokens"]), cache_schreiben=cache_schreiben))
    return f


def _bewertung_schlimmst(antwort_daten):
    """Bewertung, die ihre Obergrenze ausreizt: alle Eingabe-Tokens als
    Cache-Schreiben (1,25x), Ausgabe = max_tokens."""
    def f(kw):
        daten = antwort_daten(kw) if callable(antwort_daten) else antwort_daten
        ein = _wahre_tokens(kw) + _schema_tokens(kw)
        return _antwort(json.dumps(daten), _usage(cache_schreiben=ein, aus=kw["max_tokens"]))
    return f


def _abholung_daten(kw):
    return vollstaendig(ANTWORT_ABHOLUNG, json.loads(kw["messages"][0]["content"]))


# ------------------------------------------------ (a) Alarm-Szenario 27.09.2026
def test_a_alarm_szenario_geplant_ziel_nie_ueber_hart(welt, ki):
    """Recherche 12,00 ct (100.000 Eingabe-Tokens, 1 Suche, 2.000 aus) +
    Bewertung 3,31 ct = 15,31 ct wie im Alarm. Neu: geplant wird <= 15 ct,
    die Spanne bis 20 ct ist Puffer -> KEIN ki_kosten_ueberschritten, der
    Lauf zaehlt 'ueber dem Ziel'; die Bewertung laeuft unveraendert."""
    D = _module("ai.damage_pricing")
    MD = _module("ai.marktdaten")
    kl = ki(Klient(recherche=lambda kw: _antwort(RECHERCHE_TEXT, _usage(ein=100000, aus=2000, suchen=1)),
                   bewertung=_bewertung_fest(ANTWORT_VERTRAG, ein=4650, aus=1500, cache_schreiben=3520),
                   zaehlen=_wahre_tokens))
    w = welt.w
    _vertrag_aufraeumen(welt)
    vid = f"v_kda_{w.s}"
    _alarme_weg(welt, vid)
    fz = _fahrzeug(welt, vid)
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["status"] == "ok", erg
    assert erg["kosten_ct"] == pytest.approx(15.31, abs=0.01), "Szenario wie im Alarm"
    doc = welt.run(welt.db.ki_bewertungen.find_one({"id": erg["id"]}, {"_id": 0}))
    kd = doc["kostendeckel"]
    assert kd["ziel_ct"] == 15 and kd["hart_ct"] == 20
    assert kd["geplant_ct"] <= 15.0, kd
    assert kd["kosten_ct"] <= 20.0 and kd["ueber_ziel"] is True and kd["ueber_hart"] is False
    assert kd["bewertung"] == "voll", "Bewertung unveraendert — der Puffer bis 20 ct reicht"
    # je Runde EINE Suche; nach 12 ct in Runde 1 passt keine zweite Runde
    # mehr (Nachbesserung 27.09.2026: Runden statt zwei Suchen in einer Anfrage)
    assert len(kl.recherchen) == 1 and kl.recherchen[0]["tools"][0]["max_uses"] == 1
    assert "hoechstens 1 Suchen" in kl.recherchen[0]["messages"][0]["content"]
    assert len(kl.bewertungen) == 1 and kl.bewertungen[0]["max_tokens"] == _module("ai.provider").KI_MAX_TOKENS
    # die volle Recherche steht im Prompt der Bewertung
    zusatz = kl.bewertungen[0]["system"][1]["text"]
    assert zusatz.endswith(MD.fall_als_text({"text": doc["recherche"]["text"], "quellen": doc["recherche"]["quellen"]}))
    # der Puffer Ziel..Hart wurde genutzt, die harte Grenze galt vor dem Aufruf
    assert 15 < kd["obergrenze_ct"] <= 20 - 0.6
    assert _alarme(welt, "ki_kosten_ueberschritten", vid) == [], "zwischen Ziel und Hart kein Alarm"
    # kein zweiter bezahlter Versuch des SDK
    assert {"max_retries": 0} in [{k: v for k, v in o.items() if k == "max_retries"} for o in kl.optionen]
    # naechster Lauf: letzter ueber dem ZIEL -> Sparmodus (ohne Websuche)
    B = _module("ai.budget")
    bud = welt.run(B.pruefen(user_id=w.sucher["id"], dealer_id=w.dealer_id, art="vertrag"))
    assert bud["sparmodus"] is True and "Ziel von 15 ct" in bud["grund"]
    # Betriebsseite: Zaehler "ueber dem Ziel"
    KAL = _module("ai.kalibrierung")
    st = welt.run(KAL.statistik())
    assert st["budget"]["lauf_ziel_ct"] == 15 and st["budget"]["lauf_max_ct"] == 20
    assert st["budget"]["ueber_ziel"] >= 1
    _alarme_weg(welt, vid)
    _vertrag_aufraeumen(welt)


def test_a2_ueber_hart_nur_als_letzte_sicherung_mit_einzelposten(welt, ki):
    """Die letzte Sicherung bleibt: wird die HARTE Grenze doch gerissen
    (Bewertung liefert mehr Tokens als gezaehlt — darf praktisch nie
    vorkommen), meldet ki_kosten_ueberschritten die Einzelposten."""
    D = _module("ai.damage_pricing")
    ki(Klient(recherche=lambda kw: _antwort(RECHERCHE_TEXT, _usage(ein=100000, aus=2000, suchen=1)),
              bewertung=_bewertung_fest(ANTWORT_VERTRAG, ein=400000, aus=1500), zaehlen=_wahre_tokens))
    w = welt.w
    _vertrag_aufraeumen(welt)
    vid = f"v_kda2_{w.s}"
    _alarme_weg(welt, vid)
    fz = _fahrzeug(welt, vid)
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["kosten_ct"] > 20
    al = _alarme(welt, "ki_kosten_ueberschritten", vid)
    assert len(al) == 1 and al[0]["details"]["grenze_ct"] == 20
    assert "recherche#1" in al[0]["details"]["posten"] and "bewertung" in al[0]["details"]["posten"]
    _alarme_weg(welt, vid)
    _vertrag_aufraeumen(welt)


# ------------------------------------------------ (b) Fortsetzung nach pause_turn
def _kasse(**kw):
    KK = _module("ai.kostenkasse")
    k = KK.Kostenkasse(art="vertrag", ref="t", ziel_ct=kw.pop("ziel", 15), hart_ct=kw.pop("hart", 20))
    k.alarme = []

    async def _alarm(typ, **d):
        k.alarme.append((typ, d))
    k._alarm = _alarm
    return k


def _recherche_lauf(kasse, klient_antworten, reserve_ct=6.0):
    """provider.recherche wie aus fall_recherche: erste Anfrage mit Plan."""
    import asyncio
    P = _module("ai.provider")
    MD = _module("ai.marktdaten")
    kasse.bewertung_reserve_ct = reserve_ct
    kasse.bewertung_erwartet_ct = reserve_ct
    frage = "Fahrzeug: BMW. Recherchiere ...\n- id d1: Delle Kotfluegel\n" + MD.DATEN_ANWEISUNG
    basis = kasse.recherche_basis_tokens(MD.RECHERCHE_SYSTEM, frage)
    plan = kasse.recherche_plan(basis, MD.MAX_SUCHEN_FALL, HAIKU)
    assert plan is not None
    return asyncio.new_event_loop().run_until_complete(
        P.recherche(system=MD.RECHERCHE_SYSTEM, frage=frage, max_suchen=plan["max_uses"], max_tokens=plan["max_tokens"],
                    kasse=kasse, basis_tokens=basis, plan=plan)), plan


def test_b_fortsetzung_entfaellt_wenn_sie_nicht_passt(ki):
    """Selbst eine Fortsetzung OHNE weitere Suche (Verlauf ~93.000 Tokens)
    passt nicht mehr in den Rest -> sie entfaellt, der Text bleibt."""
    antworten = iter([_antwort("Teil 1: Delle 120-200 EUR (ADAC)", _usage(ein=90000, aus=1500, suchen=1), stop="pause_turn"),
                      _antwort("Teil 2", _usage(ein=1000, aus=100, suchen=0))])
    kl = ki(Klient(recherche=lambda kw: next(antworten)))
    kasse = _kasse()
    r, plan = _recherche_lauf(kasse, None)
    assert len(kl.recherchen) == 1, "Fortsetzung haette den Rest gesprengt"
    assert r["status"] == "ok" and "Teil 1" in r["text"], "mit dem bis dahin gefundenen Text weiter"
    assert any("Fortsetzung der Websuche entfallen" in h for h in kasse.hinweise)
    assert kasse.geplant_max_ct <= 15 and kasse.obergrenze_max_ct <= kasse.hart_budget_ct
    assert r["gebucht"] is True and kasse.posten[0]["schritt"] == "recherche#1"
    assert kasse.posten[0]["tokens_je_suche"] is not None, "Tokens je Suche werden protokolliert"
    assert {"max_retries": 0} in [{k: v for k, v in o.items() if k == "max_retries"} for o in kl.optionen]


def test_b2_kleine_fortsetzung_passt_und_laeuft(ki):
    antworten = iter([_antwort("Teil 1", _usage(ein=3000, aus=200, suchen=0), stop="pause_turn"),
                      _antwort("Teil 2", _usage(ein=3500, aus=300, suchen=1))])
    kl = ki(Klient(recherche=lambda kw: next(antworten)))
    kasse = _kasse()
    r, _plan = _recherche_lauf(kasse, None)
    assert len(kl.recherchen) == 2 and "Teil 2" in r["text"]
    assert [p["schritt"] for p in kasse.posten] == ["recherche#1", "recherche#2"]
    assert kasse.geplant_max_ct <= 15


def test_b3_fehler_mitten_in_der_schleife_verliert_keine_kosten(ki):
    """F3: bricht die zweite Anfrage ab, bleiben die Kosten der ersten im
    Ergebnis; die abgebrochene zaehlt zur Obergrenze (unsicher).
    Nachbesserung 27.09.2026: auch der Text der ersten Anfrage bleibt (status
    ok) — eine gescheiterte Fortsetzung verwirft das Gefundene nicht mehr."""
    def _antworten():
        yield _antwort("Teil 1", _usage(ein=3000, aus=200, suchen=1), stop="pause_turn")
        raise RuntimeError("Verbindung weg")
    gen = _antworten()
    ki(Klient(recherche=lambda kw: next(gen)))
    kasse = _kasse()
    r, _plan = _recherche_lauf(kasse, None)
    assert r["status"] == "ok" and r["text"] == "Teil 1" and "Fortsetzung gescheitert" in r["grund"]
    assert r["usage"]["input_tokens"] == 3000 and r["suchen"] == 1
    assert kasse.kosten_ct > 0 and kasse.unsicher_ct > 0
    # scheitert schon die ERSTE Anfrage, bleibt es ein Fehler
    def _sofort(kw):
        raise RuntimeError("Verbindung weg")
    ki(Klient(recherche=_sofort))
    kasse2 = _kasse()
    r2, _ = _recherche_lauf(kasse2, None)
    assert r2["status"] == "fehler" and kasse2.unsicher_ct > 0


# ------------------------------------------------ (c) groessere Suchergebnisse
# 106.000 Tokens: die Recherche kostet ~15 ct (eingeplant ~6 ct) -> Rest bis
# 19,4 ct reicht nicht mehr fuer die volle Bewertung (~5 ct), aber fuer eine
# gekuerzte; 140.000: ~19 ct -> Bewertung entfaellt.
@pytest.mark.parametrize("ein_recherche,erwartet", [(106000, "gekuerzt"), (140000, "entfallen")])
def test_c_groessere_suchergebnisse_kuerzen_oder_auslassen(welt, ki, ein_recherche, erwartet):
    """Abholung: die Suche bringt weit mehr Tokens als SUCHE_TOKENS_MAX
    (eingeplant ~20.000). Die Bewertung wird gegen die HARTE Grenze neu
    geprueft: gekuerzt (Fall-Text/max_tokens) oder ausgelassen — nie drueber."""
    K = _module("ai.pickup_assessment")
    KK = _module("ai.kostenkasse")
    kl = ki(Klient(recherche=lambda kw: _antwort(GROSS_TEXT + RECHERCHE_TEXT, _usage(cache_schreiben=ein_recherche,
                                                                                   aus=1500, suchen=1)),
                   bewertung=_bewertung_schlimmst(_abholung_daten), zaehlen=_wahre_tokens))
    w = welt.w
    _vertrag_aufraeumen(welt)
    welt.run(welt.db.ki_marktdaten.delete_many({"_id": "aktuell"}))     # feste Groesse des Zusatzes
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, f"kdc{erwartet[:3]}")
    _alarme_weg(welt, pid)
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    doc = welt.run(welt.db.ki_bewertungen.find_one({"protocol_id": pid, "status": {"$ne": "laeuft"}}, {"_id": 0}))
    kd = doc["kostendeckel"]
    assert kd["kosten_ct"] <= 20.0, kd
    assert kd["posten"][0]["tokens_je_suche"] > KK.SUCHE_TOKENS_MAX, "Suchergebnis groesser als eingeplant"
    if erwartet == "gekuerzt":
        assert erg["status"] == "ok" and len(kl.bewertungen) == 1
        assert kd["bewertung"] in ("fall_gekuerzt", "ohne_fall", "max_tokens_gesenkt"), kd
        zusatz = kl.bewertungen[0]["system"][1]["text"] if len(kl.bewertungen[0]["system"]) > 1 else ""
        if kd["bewertung"] == "max_tokens_gesenkt":
            assert kl.bewertungen[0]["max_tokens"] < _module("ai.provider").KI_MAX_TOKENS
        else:
            assert GROSS_TEXT.strip() not in zusatz, "Fall-Text gekuerzt oder weggelassen"
        assert kd["hinweise"] and kd["obergrenze_ct"] <= 20 - 0.6, "Hinweis am Lauf, harte Grenze vor dem Aufruf"
        assert kl.bewertungen[0]["max_tokens"] >= KK.BEWERTUNG_MAX_TOKENS_MIN
        assert _alarme(welt, "ki_kostendeckel_gegriffen", pid) == []
    else:
        assert erg["status"] == "kostendeckel" and kl.bewertungen == [], "Bewertung NICHT gemacht"
        assert "Kostendeckel je Lauf (20 ct)" in erg["grund"]
        al = _alarme(welt, "ki_kostendeckel_gegriffen", pid)
        assert len(al) == 1 and al[0]["details"]["grenze_ct"] == 20
        assert _alarme(welt, "ki_bewertung_fehlgeschlagen", pid) == [], "statt Fehlermeldung der Deckel-Alarm"
    assert _alarme(welt, "ki_kosten_ueberschritten", pid) == []
    _alarme_weg(welt, pid)
    _vertrag_aufraeumen(welt)


def test_c2_stufen_fall_text_dann_max_tokens_dann_entfallen(ki):
    """Reihenfolge, wenn die Bewertung nach einer zu teuren Recherche nicht
    mehr in die HARTE Grenze passt: voller Fall-Text -> halber -> ohne ->
    max_tokens bis zur Untergrenze -> entfallen (mit Alarm). Nie drueber."""
    import asyncio
    P = _module("ai.provider")
    S = _module("ai.schemas")
    D = _module("ai.damage_pricing")
    MD = _module("ai.marktdaten")
    KK = _module("ai.kostenkasse")
    kl = ki(Klient(bewertung=_bewertung_schlimmst({"items": []}), zaehlen=_wahre_tokens))
    nutzer = {"damages": [{"id": "d1", "type": "delle"}]}
    texte = [MD.fall_als_text({"text": GROSS_TEXT}, a) for a in KK.FALL_ANTEILE]
    loop = asyncio.new_event_loop()

    def grenze(fall_text):
        k = _kasse()
        e = loop.run_until_complete(k._bewertung_eingabe(P.json_bewerten, system=D.SYSTEM_PROMPT,
                                                         zusatz=KK._zusatz(["Marktpreise"], fall_text), nutzer=nutzer,
                                                         schema=S.ANTWORT_SCHEMA))
        return k.obergrenze_ct(SONNET, e["tokens"], P.KI_MAX_TOKENS)
    b_voll, b_halb, b_ohne = (grenze(t) for t in texte)
    assert b_voll > b_halb > b_ohne
    faelle = ((b_voll + 0.05, "voll"), ((b_voll + b_halb) / 2, "fall_gekuerzt"), ((b_halb + b_ohne) / 2, "ohne_fall"),
              (b_ohne - 0.3, "max_tokens_gesenkt"), (1.0, "entfallen"))
    for rest, stufe in faelle:
        kasse = _kasse()
        kasse.buchen("recherche#1", HAIKU, {"input_tokens": int((kasse.hart_budget_ct - rest) / 1e-4)})
        kl.bewertungen.clear()
        antwort = loop.run_until_complete(kasse.bewerten(
            P.json_bewerten, modell=SONNET, system=D.SYSTEM_PROMPT, nutzer=nutzer, schema=S.ANTWORT_SCHEMA,
            zusatz_teile=["Marktpreise"], fall_texte=texte, max_tokens=P.KI_MAX_TOKENS))
        assert kasse.bewertung_stufe == stufe, (stufe, rest, kasse.bericht())
        assert kasse.kosten_ct <= 20 and kasse.obergrenze_max_ct <= kasse.hart_budget_ct + 1e-9
        if stufe == "entfallen":
            assert antwort["status"] == "kostendeckel" and kl.bewertungen == []
            assert [a[0] for a in kasse.alarme] == ["ki_kostendeckel_gegriffen"]
        else:
            assert antwort["status"] == "ok" and len(kl.bewertungen) == 1 and kasse.alarme == []
            m = kl.bewertungen[0]["max_tokens"]
            assert (m < P.KI_MAX_TOKENS) == (stufe == "max_tokens_gesenkt") and m >= KK.BEWERTUNG_MAX_TOKENS_MIN
    loop.close()


# ------------------------------------------------ (d) count_tokens scheitert
def _bewertung_mit_kasse(kasse, klient, nutzer, zusatz_teile=("Marktpreise ...",), fall_text="", system=None):
    import asyncio
    P = _module("ai.provider")
    S = _module("ai.schemas")
    D = _module("ai.damage_pricing")
    return asyncio.new_event_loop().run_until_complete(
        kasse.bewerten(P.json_bewerten, modell=SONNET, system=system or D.SYSTEM_PROMPT, nutzer=nutzer,
                       schema=S.ANTWORT_SCHEMA, zusatz_teile=list(zusatz_teile), fall_texte=[fall_text, ""],
                       max_tokens=P.KI_MAX_TOKENS))


def test_d_count_tokens_scheitert_sicher_nach_oben(ki):
    def _kaputt(kw):
        raise RuntimeError("count_tokens nicht erreichbar")
    kl = ki(Klient(bewertung=_bewertung_schlimmst({"items": []}), zaehlen=_kaputt))
    nutzer = {"damages": [{"id": f"d{i}", "note": "Delle " * 30} for i in range(8)]}
    kasse = _kasse()
    antwort = _bewertung_mit_kasse(kasse, kl, nutzer)
    assert len(kl.zaehlungen) == 1 and len(kl.bewertungen) == 1
    post = [p for p in kasse.posten if p["schritt"] == "bewertung"][0]
    assert post["gezaehlt"] is False, "Laengen-Schaetzung statt count_tokens"
    assert post["ct"] <= post["obergrenze_ct"], "die Schaetzung liegt sicher ueber dem Echtwert"
    assert kasse.kosten_ct <= 20 and antwort["status"] == "ok"
    # ... und passt die Schaetzung nicht mehr in den Rest: Aufruf auslassen statt riskieren
    kasse2 = _kasse()
    kasse2.buchen("recherche#1", HAIKU, {"input_tokens": 150000, "web_search_requests": 2})     # 17 ct weg
    kl.bewertungen.clear()
    antwort2 = _bewertung_mit_kasse(kasse2, kl, nutzer)
    assert antwort2["status"] == "kostendeckel" and kl.bewertungen == []
    assert kasse2.kosten_ct <= 20 and kasse2.alarme[0][0] == "ki_kostendeckel_gegriffen"


def test_d2_count_tokens_mit_denselben_parametern(ki):
    """count_tokens bekommt exakt die Parameter des echten Aufrufs (ohne max_tokens)."""
    kl = ki(Klient(bewertung=_bewertung_schlimmst({"items": []}), zaehlen=_wahre_tokens))
    kasse = _kasse()
    _bewertung_mit_kasse(kasse, kl, {"damages": [{"id": "d1"}]}, fall_text="Marktrecherche zu diesem Fall: x")
    gezaehlt, gesendet = kl.zaehlungen[0], kl.bewertungen[0]
    assert {k: v for k, v in gesendet.items() if k != "max_tokens"} == gezaehlt
    post = [p for p in kasse.posten if p["schritt"] == "bewertung"][0]
    assert post["gezaehlt"] is True and post["ct"] <= post["obergrenze_ct"]


# ------------------------------------------------ (e) Normalfall unveraendert
def test_e_normalfall_vertrag_unveraendert(welt, ki):
    D = _module("ai.damage_pricing")
    MD = _module("ai.marktdaten")
    P = _module("ai.provider")
    kl = ki(Klient(recherche=lambda kw: _antwort(RECHERCHE_TEXT, _usage(ein=24000, aus=1200, suchen=1)),
                   bewertung=_bewertung_fest(ANTWORT_VERTRAG, ein=4000, aus=1400, cache_schreiben=3500),
                   zaehlen=_wahre_tokens))
    w = welt.w
    _vertrag_aufraeumen(welt)
    vid = f"v_kde_{w.s}"
    _alarme_weg(welt, vid)
    fz = _fahrzeug(welt, vid)
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["status"] == "ok"
    # Nachbesserung 27.09.2026: ZWEI Suchen wie vor dem Deckel — als zwei Runden
    # mit je einer Suche; Runde 2 nur fuer d2 (d1 hat Runde 1 schon beantwortet)
    assert len(kl.recherchen) == 2 and len(kl.bewertungen) == 1
    assert [r["tools"][0]["max_uses"] for r in kl.recherchen] == [1, 1]
    assert "id d2" in kl.recherchen[1]["messages"][0]["content"]
    assert "id d1" not in kl.recherchen[1]["messages"][0]["content"]
    b = kl.bewertungen[0]
    assert b["max_tokens"] == P.KI_MAX_TOKENS and b["model"] == SONNET
    assert b["system"][0]["text"] == D.SYSTEM_PROMPT and b["system"][0]["cache_control"] == {"type": "ephemeral"}
    doc = welt.run(welt.db.ki_bewertungen.find_one({"id": erg["id"]}, {"_id": 0}))
    fall_text = MD.fall_als_text({"text": doc["recherche"]["text"], "quellen": doc["recherche"]["quellen"]})
    assert b["system"][1]["text"].endswith(fall_text), "voller Fall-Text wie bisher"
    assert json.loads(b["messages"][0]["content"])["damages"][0]["id"] == "d1"
    # gleiche Ergebnisse: die Werte der KI-Antwort
    items = {i["source_id"]: i for i in erg["ergebnis"]["items"]}
    assert items["d1"]["fair_discount_eur"] == 250 and items["d2"]["fair_discount_eur"] == 150
    assert erg["ergebnis"]["combined"]["fair_discount_eur"] == 400
    kd = doc["kostendeckel"]
    assert kd["bewertung"] == "voll" and kd["hinweise"] == [] and kd["geplant_ct"] <= 15
    assert kd["kosten_ct"] == erg["kosten_ct"] and erg["kosten_ct"] < 15 and kd["ueber_ziel"] is False
    assert _alarme(welt, "ki_kosten_ueberschritten", vid) == [] and _alarme(welt, "ki_kostendeckel_gegriffen", vid) == []
    # Budget: reserviert wurde die HARTE Grenze, abgerechnet die echten Kosten
    B = _module("ai.budget")
    assert B.kosten_max_ct() == 20 and B.ziel_ct() == 15
    assert welt.run(B.zaehler_ct(user_id=w.sucher["id"], dealer_id=w.dealer_id, art="vertrag")) == pytest.approx(
        erg["kosten_ct"], abs=0.02)
    _alarme_weg(welt, vid)
    _vertrag_aufraeumen(welt)


def test_e2_normalfall_abholung_unveraendert(welt, ki):
    K = _module("ai.pickup_assessment")
    P = _module("ai.provider")
    kl = ki(Klient(recherche=lambda kw: _antwort(RECHERCHE_TEXT, _usage(ein=24000, aus=1200, suchen=1)),
                   bewertung=_bewertung_fest(_abholung_daten, ein=5000, aus=1800, cache_schreiben=3900),
                   zaehlen=_wahre_tokens))
    w = welt.w
    _vertrag_aufraeumen(welt)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "kde2")
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "ok"
    # zwei Runden mit je einer Suche (Nachbesserung 27.09.2026)
    assert len(kl.recherchen) == 2 and len(kl.bewertungen) == 1 and kl.bewertungen[0]["max_tokens"] == P.KI_MAX_TOKENS
    assert "Delle Smart-Repair 120-200 EUR" in kl.bewertungen[0]["system"][1]["text"]
    doc = welt.run(welt.db.ki_bewertungen.find_one({"protocol_id": pid, "status": "ok"}, {"_id": 0}))
    assert doc["kostendeckel"]["bewertung"] == "voll" and doc["kostendeckel"]["geplant_ct"] <= 15
    assert doc["kosten_ct"] < 15
    _vertrag_aufraeumen(welt)


# ------------------------------------------------ (f) Eigenschaftstest
def test_f_eigenschaft_500_faelle_nie_ueber_hart_plan_nie_ueber_ziel(ki):
    """Zufaellige Groessen (seed fest): Zahl/Laenge der Schaeden, Markttabelle,
    Suchergebnis je Suche, Zahl der Suchen, pause_turn, Ausgabe, count_tokens
    ja/nein. Die Attrappe bucht immer den teuersten Fall (Cache-Schreiben).
    Tatsaechliche Summe nie > HART; geplante Obergrenze nie > ZIEL, solange
    die Suchergebnisse <= SUCHE_TOKENS_MAX sind."""
    import asyncio
    D = _module("ai.damage_pricing")
    MD = _module("ai.marktdaten")
    P = _module("ai.provider")
    S = _module("ai.schemas")
    KK = _module("ai.kostenkasse")
    rnd = random.Random(20260927)
    loop = asyncio.new_event_loop()
    fahrzeug = {"make_label": "BMW", "model_label": "530", "first_registration": "01/2010", "price": 8900,
                "mileage": 206000}
    arten = ["delle", "kratzer", "rost", "steinschlag", "beleuchtung"]
    gross_n = klein_n = 0
    for fallnr in range(500):
        n_schaeden = rnd.randint(1, 12)
        schaeden = [{"id": f"d{i}", "type_key": rnd.choice(arten), "type_label": "Schaden " + "x" * rnd.randint(0, 80),
                     "zone": "Tür vorne links " + "y" * rnd.randint(0, 60), "view": "left",
                     "note": "Notiz " * rnd.randint(0, 40), "severity_data": {"groesse": "2–5 cm"}}
                    for i in range(n_schaeden)]
        paket = D.paket_bauen(fahrzeug, schaeden, kaufpreis=8000)
        markt = "Aktuelle Marktpreise:\n" + "\n".join(f"- Position {i}: 100-300 EUR (ADAC)" for i in range(rnd.randint(0, 400)))
        faktor = rnd.choice([0.3, 0.6, 1.0]) if rnd.random() < 0.7 else rnd.choice([1.5, 2.5, 4.0])
        r_akt = int(KK.SUCHE_TOKENS_MAX * faktor)
        gross = faktor > 1.0
        gross_n += gross
        klein_n += not gross
        pause = rnd.random() < 0.3
        mit_zaehlen = rnd.random() < 0.8
        kontext = {"ctx": None, "nr": 0}

        def _recherche(kw, kontext=kontext):
            kontext["nr"] += 1
            m, n = kw["max_tokens"], kw["tools"][0]["max_uses"]
            if (kw.get("tool_choice") or {}).get("type") == "none":
                n = 0                      # Fortsetzung ohne weitere Suche
            if kontext["ctx"] is None or len(kw["messages"]) == 1:      # neue Anfrage (Runde), keine Fortsetzung
                basis_echt = int((len(kw["system"][0]["text"].encode()) + len(kw["messages"][0]["content"].encode())) / 3.2) + 600
                kontext["ctx"] = basis_echt
            k = rnd.randint(0, n)
            aus = rnd.randint(100, m)
            ctx = kontext["ctx"]
            ein = (k + 1) * ctx + k * aus + r_akt * k * (k + 1) // 2
            kontext["ctx"] = ctx + k * r_akt + aus
            stop = "pause_turn" if (pause and kontext["nr"] == 1) else "end_turn"
            return _antwort(RECHERCHE_TEXT, _usage(cache_schreiben=ein, aus=aus, suchen=k), stop=stop)

        def _bewertung(kw):
            ein = int(_wahre_tokens(kw) * rnd.uniform(0.97, 1.05)) + rnd.randint(0, 300) + _schema_tokens(kw)
            return _antwort("{}", _usage(cache_schreiben=ein, aus=rnd.randint(200, kw["max_tokens"])))

        def _zaehlen(kw):
            if not mit_zaehlen:
                raise RuntimeError("weg")
            return _wahre_tokens(kw)
        ki(Klient(recherche=_recherche, bewertung=_bewertung, zaehlen=_zaehlen))
        kasse = _kasse()
        teile = [markt, ""]

        async def lauf():
            await kasse.bewertung_einplanen(P.json_bewerten, modell=SONNET, system=D.SYSTEM_PROMPT, nutzer=paket,
                                            schema=S.ANTWORT_SCHEMA, zusatz_teile=teile, max_tokens=P.KI_MAX_TOKENS,
                                            fall_text_max_zeichen=MD.FALL_TEXT_MAX_ZEICHEN)
            fall = await MD.fall_recherche("vertrag", paket, eigene={}, kasse=kasse)
            antwort = await kasse.bewerten(P.json_bewerten, modell=SONNET, system=D.SYSTEM_PROMPT, nutzer=paket,
                                           schema=S.ANTWORT_SCHEMA, zusatz_teile=teile,
                                           fall_texte=[MD.fall_als_text(fall, a) for a in KK.FALL_ANTEILE],
                                           max_tokens=P.KI_MAX_TOKENS)
            return fall, antwort, await kasse.abschliessen()
        fall, antwort, kosten = loop.run_until_complete(lauf())
        info = (fallnr, n_schaeden, faktor, pause, mit_zaehlen, kasse.bericht())
        assert kosten <= 20.0 + 1e-9, info
        assert kasse.bewertung_reserve_ct <= 15.0, info
        assert kasse.obergrenze_max_ct <= kasse.hart_budget_ct + 1e-9, info
        if not gross:
            # Nachbesserung 27.09.2026: ZIEL = erwartete Kosten, die sichere
            # Obergrenze gilt gegen HART (oben) — Bewertung bleibt voll
            assert kasse.geplant_max_ct <= 15.0 + 1e-9, info
            assert antwort["status"] == "ok" and kasse.bewertung_stufe == "voll", info
        assert not [a for a in kasse.alarme if a[0] == "ki_kosten_ueberschritten"], info
    loop.close()
    assert gross_n > 50 and klein_n > 250
