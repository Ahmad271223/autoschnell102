# -*- coding: utf-8 -*-
"""Fehlversuche der Marktbeobachtung je Marke/Modell/Segment (Frage Ahmad 28.09.2026: "wie viele Segmente
sind mehrfach fehlgeschlagen — welche Marke, Modell und Segmente haben bislang nicht geklappt").

    python -X utf8 scripts/markt_fehlversuche.py                        # alle Tage
    python -X utf8 scripts/markt_fehlversuche.py --tage 7               # nur die letzten 7 Tage (Job-Tag)
    python -X utf8 scripts/markt_fehlversuche.py --seit "2026-09-28 18:40"   # nur Laeufe, die seitdem endeten (deutsche Zeit)
    python -X utf8 scripts/markt_fehlversuche.py --alle                 # auch Segmente mit nur EINEM Fehlversuch auflisten

Liest nur market_crawl_jobs, market_segments und market_models. Aendert nichts, loest keinen Abruf aus.

Fehlversuch = Job mit status `failed` (technischer Fehler, alle Versuche verbraucht) oder `data_invalid`
(Lauf lief, Daten unbrauchbar — z. B. "alle Zeilen verworfen (land IT != DE)", "Sortierung unsicher"; Kosten
gebucht). Stornos (`cancelled`: Crawler aus, Auftrag pausiert, Doppel-Job) zaehlen nicht.
"ZULETZT GESCHEITERT" = der juengste abgeschlossene Lauf des Segments war ein Fehlversuch (das Segment liefert
gerade nichts); "inzwischen ok" = danach kam ein erfolgreicher Lauf. Uhrzeiten in deutscher Zeit.

Erster Live-Befund (prod2, 28.09.2026 abends): 570 ungueltige Laeufe in 551 von 1.176 Segmenten, ALLE
"alle Zeilen verworfen (land IT/NL/AT/BE/DK != DE)" — der Laenderfehler vor dem Filter cn=DE (Commit cdb5894,
ausgerollt 28.09. 16:25 prod2 / 18:38 prod1). Deshalb --seit: zeigt, ob nach dem Rollout noch Laeufe scheitern.
"""
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
FEHL = ("failed", "data_invalid")
ABGESCHLOSSEN = ("completed", "failed", "data_invalid")
BERLIN = ZoneInfo("Europe/Berlin")
_LAND = re.compile(r"\bland ([A-Z]{2}) != DE\b")
_ZAHLEN = re.compile(r"\s*\(.*$", re.S)      # Klammerzusatz weg: "(20 von 20: land IT ...)", "(Prozess weg?)"


def grund_kurz(fehler: Optional[str]) -> str:
    """Fehlertext auf seine Familie kuerzen: "Sortierung unsicher: Luecke bei 3" -> "Sortierung unsicher",
    "alle Zeilen verworfen (20 von 20: land IT != DE; ...)" -> "alle Zeilen verworfen"."""
    t = (fehler or "").strip()
    if not t:
        return "(ohne Grund)"
    t = _ZAHLEN.sub("", t)
    return t.split(":")[0].split(" — ")[0].strip()[:60] or "(ohne Grund)"


def laender(fehler: Optional[str]) -> List[str]:
    """Verworfene Laender aus dem Fehlertext (ein Lauf zaehlt je Land einmal)."""
    return sorted(set(_LAND.findall(fehler or "")))


def lokal(iso: Optional[str]) -> str:
    """ISO-Zeit (UTC) -> 'JJJJ-MM-TT HH:MM' deutsche Zeit; leer, wenn nicht lesbar."""
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)[:16]
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M")


def seit_utc(text: str) -> str:
    """'JJJJ-MM-TT HH:MM' (deutsche Zeit) -> ISO in UTC, vergleichbar mit finished_at."""
    t = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=BERLIN)
    return t.astimezone(timezone.utc).isoformat()


