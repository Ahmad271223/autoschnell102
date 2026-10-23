# -*- coding: utf-8 -*-
"""Kostendeckel je KI-Lauf - Runde 3 (Nachpruefung 27.09.2026, vier niedrige
Restpunkte; keiner riss die harte Grenze). Nie ein echter Anthropic-Aufruf:
Attrappe des Clients aus test_ki_kostendeckel_20260927, Preise fest.

(M) Abholung: kosten_summe_ct wanderte in den Folgemonat mit - jetzt Summe
    je Monat (kosten_monat), budget.abgleichen zaehlt nur den laufenden
    Monat; Altdokumente ohne Monatsaufteilung zaehlen im Monat ihres
    created_at und werden vor einem neuen Lauf umgestellt
(U) pickup_assessment.bewertung_ausfuehren: kein UnboundLocalError, wenn
    schon _grundlagen wirft ("Wirft nie")
(V) Vertrag: bei einer Lauf-Uebernahme bleiben die bezahlten Kosten des
    alten Laufs am Dokument (frueher_ids) - abgleichen zaehlt beide Laeufe
(F) Fortsetzung nach pause_turn direkt nach einem noch nicht gelesenen
    web_search_tool_result: SUCHE_TOKENS_MAX zur Basis, die Obergrenze des
    Aufrufs haelt wieder
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_ki_abholbewertung_20260925 import _welt_aufbauen  # noqa: E402
from test_ki_kostendeckel_20260927 import (RECHERCHE_TEXT, Klient, _abholung_daten, _antwort,  # noqa: E402,F401
                                           _Block, _bewertung_fest, _kasse, _recherche_lauf, _usage,
                                           _vertrag_aufraeumen, _wahre_tokens, ki)
from test_ki_vertrag_20260926 import ANTWORT as ANTWORT_VERTRAG, SCHAEDEN, _fahrzeug  # noqa: E402

# Werte fuer BEIDE Positionen -> keine zweite Suchrunde (feste Kosten je Recherche)
RECHERCHE_BEIDE = ("Delle 120-200 EUR (ADAC). Kratzer 150-250 EUR (FairGarage).\n###DATEN\n"
                   "d1|120|200|160|ADAC|https://www.adac.de/x\nd2|150|250|200|FairGarage|https://www.fairgarage.com/y\n")


def _monate():
    """(laufender Monat, Vormonat) als JJJJ-MM (UTC)."""
    jetzt = datetime.now(timezone.utc)
    j, m = jetzt.year, jetzt.month - 1
    if m == 0:
        j, m = j - 1, 12
    return jetzt.strftime("%Y-%m"), f"{j:04d}-{m:02d}"


def _zaehler(welt, art):
    B = _module("ai.budget")
    w = welt.w
    return welt.run(B.zaehler_ct(user_id=w.sucher["id"], dealer_id=w.dealer_id, art=art))


# ------------------------------------------------ (M) Summe je Monat
@pytest.mark.parametrize("fall", ["neu", "alt_vormonat", "alt_gleicher_monat"])
def test_m1_abholung_folgemonat_nimmt_alte_laeufe_nicht_mit(welt, ki, fall):
    """Pruefer: Lauf 1 (10,8 ct) auf den 20. des Vormonats zurueckdatiert,
    Lauf 2 per 'Neu berechnen' im laufenden Monat - der Zaehler zeigte nach
    abgleichen 21,6 statt 10,8 ct (Sparmodus/Monatsdeckel zu frueh).
    neu: Dokument mit kosten_monat. alt_vormonat: Altdokument (nur
    kosten_summe_ct) aus dem Vormonat -> zaehlt nicht mehr mit.
    alt_gleicher_monat: Altdokument aus diesem Monat -> Lauf 1 zaehlt weiter
    (sonst fiele er beim Umstellen heraus)."""
    K = _module("ai.pickup_assessment")
    B = _module("ai.budget")
    w = welt.w
    _vertrag_aufraeumen(welt)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, f"k3m{fall[:6]}")
    ki(Klient(recherche=lambda kw: _antwort(RECHERCHE_TEXT, _usage(ein=24000, aus=1200, suchen=1)),
              bewertung=_bewertung_fest(_abholung_daten, ein=5000, aus=1800), zaehlen=_wahre_tokens))
    assert welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))["status"] == "ok"
    doc = welt.run(welt.db.ki_bewertungen.find_one({"protocol_id": pid}, {"_id": 0}))
    c1 = doc["kosten_ct"]
    assert c1 > 4
    jetzt, vormonat = _monate()
    if fall == "neu":
        # Lauf 1 wie im Vormonat gelaufen (created_at UND Monatsaufteilung)
        welt.run(welt.db.ki_bewertungen.update_one(
            {"protocol_id": pid}, {"$set": {"created_at": f"{vormonat}-20T10:00:00+00:00"},
                                   "$rename": {f"kosten_monat.{jetzt}": f"kosten_monat.{vormonat}"}}))
    elif fall == "alt_vormonat":
        welt.run(welt.db.ki_bewertungen.update_one(
            {"protocol_id": pid}, {"$set": {"created_at": f"{vormonat}-20T10:00:00+00:00"},
                                   "$unset": {"kosten_monat": ""}}))
    else:
        welt.run(welt.db.ki_bewertungen.update_one({"protocol_id": pid}, {"$unset": {"kosten_monat": ""}}))
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id, erzwingen=True))
    assert erg["status"] == "ok"
    doc = welt.run(welt.db.ki_bewertungen.find_one({"protocol_id": pid}, {"_id": 0}))
    c2 = doc["kosten_ct"]
    soll = c1 + c2 if fall == "alt_gleicher_monat" else c2
    welt.run(B.abgleichen())
    assert _zaehler(welt, "abholung") == pytest.approx(soll, abs=0.02), "nur die Kosten dieses Monats"
    if doc.get("driver_id"):
        assert welt.run(B.fahrer_zaehler_ct(doc["driver_id"])) == pytest.approx(soll, abs=0.02)
    assert doc["kosten_summe_ct"] == pytest.approx(c1 + c2, abs=0.02), "Gesamtsumme bleibt"
    assert doc["kosten_monat"][jetzt] == pytest.approx(soll, abs=0.02)
    if fall != "alt_gleicher_monat":
        assert doc["kosten_monat"][vormonat] == pytest.approx(c1, abs=0.02), "Lauf 1 bleibt im Vormonat"
    _vertrag_aufraeumen(welt)


def test_m2_gezahlt_je_monat_und_altdokumente():
    B = _module("ai.budget")
    neu = {"status": "ok", "created_at": "2026-09-03T08:00:00+00:00", "kosten_ct": 10.8, "kosten_summe_ct": 21.6,
           "kosten_monat": {"2026-08": 10.8, "2026-09": 10.8}}
    assert B._gezahlt_ct(neu, "2026-09") == 10.8 and B._gezahlt_ct(neu, "2026-08") == 10.8
    assert B._gezahlt_ct(neu, "2026-10") == 0.0
    alt = {"status": "ok", "created_at": "2026-09-03T08:00:00+00:00", "kosten_ct": 10.8, "kosten_summe_ct": 21.6}
    assert B._gezahlt_ct(alt, "2026-09") == 21.6, "Altdokument: Summe im Monat von created_at"
    assert B._gezahlt_ct(alt, "2026-10") == 0.0, "... und in keinem anderen Monat"
    ganz_alt = {"status": "ok", "created_at": "2026-09-03T08:00:00+00:00", "kosten_ct": 7.0}
    assert B._gezahlt_ct(ganz_alt, "2026-09") == 7.0
    assert B._gezahlt_ct({**ganz_alt, "status": "laeuft"}, "2026-09") == 0.0
    assert B.monat_von("2026-08-20T10:00:00+00:00") == "2026-08" and len(B.monat_von("kaputt")) == 7
    assert B.kosten_inc(4.0, "2026-09") == {"kosten_summe_ct": 4.0, "kosten_monat.2026-09": 4.0}


# ------------------------------------------------ (U) kein UnboundLocalError
def test_u1_grundlagen_wirft_liefert_none(welt, ki, monkeypatch):
    """Pruefer: _grundlagen wirft RuntimeError('DB weg') -> beim Aufrufer kam
    UnboundLocalError (kasse vor der Zuweisung gelesen). 'Wirft nie'."""
    K = _module("ai.pickup_assessment")

    async def _kaputt(*a, **kw):
        raise RuntimeError("DB weg")
    monkeypatch.setattr(K, "_grundlagen", _kaputt)
    kl = ki(Klient())
    assert welt.run(K.bewertung_ausfuehren("p_gibt_es_nicht", welt.w.dealer_id)) is None
    assert kl.recherchen == [] and kl.bewertungen == []


# ------------------------------------------------ (V) Vertrag: Uebernahme
class UebernahmeKlient(Klient):
    """Wie Klient; vor dem ersten Aufruf der Art `bei` (recherche/bewertung)
    laeuft `waehrend` (async) - hier: Lease abgelaufen, ein zweiter Aufruf
    uebernimmt denselben Stand, waehrend der erste noch rechnet."""

    def __init__(self, *a, bei="recherche", waehrend=None, **kw):
        super().__init__(*a, **kw)
        self.bei, self.waehrend, self.nebenbei = bei, waehrend, []

    async def create(self, **kw):
        ist_recherche = bool(kw.get("tools"))
        if self.waehrend is not None and ist_recherche == (self.bei == "recherche"):
            f, self.waehrend = self.waehrend, None
            self.nebenbei.append(await f())
        return await super().create(**kw)


@pytest.mark.parametrize("bei", ["recherche", "bewertung"])
def test_v1_uebernahme_kosten_beider_laeufe_bleiben(welt, ki, bei):
    """Pruefer: der alte Lauf bezahlt 4 ct und wird uebernommen, der neue
    endet mit seinen Kosten - nach abgleichen zeigte der Zaehler nur den
    neuen Lauf (kosten_sichern bzw. der Abschluss trafen die alte id nicht).
    recherche: Uebernahme waehrend der ersten Recherche, der alte Lauf bricht
    vor der Bewertung ab (Ausnahmepfad). bewertung: Uebernahme waehrend der
    Bewertung, der alte Lauf endet normal (Abschluss nach der Uebernahme)."""
    D = _module("ai.damage_pricing")
    B = _module("ai.budget")
    w = welt.w
    _vertrag_aufraeumen(welt)
    vid = f"v_k3v{bei[:3]}_{w.s}"
    fz = _fahrzeug(welt, vid)

    async def zweiter_aufruf():
        await welt.db.ki_bewertungen.update_one({"vehicle_id": vid, "status": "laeuft"},
                                                {"$set": {"lease_until": "2000-01-01T00:00:00+00:00"}})
        alt = await welt.db.ki_bewertungen.find_one({"vehicle_id": vid}, {"_id": 0, "id": 1})
        neu = await D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN)
        return {"alt_id": alt["id"], "neu": neu}
    kl = ki(UebernahmeKlient(recherche=lambda kw: _antwort(RECHERCHE_BEIDE, _usage(ein=24000, aus=1200, suchen=1)),
                             bewertung=_bewertung_fest(ANTWORT_VERTRAG, ein=4000, aus=1400), zaehlen=_wahre_tokens,
                             bei=bei, waehrend=zweiter_aufruf))
    erg_alt = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    neben = kl.nebenbei[0]
    assert neben["neu"]["status"] == "ok" and neben["neu"]["id"] != neben["alt_id"], "zweiter Aufruf hat uebernommen"
    assert erg_alt["id"] == neben["alt_id"]
    assert len(kl.bewertungen) == (1 if bei == "recherche" else 2)
    docs = welt.run(welt.db.ki_bewertungen.find({"vehicle_id": vid}, {"_id": 0}).to_list(10))
    assert len(docs) == 1, "die Uebernahme nutzt dasselbe Dokument"
    doc = docs[0]
    assert doc["id"] == neben["neu"]["id"] and doc["status"] == "ok"
    kosten_neu = doc["kosten_ct"]
    kosten_alt = 4.0 if bei == "recherche" else kosten_neu      # bewertung: gleicher Ablauf wie der neue Lauf
    vor = _zaehler(welt, "vertrag")                              # abrechnen: beide Laeufe
    assert vor == pytest.approx(kosten_alt + kosten_neu, abs=0.02)
    welt.run(B.abgleichen())
    assert _zaehler(welt, "vertrag") == pytest.approx(kosten_alt + kosten_neu, abs=0.02), \
        "abgleichen zaehlt die Kosten beider Laeufe"
    doc = welt.run(welt.db.ki_bewertungen.find_one({"vehicle_id": vid}, {"_id": 0}))
    assert doc["kosten_summe_ct"] == pytest.approx(kosten_alt + kosten_neu, abs=0.02)
    assert neben["alt_id"] in doc.get("frueher_ids", [])
    _vertrag_aufraeumen(welt)


# ------------------------------------------------ (F) Fortsetzung nach Suchergebnis
def test_f1_fortsetzung_nach_ungelesenem_suchergebnis_obergrenze_haelt(ki):
    """Pruefer: pause_turn direkt nach einem web_search_tool_result (letzter
    Block, noch von keiner Runde gelesen, in keiner usage). Die Fortsetzung
    liest es (hier SUCHE_TOKENS_MAX Tokens als Cache-Schreiben) - ihre
    Obergrenze lag darunter (2,05 ct geplant, 4,22 ct tatsaechlich)."""
    KK = _module("ai.kostenkasse")
    MD = _module("ai.marktdaten")
    kasse = _kasse()
    frage = "Fahrzeug: BMW. Recherchiere ...\n- id d1: Delle Kotfluegel\n" + MD.DATEN_ANWEISUNG
    basis = kasse.recherche_basis_tokens(MD.RECHERCHE_SYSTEM, frage)
    pause = SimpleNamespace(content=[_Block(type="text", text="Teil 1", citations=None),
                                     _Block(type="server_tool_use", text=""),
                                     _Block(type="web_search_tool_result", text="", content=[])],
                            stop_reason="pause_turn", usage=_usage(ein=3000, aus=200, suchen=1), stop_details=None)

    def fortsetzung(kw):
        # der ganze Verlauf + das ungelesene Ergebnis, alles als Cache-Schreiben (1,25x)
        return _antwort("Teil 2\n###DATEN\nd1|120|200|160|ADAC|https://www.adac.de/x",
                        _usage(cache_schreiben=basis + 3000 + 200 + KK.SUCHE_TOKENS_MAX, aus=kw["max_tokens"]))
    schritte = iter([lambda kw: pause, fortsetzung])
    kl = ki(Klient(recherche=lambda kw: next(schritte)(kw)))
    # Bewertung mit 10 ct eingeplant: im Rest passt die Fortsetzung nur OHNE
    # weitere Suche (Plan n=0 wie im Befund) - mit einer Suche (n=1) waere
    # SUCHE_TOKENS_MAX ohnehin eingerechnet
    r, _plan = _recherche_lauf(kasse, None, reserve_ct=10.0)
    assert len(kl.recherchen) == 2 and r["status"] == "ok"
    assert kl.recherchen[1].get("tool_choice") == {"type": "none"}, "Fortsetzung ohne weitere Suche (n=0)"
    p2 = kasse.posten[1]
    assert p2["schritt"] == "recherche#2"
    assert p2["ct"] <= p2["obergrenze_ct"], f"Obergrenze der Fortsetzung zu niedrig: {p2}"
    assert kasse.kosten_ct <= kasse.obergrenze_max_ct + 0.01 and kasse.kosten_ct <= kasse.hart_budget_ct
    # ohne ungelesenes Ergebnis (letzter Block Text) bleibt die Basis wie bisher
    assert KK.Kostenkasse.fortsetzung_basis(100, {"input_tokens": 50, "output_tokens": 5}) == 155
    assert KK.Kostenkasse.fortsetzung_basis(100, {"input_tokens": 50, "output_tokens": 5}, 2) == \
        155 + 2 * KK.SUCHE_TOKENS_MAX
