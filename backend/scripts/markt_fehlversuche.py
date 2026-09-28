# -*- coding: utf-8 -*-
"""Fehlversuche der Marktbeobachtung je Marke/Modell/Segment (Frage Ahmad 28.09.2026: "wie viele Segmente
sind mehrfach fehlgeschlagen — welche Marke, Modell und Segmente haben bislang nicht geklappt").

    python -X utf8 scripts/markt_fehlversuche.py             # alle Tage
    python -X utf8 scripts/markt_fehlversuche.py --tage 7    # nur die letzten 7 Tage
    python -X utf8 scripts/markt_fehlversuche.py --alle      # auch Segmente mit nur EINEM Fehlversuch auflisten

Liest nur market_crawl_jobs, market_segments und market_models. Aendert nichts, loest keinen Abruf aus.

Fehlversuch = Job mit status `failed` (technischer Fehler, alle Versuche verbraucht) oder `data_invalid`
(Lauf lief, Daten unbrauchbar — z. B. "Sortierung unsicher", Zeilenfilter defekt; Kosten gebucht).
Stornos (`cancelled`: Crawler aus, Auftrag pausiert, Doppel-Job) zaehlen nicht.
"zuletzt gescheitert" = der juengste abgeschlossene Lauf des Segments war ein Fehlversuch (das Segment
liefert gerade nichts); "inzwischen ok" = danach kam ein erfolgreicher Lauf.
"""
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
FEHL = ("failed", "data_invalid")
ABGESCHLOSSEN = ("completed", "failed", "data_invalid")


def grund_kurz(fehler: Optional[str]) -> str:
    """Fehlertext auf seine Familie kuerzen: "Sortierung unsicher: Luecke bei Position 3" -> "Sortierung unsicher"."""
    t = (fehler or "").strip()
    if not t:
        return "(ohne Grund)"
    return t.split(":")[0].split(" — ")[0].strip()[:60]


def bericht(db, tage: Optional[int] = None) -> Dict[str, Any]:
    filt: Dict[str, Any] = {"status": {"$in": list(FEHL)}}
    ab = None
    if tage:
        ab = (datetime.now(timezone.utc) - timedelta(days=int(tage))).strftime("%Y-%m-%d")
        filt["tag"] = {"$gte": ab}
    je_seg: Dict[str, Dict[str, Any]] = {}
    gruende: Counter = Counter()
    laeufe = Counter()
    for j in db.market_crawl_jobs.find(
            filt, {"_id": 0, "segment_id": 1, "model_id": 1, "status": 1, "error": 1, "tag": 1, "finished_at": 1}
    ).sort([("finished_at", 1), ("tag", 1)]):
        e = je_seg.setdefault(j["segment_id"], {"segment_id": j["segment_id"], "model_id": j.get("model_id"),
                                                "n": 0, "failed": 0, "invalid": 0, "letzter_grund": "",
                                                "letzter_tag": ""})
        e["n"] += 1
        e["failed" if j["status"] == "failed" else "invalid"] += 1
        e["letzter_grund"] = (j.get("error") or "")[:120]
        e["letzter_tag"] = (j.get("tag") or "")[:10]
        gruende[grund_kurz(j.get("error"))] += 1
        laeufe[j["status"]] += 1
    # Juengster abgeschlossener Lauf je betroffenem Segment (ohne Tagesgrenze: "inzwischen ok" darf juenger sein)
    letzte = {d["_id"]: d for d in db.market_crawl_jobs.aggregate([
        {"$match": {"status": {"$in": list(ABGESCHLOSSEN)}, "segment_id": {"$in": list(je_seg)}}},
        {"$sort": {"finished_at": -1, "tag": -1}},
        {"$group": {"_id": "$segment_id", "status": {"$first": "$status"}, "tag": {"$first": "$tag"}}},
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
        "ab_tag": ab,
        "laeufe": sum(laeufe.values()), "laeufe_failed": laeufe.get("failed", 0), "laeufe_invalid": laeufe.get("data_invalid", 0),
        "segmente_aktiv": db.market_segments.count_documents({"enabled": True}),
        "segmente_mit_fehler": len(werte),
        "mehrfach": sum(1 for e in werte if e["n"] >= 2),
        "dreifach": sum(1 for e in werte if e["n"] >= 3),
        "zuletzt_gescheitert": sum(1 for e in werte if e["zuletzt_gescheitert"]),
        "inzwischen_ok": sum(1 for e in werte if not e["zuletzt_gescheitert"]),
        "nur_ungueltig": sum(1 for e in werte if e["failed"] == 0),
        "nur_fehler": sum(1 for e in werte if e["invalid"] == 0),
        "gruende": gruende.most_common(10),
        "je_modell": reihen,
    }


def _zeile(e: Dict[str, Any]) -> str:
    art = []
    if e["invalid"]:
        art.append(f"{e['invalid']} ungueltig")
    if e["failed"]:
        art.append(f"{e['failed']} Fehler")
    stand = ("ZULETZT GESCHEITERT" if e["zuletzt_gescheitert"]
             else f"inzwischen ok (letzter Lauf {e['letzter_lauf_tag'] or '?'} erfolgreich)")
    seg = " · ".join(x for x in (e["ez_label"], e["km_label"]) if x) or e["segment_id"]
    aus = "" if e.get("enabled") in (True, None) else " [Segment inaktiv]"
    return (f"    {seg}{aus}: {e['n']} Fehlversuch(e) ({', '.join(art)}), letzter {e['letzter_tag'] or '?'} — "
            f"{stand}\n        Grund: {e['letzter_grund'] or '(ohne Grund)'}")


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    tage = None
    alle = False
    i = 0
    while i < len(argv):
        if argv[i] == "--tage" and i + 1 < len(argv) and argv[i + 1].isdigit():
            tage = int(argv[i + 1])
            i += 2
        elif argv[i] == "--alle":
            alle = True
            i += 1
        else:
            print(__doc__)
            return 2
    db = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10000)[DB_NAME]
    b = bericht(db, tage)
    zeitraum = f"seit {b['ab_tag']} (letzte {tage} Tage)" if tage else "alle Tage"
    print(f"Datenbank: {DB_NAME}   Zeitraum: {zeitraum}")
    print(f"Fehlversuche: {b['laeufe']} Laeufe in {b['segmente_mit_fehler']} Segmenten "
          f"(von {b['segmente_aktiv']} aktiven)")
    print(f"  mehrfach (>= 2): {b['mehrfach']} Segmente, >= 3: {b['dreifach']}")
    print(f"  zuletzt gescheitert (juengster Lauf kaputt): {b['zuletzt_gescheitert']}   "
          f"inzwischen ok: {b['inzwischen_ok']}")
    print(f"  Art: {b['laeufe_invalid']} x Daten unbrauchbar (data_invalid), {b['laeufe_failed']} x technischer Fehler (failed)")
    if b["gruende"]:
        print("Haeufigste Gruende:")
        for g, n in b["gruende"]:
            print(f"  {n:5d} x {g}")
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
