# -*- coding: utf-8 -*-
"""Zulassung bei Übergabe (Wunsch Ahmad 24.11.2026).

Das Vertragsfeld `zulassung` gibt es seit Runde 22 ("" | "angemeldet" |
"abgemeldet"; leer = unbekannt). Seit 24.11.2026 steht bei einem bekannten
Wert zusätzlich ein Satz im Punkt "Zusätzliche Vereinbarung" des Vertrags.
Bei "unbekannt" (leer, fehlt bei Altverträgen) steht kein Satz — der Dialog
zeigt dann einen Hinweis.
"""

SATZ_ABGEMELDET = "Das Fahrzeug wird abgemeldet übergeben."
SATZ_ANGEMELDET = ("Das Fahrzeug wird angemeldet übergeben. Der Käufer meldet es innerhalb "
                   "von drei Werktagen nach der Übergabe ab.")

_SAETZE = {"abgemeldet": SATZ_ABGEMELDET, "angemeldet": SATZ_ANGEMELDET}


def zulassung_satz(contract: dict) -> str:
    """Satz für "Zusätzliche Vereinbarung" — leer bei unbekannt/fehlend/unbekanntem Wert."""
    wert = str((contract or {}).get("zulassung") or "").strip().lower()
    return _SAETZE.get(wert, "")


def zusaetzliche_vereinbarung_text(contract: dict) -> str:
    """Freitext "Sonstige Vereinbarung" plus Zulassungssatz (eigener Absatz)."""
    teile = [(contract or {}).get("sonstige_vereinbarung") or "", zulassung_satz(contract)]
    return "\n\n".join(t.strip() for t in teile if t and t.strip())
