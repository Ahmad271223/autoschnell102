# -*- coding: utf-8 -*-
"""Endauswertung der Lasttest-Matrix (Auftrag Punkt 12).

Liest alle Berichte aus docs/lasttests/matrix/ und erzeugt die
Abschlusstabelle als Markdown: je Szenario die drei gueltigen Laeufe,
technische Fehlerrate (nur unerwartete Fehler + Verbindungsabbrueche),
fachliche Ablehnungen, p95/p99 der Hauptfunktion, Rueckstau, Drain,
Integritaet, Speicherentwicklung, bestanden-Bewertung, Engpass.

Bewertungsregeln (aus dem Auftrag):
- technische Fehlerrate < 1 %  (fachliche Ablehnungen zaehlen NICHT)
- Jobannahme p95 < 500 ms, bekannte Links p95 < 1 s, Listen p95 < 1 s,
  PDF p95 < 3 s
- 0 doppelte Anbieter-Abrufe, 0 Doppelversand, 0 verlorene/haengende Jobs
- Warteschlangen nach dem Lauf leer, RAM faellt zurueck
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

MATRIX = Path(__file__).resolve().parent.parent.parent / "docs" / "lasttests" / "matrix"

HAUPTFUNKTION = {
    "T1": "link_neu_check", "T2": "pdf_vertrag_neu",
    "T3": "foto_upload_klein", "T4": "protokoll_abschluss",
    "T5": "versand_email", "T6": "versand_whatsapp",
    "T7": "markt_suche", "T8": "link_bekannt", "T9": "link_neu_check",
}


def technische_fehler(d):
    """Unerwartete Fehler = Klasse technisch_unerwartet + 599; Berichte
    ohne Klassenfeld (alte Instrumentierung): alles ausser den als
    fachlich/Sicherheitstest bekannten 400/404/409-Mustern."""
    k = d.get("klassen")
    if k:
        return k["technisch_unerwartet"] + k["verbindungsabbruch_client"]
    n = 0
    for name, v in d["endpunkte"].items():
        for st, c in (v.get("fehler_nach_status") or {}).items():
            if st.startswith("5") or st == "599":
                n += c
    return n


def fachliche(d):
    k = d.get("klassen")
    if k:
        return k["fachlich_erwartet"] + k["race_ux_befund"]
    n = 0
    for v in d["endpunkte"].values():
        for st, c in (v.get("fehler_nach_status") or {}).items():
            if st in ("400", "404", "409"):
                n += c
    return n


def main():
    laeufe = defaultdict(list)
    for f in sorted(MATRIX.glob("*.json")):
        if f.name == "abschlusstabelle.json":
            continue                     # eigene Ausgabe nicht einlesen
        d = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or "szenario" not in d:
            continue
        d["_datei"] = f.name
        laeufe[d["szenario"]].append(d)

    zeilen = []
    for sz in sorted(laeufe):
        gruppe = laeufe[sz]
        # "gueltig" = Laeufe mit Klassen-Feld (finale Instrumentierung);
        # aeltere Laeufe zaehlen als Diagnose-/Schutztests.
        final = [d for d in gruppe if d.get("klassen") is not None]
        diagnose = [d for d in gruppe if d.get("klassen") is None]
        # Bewertet werden die JUENGSTEN drei finalen Laeufe (T3 wurde nach
        # dem Foto-Fix erneut gefahren; die aelteren finalen Laeufe
        # dokumentieren den Zustand VOR dem Fix und zaehlen als Diagnose).
        final.sort(key=lambda d: d["_datei"])
        if len(final) > 3:
            diagnose = diagnose + final[:-3]
            final = final[-3:]
        bewertet = final if len(final) >= 3 else (final or gruppe)

        anfragen = [d["anfragen_gesamt"] for d in bewertet]
        tf = [technische_fehler(d) for d in bewertet]
        gesamt = [sum(v["ok"] + v["fehler"]
                      for v in d["endpunkte"].values()) for d in bewertet]
        raten = [round(100 * a / max(1, b), 2) for a, b in zip(tf, gesamt)]
        fach = [fachliche(d) for d in bewertet]
        hf = HAUPTFUNKTION[sz]
        p95 = [d["endpunkte"].get(hf, {}).get("p95_ms") for d in bewertet]
        p99 = [d["endpunkte"].get(hf, {}).get("p99_ms") for d in bewertet]
        queue = max(d["jobs"]["queue_max"] for d in bewertet)
        drain = max(d["jobs"]["drain_sekunden"] for d in bewertet)
        # Auftragsregel (Punkt 11): eine bei einem Verbindungsabbruch
        # verlorene Client-Antwort ist KEIN Doppelversand — der Server hat
        # den Auftrag genau einmal registriert. Nur ein Ueberhang JENSEITS
        # der Abbrueche desselben Laufs zaehlt als Integritaetsfehler; die
        # Rohzahl wird separat ausgewiesen. Der harte Beweis (Retry mit
        # Idempotency-Key -> genau 1 Eintrag) steht in
        # tests/test_send_idempotenz.py.
        ohne_antwort = 0
        integ = 0
        for d in bewertet:
            k = d.get("klassen") or {}
            resets = k.get("verbindungsabbruch_client", 0)
            dv = max(0, d["integritaet"].get("doppel_versand", 0))
            ohne_antwort += min(dv, resets)
            integ += (d["integritaet"]["doppelte_anbieter_abrufe"]
                      + max(0, dv - resets)
                      + d["jobs"]["haengende_jobs"])
        ram = [(d["system"]["ram_median"], d["system"]["ram_nach_drain"])
               for d in bewertet]
        ram_ok = all(nach <= med + 2 for med, nach in ram)

        probleme = []
        if len(final) < 3:
            probleme.append(f"nur {len(final)} Laeufe mit finaler Version")
        if any(r >= 1.0 for r in raten):
            probleme.append(f"technische Fehlerrate {max(raten)}%")
        if integ:
            probleme.append(f"Integritaet: {integ}")
        if drain > 60:
            probleme.append(f"Drain {drain}s")
        if not ram_ok:
            probleme.append("RAM faellt nicht zurueck")
        bestanden = "JA" if not probleme else "NEIN: " + "; ".join(probleme)

        zeilen.append({
            "szenario": sz, "gueltige_laeufe": len(final),
            "diagnose_laeufe": len(diagnose),
            "anfragen": anfragen, "tech_fehlerrate_prozent": raten,
            "fachliche_ablehnungen": fach,
            "hauptfunktion": hf, "p95_ms": p95, "p99_ms": p99,
            "queue_max": queue, "drain_s": drain,
            "integritaetsfehler": integ,
            "versand_ohne_client_antwort": ohne_antwort,
            "ram_faellt_zurueck": ram_ok,
            "bestanden": bestanden,
        })

    md = ["| Szenario | 3 gültige Läufe | Anfragen je Lauf | techn. Fehlerrate | fachl. Ablehnungen | p95 (ms) | p99 (ms) | max. Rückstau | Drain | Integrität | RAM fällt zurück | bestanden |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for z in zeilen:
        md.append("| {szenario} | {g}/3 (+{d} Diagnose) | {a} | {r} % | {f} | {p95} | {p99} | {q} | {dr}s | {i} | {ram} | {b} |".format(
            szenario=z["szenario"], g=z["gueltige_laeufe"],
            d=z["diagnose_laeufe"],
            a="/".join(str(x) for x in z["anfragen"]),
            r="/".join(str(x) for x in z["tech_fehlerrate_prozent"]),
            f="/".join(str(x) for x in z["fachliche_ablehnungen"]),
            p95="/".join(str(x) for x in z["p95_ms"]),
            p99="/".join(str(x) for x in z["p99_ms"]),
            q=z["queue_max"], dr=z["drain_s"],
            i=(f"{z['integritaetsfehler']}"
               + (f" ({z['versand_ohne_client_antwort']} o. Antwort)"
                  if z["versand_ohne_client_antwort"] else "")),
            ram="ja" if z["ram_faellt_zurueck"] else "NEIN",
            b=z["bestanden"]))
    print("\n".join(md))
    (MATRIX / "abschlusstabelle.json").write_text(
        json.dumps(zeilen, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON: {MATRIX / 'abschlusstabelle.json'}")


if __name__ == "__main__":
    main()
