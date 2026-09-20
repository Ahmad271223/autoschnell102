# -*- coding: utf-8 -*-
"""Nachpruefung 20.09.2026: mobile.de + AutoScout24 gegen die Apify-Grenze.

Frage Ahmads: Kleinanzeigen laeuft ueber einen eigenen bezahlten Dienst
(kleinanzeigen-agent.de), mobile.de und AutoScout24 aber BEIDE ueber Apify.
Jede Quelle hatte eine eigene Obergrenze (je 20), eine gemeinsame gab es
nicht — zusammen also 40, waehrend der Starter-Plan 32 gleichzeitige Laeufe
erlaubt.

Gemessen mit `scripts/lasttest_apify_grenze.py` (Apify nachgestellt, echter
Job-Weg, echte Zaehler in der Datenbank, Verbund aus 8 Prozessen):

    mobile 20 + autoscout 20 = 40   ->  8 von 40 Suchern sahen einen Fehler
                                        (24 Apify-Ablehnungen, alle drei
                                        Versuche in 4,5 s verbraucht)
    mobile 16 + autoscout 16 = 32   ->  0 Fehler
    20+20, aber MIT Abstand          ->  0 Fehler (8 Abweisungen, die
                                        Wartenden kamen nach 5 s durch)

Daraus zwei Absicherungen, die diese Tests festhalten:
  1. Die Standardwerte passen zum Plan (16+16 = 32), und der Start warnt,
     wenn jemand die Summe darueber schiebt.
  2. Ein Tempolimit (429) bekommt einen Abstand, bevor der Job erneut
     anlaeuft — vorher waren alle drei Versuche in gut einer Sekunde weg.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
PROJEKT = BACKEND.parent


def _compose() -> str:
    return (PROJEKT / "docker-compose.yml").read_text(encoding="utf-8")


def _zahl(text: str, name: str) -> int:
    """Standardwert einer Compose-Zeile `- NAME=${NAME:-42}` lesen."""
    import re
    treffer = re.search(rf"{name}=\$\{{{name}:-(\d+)\}}", text)
    assert treffer, f"{name} steht nicht in docker-compose.yml"
    return int(treffer.group(1))


def test_01_standardwerte_passen_zum_apify_plan():
    """Seit dem gemeinsamen Topf (N9) deckelt APIFY_MAX_PARALLEL beide
    Quellen zusammen — einzeln duerfen sie den ganzen Plan nutzen, sonst
    liegen Plaetze brach (gemessen: 143 s statt 83 s fuer 100 Links)."""
    t = _compose()
    mob = _zahl(t, "MAX_CONCURRENT_MOBILE")
    aut = _zahl(t, "MAX_CONCURRENT_AUTOSCOUT")
    plan = _zahl(t, "APIFY_MAX_PARALLEL")
    assert mob >= plan and aut >= plan, (
        f"mobile ({mob}) bzw. autoscout ({aut}) duerfen weniger als der "
        f"Apify-Plan ({plan}) — dann bleiben Plaetze ungenutzt")


def test_01b_der_gemeinsame_topf_deckelt_beide():
    import provider_limiter as PL
    assert "apify" in PL.PROVIDER_MAX_CONCURRENT, (
        "es gibt keinen gemeinsamen Apify-Topf (N9)")
    assert set(PL.APIFY_QUELLEN) == {"mobile", "autoscout24"}
    quelle = (BACKEND / "listing_identity.py").read_text(encoding="utf-8")
    code = "\n".join(z.split("#", 1)[0] for z in quelle.splitlines())
    assert 'acquire_slot(db, "apify")' in code, (
        "der Abrufweg belegt keinen Platz im gemeinsamen Topf")
    assert code.count("release_slot(db, apify_slot)") >= 1, (
        "der Platz im gemeinsamen Topf wird nie zurueckgegeben")
    # Klappt der zweite Griff nicht, muss der erste zurueck — sonst leckt
    # bei jeder Ueberlastung ein Platz der Quelle.
    assert "release_slot(db, slot_id)" in code


def test_02_kleinanzeigen_zaehlt_nicht_zum_apify_topf():
    """Kleinanzeigen laeuft ueber kleinanzeigen-agent.de, nicht ueber Apify —
    sonst waere die Rechnung oben falsch."""
    quelle = (BACKEND / "kleinanzeigen_api.py").read_text(encoding="utf-8")
    assert "apify" not in quelle.lower(), (
        "Kleinanzeigen laeuft doch ueber Apify — dann muss es in die Summe")
    assert "kleinanzeigen-agent.de" in quelle


def test_03_startpruefung_warnt_bei_zu_hoher_summe():
    import production_check

    class _Log:
        def __init__(self):
            self.zeilen = []

        def warning(self, text, *a):
            self.zeilen.append(str(text) % a if a else str(text))

        info = error = warning

    alt = dict(os.environ)
    try:
        os.environ.update({"MAX_CONCURRENT_MOBILE": "16",
                           "MAX_CONCURRENT_AUTOSCOUT": "16",
                           "APIFY_MAX_PARALLEL": "32",
                           "APP_ENV": "entwicklung"})
        log = _Log()
        production_check.pruefe_produktion(log)
        alles = "\n".join(log.zeilen)
        assert "MAX_CONCURRENT_MOBILE" in alles and "APIFY_MAX_PARALLEL" in alles, (
            "der Start warnt nicht, wenn eine Quelle weniger darf als der "
            "Apify-Plan hergibt — dann liegen Plaetze brach")

        os.environ.update({"MAX_CONCURRENT_MOBILE": "32",
                           "MAX_CONCURRENT_AUTOSCOUT": "32"})
        log2 = _Log()
        production_check.pruefe_produktion(log2)
        assert "APIFY_MAX_PARALLEL" not in "\n".join(log2.zeilen), (
            "bei passender Summe darf nicht gewarnt werden")
    finally:
        os.environ.clear()
        os.environ.update(alt)


def test_04_tempolimit_bekommt_abstand():
    import link_jobs as LJ
    from anbieter_fehler import AnbieterFehler, ART_LIMIT, ART_AUSFALL

    assert LJ._ist_tempolimit(AnbieterFehler(ART_LIMIT, "mobile.de", "HTTP 429"))
    # Alles andere laeuft sofort wieder an — ein verschwundenes Inserat oder
    # ein Netzfehler hat nichts mit einer Ueberlastung zu tun.
    assert not LJ._ist_tempolimit(AnbieterFehler(ART_AUSFALL, "mobile.de", "HTTP 500"))
    assert not LJ._ist_tempolimit(RuntimeError("irgendwas"))
    assert LJ.TEMPOLIMIT_WARTEN > 0, (
        "ohne Abstand sind alle drei Versuche in gut einer Sekunde verbraucht")


def test_05_wartende_jobs_werden_nicht_vorzeitig_geholt():
    """Der Filter `_reif()` haelt einen Job mit Wartezeit zurueck."""
    import link_jobs as LJ

    f = LJ._reif()
    assert "$or" in f
    faelle = [b for b in f["$or"] if "fruehestens" in b]
    assert len(faelle) == 3, (
        "der Filter deckt nicht alle drei Faelle ab (Feld fehlt / None / faellig)")
    # Ein Job OHNE Wartezeit muss durchkommen — das ist der Normalfall.
    ohne = {}
    faellig = {"fruehestens": datetime.now(timezone.utc) - timedelta(seconds=1)}
    spaeter = {"fruehestens": datetime.now(timezone.utc) + timedelta(seconds=60)}

    def passt(doc):
        for bed in f["$or"]:
            feld, regel = next(iter(bed.items()))
            if regel is None:
                # {"fruehestens": None} — ausdruecklich leer gesetzt
                if feld in doc and doc[feld] is None:
                    return True
                continue
            if "$exists" in regel:
                if (feld in doc) == regel["$exists"]:
                    return True
            elif "$lte" in regel:
                wert = doc.get(feld)
                if wert is not None and wert <= regel["$lte"]:
                    return True
        return False

    assert passt(ohne), "ein Job ohne Wartezeit wuerde liegenbleiben"
    assert passt(faellig), "ein faelliger Job wuerde liegenbleiben"
    assert not passt(spaeter), "ein wartender Job wird zu frueh geholt"


def test_06_der_filter_sitzt_an_allen_drei_stellen():
    """Gegenprobe an der Quelle: `_reif()` muss in JEDEM Weg stehen, der
    wartende Jobs holt — sonst greift der Abstand nur teilweise."""
    quelle = (BACKEND / "link_jobs.py").read_text(encoding="utf-8")
    code = "\n".join(z.split("#", 1)[0] for z in quelle.splitlines())
    assert code.count("**_reif()") == 3, (
        f"_reif() steht an {code.count('**_reif()')} statt 3 Stellen "
        "(_beanspruchen und die beiden Kandidaten-Runden)")
    # Und kein Weg darf noch ohne den Filter auf wartende Jobs zugreifen.
    ohne_filter = [z for z in code.splitlines()
                   if '"status": "queued", "active": True' in z
                   and "_reif()" not in z]
    assert not ohne_filter, f"Weg ohne Wartezeit-Filter: {ohne_filter}"


def test_07_lasttest_skript_ist_da_und_schickt_nichts_raus():
    skript = (BACKEND / "scripts" / "lasttest_apify_grenze.py")
    assert skript.exists(), "das Messwerkzeug fehlt"
    t = skript.read_text(encoding="utf-8")
    assert "KEINE echte Anfrage" in t
    assert "ApifyNachgestellt" in t, (
        "der Lasttest stellt Apify nicht nach — er wuerde echte Laeufe kosten")