def bericht(db, tage: Optional[int] = None, seit: Optional[str] = None) -> Dict[str, Any]:
    filt: Dict[str, Any] = {"status": {"$in": list(FEHL)}}
    ab = None
    if tage:
        ab = (datetime.now(timezone.utc) - timedelta(days=int(tage))).strftime("%Y-%m-%d")
        filt["tag"] = {"$gte": ab}
    seit_iso = seit_utc(seit) if seit else None
    if seit_iso:
        filt["finished_at"] = {"$gte": seit_iso}
    # Gegenprobe: erfolgreiche Laeufe im selben Zeitraum — "0 Fehlversuche" sagt nichts, wenn gar nichts lief
    erfolge = db.market_crawl_jobs.count_documents({**filt, "status": "completed"})
    je_seg: Dict[str, Dict[str, Any]] = {}
    gruende: Counter = Counter()
    land_laeufe: Counter = Counter()
    laeufe: Counter = Counter()
    je_tag: Counter = Counter()
    for j in db.market_crawl_jobs.find(
            filt, {"_id": 0, "segment_id": 1, "model_id": 1, "status": 1, "error": 1, "tag": 1, "finished_at": 1}
    ).sort([("finished_at", 1), ("tag", 1)]):
        e = je_seg.setdefault(j["segment_id"], {"segment_id": j["segment_id"], "model_id": j.get("model_id"),
                                                "n": 0, "failed": 0, "invalid": 0, "letzter_grund": "",
                                                "letzter_tag": "", "letzte_zeit": ""})
        e["n"] += 1
        e["failed" if j["status"] == "failed" else "invalid"] += 1
        e["letzter_grund"] = (j.get("error") or "")[:120]
        e["letzter_tag"] = (j.get("tag") or "")[:10]
        e["letzte_zeit"] = lokal(j.get("finished_at"))
        gruende[grund_kurz(j.get("error"))] += 1
        for land in laender(j.get("error")):
            land_laeufe[land] += 1
        laeufe[j["status"]] += 1
        je_tag[(j.get("tag") or "")[:10]] += 1
    # Juengster abgeschlossener Lauf je betroffenem Segment (ohne Zeitgrenze: "inzwischen ok" darf juenger sein)
    letzte = {d["_id"]: d for d in db.market_crawl_jobs.aggregate([
        {"$match": {"status": {"$in": list(ABGESCHLOSSEN)}, "segment_id": {"$in": list(je_seg)}}},
        {"$sort": {"finished_at": -1, "tag": -1}},
        {"$group": {"_id": "$segment_id", "status": {"$first": "$status"}, "tag": {"$first": "$tag"},
                    "finished_at": {"$first": "$finished_at"}}},
    ])} if je_seg else {}
    segs = {s["id"]: s for s in db.market_segments.find(
        {"id": {"$in": list(je_seg)}},
        {"_id": 0, "id": 1, "model_id": 1, "label": 1, "ez_label": 1, "km_label": 1, "enabled": 1})}
    modelle = {m["id"]: m for m in db.market_models.find({}, {"_id": 0, "id": 1, "label": 1, "status": 1})}
    for sid, e in je_seg.items():
        s = segs.get(sid) or {}
        lz = letzte.get(sid) or {}
        e["zuletzt_gescheitert"] = lz.get("status") in FEHL
        e["letzter_lauf_tag"] = (lz.get("tag") or "")[:10]
        e["letzter_lauf_zeit"] = lokal(lz.get("finished_at"))
        e["ez_label"] = s.get("ez_label") or ""
        e["km_label"] = s.get("km_label") or ""
        e["enabled"] = bool(s.get("enabled")) if s else None
        e["model_id"] = e.get("model_id") or s.get("model_id")
    je_modell: Dict[str, Dict[str, Any]] = {}
    for e in je_seg.values():
        mid = e.get("model_id") or "?"
        m = modelle.get(mid) or {}
        z = je_modell.setdefault(mid, {"model_id": mid, "label": m.get("label") or (segs.get(e["segment_id"]) or {}).get("label") or mid,
                                       "status": m.get("status") or "?", "n": 0, "segmente": [], "zuletzt_gescheitert": 0})
        z["n"] += e["n"]
        z["segmente"].append(e)
        z["zuletzt_gescheitert"] += int(e["zuletzt_gescheitert"])
    for z in je_modell.values():
        z["segmente"].sort(key=lambda e: (-int(e["zuletzt_gescheitert"]), -e["n"], e["ez_label"], e["km_label"]))
    reihen = sorted(je_modell.values(), key=lambda z: (-z["zuletzt_gescheitert"], -z["n"], z["label"]))
    werte = list(je_seg.values())
    return {
        "ab_tag": ab, "seit": lokal(seit_iso) if seit_iso else None,
        "laeufe": sum(laeufe.values()), "laeufe_failed": laeufe.get("failed", 0), "laeufe_invalid": laeufe.get("data_invalid", 0),
        "erfolge": erfolge,
        "je_tag": sorted(je_tag.items()),
        "segmente_aktiv": db.market_segments.count_documents({"enabled": True}),
        "segmente_mit_fehler": len(werte),
        "mehrfach": sum(1 for e in werte if e["n"] >= 2),
        "dreifach": sum(1 for e in werte if e["n"] >= 3),
        "zuletzt_gescheitert": sum(1 for e in werte if e["zuletzt_gescheitert"]),
        "inzwischen_ok": sum(1 for e in werte if not e["zuletzt_gescheitert"]),
        "nur_ungueltig": sum(1 for e in werte if e["failed"] == 0),
        "nur_fehler": sum(1 for e in werte if e["invalid"] == 0),
        "gruende": gruende.most_common(10),
        "laender": land_laeufe.most_common(),
        "je_modell": reihen,
    }


def _zeile(e: Dict[str, Any]) -> str:
    art = []
    if e["invalid"]:
        art.append(f"{e['invalid']} ungueltig")
    if e["failed"]:
        art.append(f"{e['failed']} Fehler")
    stand = ("ZULETZT GESCHEITERT" if e["zuletzt_gescheitert"]
             else f"inzwischen ok (letzter Lauf {e['letzter_lauf_zeit'] or e['letzter_lauf_tag'] or '?'} erfolgreich)")
    seg = " · ".join(x for x in (e["ez_label"], e["km_label"]) if x) or e["segment_id"]
    aus = "" if e.get("enabled") in (True, None) else " [Segment inaktiv]"
    return (f"    {seg}{aus}: {e['n']} Fehlversuch(e) ({', '.join(art)}), letzter {e['letzte_zeit'] or e['letzter_tag'] or '?'}"
            f" — {stand}\n        Grund: {e['letzter_grund'] or '(ohne Grund)'}")


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    tage = None
    seit = None
    alle = False
    i = 0
    while i < len(argv):
        if argv[i] == "--tage" and i + 1 < len(argv) and argv[i + 1].isdigit():
            tage = int(argv[i + 1])
            i += 2
        elif argv[i] == "--seit" and i + 1 < len(argv):
            try:
                seit_utc(argv[i + 1])
            except ValueError:
                print("--seit erwartet 'JJJJ-MM-TT HH:MM' (deutsche Zeit), z. B. --seit \"2026-09-28 18:40\"")
                return 2
            seit = argv[i + 1]
            i += 2
        elif argv[i] == "--alle":
            alle = True
            i += 1
        else:
            print(__doc__)
            return 2
    db = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10000)[DB_NAME]
    b = bericht(db, tage, seit)
    zeitraum = []
    if tage:
        zeitraum.append(f"seit Tag {b['ab_tag']} (letzte {tage} Tage)")
    if seit:
        zeitraum.append(f"Laeufe beendet seit {b['seit']} Uhr")
    print(f"Datenbank: {DB_NAME}   Zeitraum: {' und '.join(zeitraum) or 'alle Tage'}")
    print(f"Fehlversuche: {b['laeufe']} Laeufe in {b['segmente_mit_fehler']} Segmenten "
          f"(von {b['segmente_aktiv']} aktiven)   —   erfolgreiche Laeufe im selben Zeitraum: {b['erfolge']}")
    print(f"  mehrfach (>= 2): {b['mehrfach']} Segmente, >= 3: {b['dreifach']}")
    print(f"  zuletzt gescheitert (juengster Lauf kaputt): {b['zuletzt_gescheitert']}   "
          f"inzwischen ok: {b['inzwischen_ok']}")
    print(f"  Art: {b['laeufe_invalid']} x Daten unbrauchbar (data_invalid), {b['laeufe_failed']} x technischer Fehler (failed)")
    if b["je_tag"]:
        print("  je Tag: " + ", ".join(f"{t or '?'}: {n}" for t, n in b["je_tag"][-7:]))
    if b["gruende"]:
        print("Haeufigste Gruende:")
        for g, n in b["gruende"]:
            print(f"  {n:5d} x {g}")
    if b["laender"]:
        print("Verworfene Laender (Laeufe, in denen das Land vorkam): "
              + ", ".join(f"{land} {n}" for land, n in b["laender"]))
    grenze = 1 if alle else 2
    print(f"\nJe Marke/Modell (Segmente mit >= {grenze} Fehlversuch(en)"
          f"{'' if alle else '; --alle zeigt auch einzelne'}):")
    gezeigt = 0
    for z in b["je_modell"]:
        segs = [e for e in z["segmente"] if e["n"] >= grenze]
        if not segs:
            continue
        gezeigt += 1
        print(f"\n{z['label']} ({z['status']}) — {z['n']} Fehlversuche in {len(z['segmente'])} Segmenten, "
              f"{z['zuletzt_gescheitert']} zuletzt gescheitert")
        for e in segs:
            print(_zeile(e))
    if not gezeigt:
        print("  keine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
