# -*- coding: utf-8 -*-
"""Meta-Test (Pruefbericht 20.09.2026, T-01): Jede Route von server.app traegt
die Abhaengigkeit, die ihr Pfad verlangt.

Die meisten Tests rufen Routenfunktionen direkt auf — die Depends-Kette
(current_super_admin, current_haendler, require_active_sub, ...) wird dabei
nie durchlaufen. Dieser Test geht ueber ALLE Routen der App und prueft je
Pfadpraefix die erwartete Sperre. Eine neue Route ohne passende Sperre laesst
den Test scheitern; Ausnahmen (Anmeldung, Health, oeffentliche Links,
signierte Dateien) stehen ausdruecklich unten.

Kein Server, keine Datenbank: nur `import server` und die Routentabelle.
"""
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
# deps.py liest MONGO_URL/DB_NAME beim Import — Werte vorgeben, falls leer.
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "autoschnell")
# Der Marktplatz-Schalter aendert nur das Verhalten der Dependency, nicht die
# Kette — die Routen tragen `marktplatz_freigeschaltet` in beiden Faellen.

from fastapi.routing import APIRoute  # noqa: E402

import server  # noqa: E402


# ------------------------------------------------------------ Routentabelle
def _kette(dependant, acc):
    """Namen aller Abhaengigkeiten (rekursiv), Sicherheitsobjekte ohne Namen
    (HTTPBearer) werden ausgelassen."""
    for d in dependant.dependencies:
        name = getattr(d.call, "__name__", None)
        if name:
            acc.add(name)
        _kette(d, acc)
    return acc


def _sammeln(routen, out):
    """FastAPI < 0.141: APIRoute direkt in app.routes. Ab 0.141 liegen
    eingebundene Router als _IncludedRouter dort; effective_candidates()
    liefert die Routen MIT den Router-Abhaengigkeiten (include_router(...,
    dependencies=[...]))."""
    for r in routen:
        if isinstance(r, APIRoute):
            out.append((r.path, set(r.methods or ()), _kette(r.dependant, set())))
        elif hasattr(r, "effective_candidates"):
            _sammeln(r.effective_candidates(), out)
        elif hasattr(r, "dependant") and hasattr(r, "path"):
            out.append((r.path, set(r.methods or ()), _kette(r.dependant, set())))
        # Mounts/Statisches: nichts zu pruefen


ROUTEN = []
_sammeln(server.app.routes, ROUTEN)
ROUTEN_JE_PFAD = {}
for _p, _m, _d in ROUTEN:
    ROUTEN_JE_PFAD.setdefault(_p, []).append((_m, _d))

SCHREIBEND = {"POST", "PUT", "PATCH", "DELETE"}
NUTZER_DEPS = {"current_user", "current_driver", "marktplatz_besucher"}


def _routen(praefix=None, exakt=None):
    return [(p, m, d) for p, m, d in ROUTEN
            if (praefix and p.startswith(praefix)) or (exakt and p == exakt)]


def _ids(liste):
    return [f"{'/'.join(sorted(m))} {p}" for p, m, _ in liste]


# --------------------------------------------------------------- Ausnahmen
# Routen OHNE Nutzer-Abhaengigkeit — jede davon ist bewusst oeffentlich:
OEFFENTLICH = {
    "/api/",                                  # Lebenszeichen
    "/api/features",                          # Go-Live-Schalter fuer die Oberflaeche
    "/api/health", "/api/ready",              # Lastverteiler / Monitoring
    "/api/client-errors",                     # Fehlerberichte der Oberflaeche
    "/api/zugang-anfrage",                    # oeffentliche Zugangs-Anfrage
    "/api/auth/login", "/api/auth/login/mfa",
    "/api/auth/register",                     # antwortet 410 (Kontonummer, Schritt 5)
    "/api/auth/password-reset/request", "/api/auth/password-reset/confirm",
    "/api/driver/login", "/api/driver/register",     # register: 410
    "/api/buyer/login", "/api/buyer/register",       # register: 410
    "/api/public/vertrag/{token}",            # Vertragslink mit Token in der Adresse
    "/api/marktplatz/haendler", "/api/marktplatz/listings",
    "/api/marktplatz/haendler/{slug}",        # Marktplatz ohne Anmeldung (Wunsch 09/2026)
    "/api/files/{key:path}",                  # signierte Dateilinks (dateien.signatur_gueltig)
    "/api/bild",                              # Bild-Proxy nur mit Signatur (bild_proxy)
}
# ... davon tragen diese trotzdem eine Kette (Besucher ODER Kaeufer, bzw. der
# Marktplatz-Schalter) — sie zaehlen nicht als "ganz ohne Abhaengigkeit":
OEFFENTLICH_MIT_KETTE = {"/api/marktplatz/haendler", "/api/marktplatz/listings",
                         "/api/marktplatz/haendler/{slug}",
                         "/api/buyer/login", "/api/buyer/register"}

# Admin-Bereich: alles mindestens current_admin; SCHREIBENDE Routen der
# Verwaltung nur der Super-Admin (seit Runde 12 gibt es keinen anderen Admin;
# current_admin bleibt fuer die Lese- und Selbstverwaltungsrouten stehen).
ADMIN_SCHREIBEND_NUR_ADMIN = {
    "/api/admin/me/password",                 # eigenes Passwort
    "/api/admin/me/mfa/einrichten", "/api/admin/me/mfa/aktivieren",
    "/api/admin/me/mfa/deaktivieren", "/api/admin/me/mfa/codes-neu",   # eigener 2. Faktor
    "/api/admin/me/mfa/wechsel",              # Geraetewechsel (RP-556, Welle B3)
    "/api/admin/errors", "/api/admin/errors/{error_id}",              # Fehlerliste pflegen
    "/api/admin/buyers/{buyer_id}/ustid-pruefen",                     # nur Abfrage bei VIES
}

# Firmenbereich: Chefsache (current_haendler bzw. current_chef) je Pfad.
CHEF_ROUTEN = {
    "/api/vehicles/{vehicle_id}/decision", "/api/vehicles/{vehicle_id}/bestand",
    "/api/vehicles/{vehicle_id}/apply-deviations", "/api/vehicles/manual",
    "/api/vehicles/manual/{vehicle_id}", "/api/vehicles/{vehicle_id}/besitzer",
    "/api/dealer/sucher", "/api/dealer/sucher/{sucher_id}",
    "/api/dealer/sucher/{sucher_id}/abo-anfrage", "/api/dealer/sucher-plans",
    "/api/dealer/sale-plan", "/api/dealer/sale-plan/upgrade-request",
    "/api/dealer/marketplace-profile", "/api/dealer/invites",
    "/api/dealer/invites/{invite_id}", "/api/dealer/network/members",
    "/api/dealer/network/members/{buyer_user_id}",
    "/api/dealer/interessen", "/api/dealer/interessen/anzahl",
    "/api/interessen/{interest_id}/antwort",
    "/api/appointments/fahrer-abgelehnt/anzahl",
    "/api/protocols/zur-freigabe", "/api/protocols/zur-freigabe/anzahl",
    "/api/protocols/rueckfragen-offen",
    "/api/protocols/{protocol_id}/freigabe",
    "/api/protocols/{protocol_id}/ki-bewertung", "/api/protocols/{protocol_id}/ki-bewertung/neu",   # KI-Abholbewertung (25.09.2026)
}
# Sucher-Funktionen (kostenpflichtig): Abo-Pflicht ueber require_active_sub.
ABO_ROUTEN = {
    "/api/mobile/compare", "/api/mobile/live-counter/{ad_id}",
    "/api/listings/ingest", "/api/listings/check", "/api/listings/check/{job_id}",
    "/api/listings/resolve", "/api/manual/search",
    "/api/contracts/preview", "/api/contracts",              # POST (GET: nur Firma)
    "/api/contracts/ki-schadennachlass",                     # KI-Schadennachlass Vertrag (26.09.2026)
    "/api/contracts/{contract_id}/send", "/api/contracts/{contract_id}/folge-mail",
}
# Beweisdokumente: die ID-Routen sind firmenuebergreifend (ein Dokument je
# Inserat, alle Firmen teilen es) — Zugriff prueft die Route selbst.
BEWEIS_NUR_NUTZER = {"/api/beweise/{beweis_id}", "/api/beweise/{beweis_id}/pdf"}
# Snapshots (Altbestand): _snapshot_nutzer laesst Firma UND Fahrer zu.
SNAPSHOT_ROUTEN = {"/api/snapshots/{snap_id}", "/api/snapshots/{snap_id}/{kind}"}


# ------------------------------------------------------------------ Tests
def test_routentabelle_ist_nicht_leer():
    assert len(ROUTEN) > 150, "Routen nicht gefunden — FastAPI-Struktur geaendert?"


def test_jede_route_ohne_nutzer_abhaengigkeit_ist_bekannt():
    """Kern des Meta-Tests: eine neue Route ohne Sperre faellt hier auf."""
    ohne = sorted({p for p, _, d in ROUTEN if not (d & NUTZER_DEPS)})
    unbekannt = [p for p in ohne if p not in OEFFENTLICH]
    assert not unbekannt, f"Routen ohne Nutzer-Abhaengigkeit, die nicht als oeffentlich gelistet sind: {unbekannt}"
    # ... und umgekehrt: jede gelistete Ausnahme gibt es noch (sonst veraltet die Liste)
    fehlt = [p for p in OEFFENTLICH if p not in ROUTEN_JE_PFAD]
    assert not fehlt, f"Ausnahmen ohne Route (Liste OEFFENTLICH veraltet): {fehlt}"
    # Und die "ganz nackten" sind genau die ohne Kette
    ganz_ohne = sorted({p for p, _, d in ROUTEN if not d})
    assert set(ganz_ohne) == OEFFENTLICH - OEFFENTLICH_MIT_KETTE, ganz_ohne


@pytest.mark.parametrize("pfad,methoden,deps", _routen("/api/admin/"),
                         ids=_ids(_routen("/api/admin/")))
def test_admin_routen(pfad, methoden, deps):
    assert "current_admin" in deps, f"{pfad}: nicht durch current_admin gesperrt"
    if methoden & SCHREIBEND and pfad not in ADMIN_SCHREIBEND_NUR_ADMIN:
        assert "current_super_admin" in deps, \
            f"{pfad}: schreibende Verwaltungsroute ohne current_super_admin"


def test_admin_ausnahmen_existieren_noch():
    fehlt = [p for p in ADMIN_SCHREIBEND_NUR_ADMIN if p not in ROUTEN_JE_PFAD]
    assert not fehlt, f"Ausnahmeliste veraltet: {fehlt}"


@pytest.mark.parametrize("pfad,methoden,deps", _routen("/api/driver/"),
                         ids=_ids(_routen("/api/driver/")))
def test_fahrer_app_routen(pfad, methoden, deps):
    if pfad in OEFFENTLICH:
        return
    assert "current_driver" in deps, f"{pfad}: Fahrer-Route ohne current_driver"
    assert "current_user" not in deps, f"{pfad}: Fahrer-Route mit Firmen-Token erreichbar"


FIRMA_PRAEFIXE = ("/api/contracts", "/api/appointments", "/api/vehicles",
                  "/api/drivers", "/api/protocols", "/api/bestand",
                  "/api/pickup-fotos", "/api/dealer/", "/api/beweise",
                  "/api/mobile/", "/api/listings/", "/api/manual/", "/api/snapshots")
_FIRMA = [r for r in ROUTEN if r[0].startswith(FIRMA_PRAEFIXE)]


@pytest.mark.parametrize("pfad,methoden,deps", _FIRMA, ids=_ids(_FIRMA))
def test_firmen_routen(pfad, methoden, deps):
    if pfad in BEWEIS_NUR_NUTZER or pfad in SNAPSHOT_ROUTEN or pfad == "/api/manual/makes":
        assert "current_user" in deps, pfad
        return
    assert "current_firma" in deps, f"{pfad}: Firmen-Route ohne current_firma"
    if pfad in CHEF_ROUTEN:
        assert deps & {"current_haendler", "current_chef"}, \
            f"{pfad}: Chefsache, aber nicht durch current_haendler/current_chef gesperrt"
    if pfad in ABO_ROUTEN and (pfad != "/api/contracts" or methoden & SCHREIBEND):
        assert "require_active_sub" in deps, f"{pfad}: Sucher-Funktion ohne Abo-Pflicht"


def test_chef_und_abo_listen_sind_aktuell():
    fehlt = [p for p in CHEF_ROUTEN | ABO_ROUTEN | BEWEIS_NUR_NUTZER | SNAPSHOT_ROUTEN
             if p not in ROUTEN_JE_PFAD]
    assert not fehlt, f"Listen veraltet (Routen gibt es nicht mehr): {fehlt}"


def test_sucher_duerfen_keine_chef_routen_nutzen():
    """Gegenprobe zur Liste: alle Routen mit current_haendler/current_chef
    stehen in CHEF_ROUTEN oder unter /api/resale (dort gilt es fuer alle)."""
    chef = sorted({p for p, _, d in ROUTEN
                   if d & {"current_haendler", "current_chef"}
                   and not p.startswith("/api/resale")})
    fehlt = [p for p in chef if p not in CHEF_ROUTEN]
    assert not fehlt, f"Chef-Routen, die in CHEF_ROUTEN fehlen (bitte eintragen): {fehlt}"


MARKT_PRAEFIXE = ("/api/resale", "/api/marktplatz", "/api/buyer", "/api/interessen",
                  "/api/invites", "/api/dealer/marketplace-profile", "/api/dealer/invites",
                  "/api/dealer/network", "/api/dealer/interessen", "/api/dealer/sale-plan")
_MARKT = [r for r in ROUTEN if r[0].startswith(MARKT_PRAEFIXE)]


@pytest.mark.parametrize("pfad,methoden,deps", _MARKT, ids=_ids(_MARKT))
def test_marktplatz_und_inserate_hinter_dem_schalter(pfad, methoden, deps):
    """Go-Live-Schalter (15.09.2026): MARKTPLATZ_AKTIV sperrt Marktplatz UND
    Inserieren — jede dieser Routen muss marktplatz_freigeschaltet tragen."""
    assert "marktplatz_freigeschaltet" in deps, f"{pfad}: nicht hinter MARKTPLATZ_AKTIV"
    if pfad.startswith("/api/resale"):
        assert "current_haendler" in deps, f"{pfad}: Inserieren ist Chefsache"
    if pfad.startswith(("/api/marktplatz/favoriten", "/api/buyer/interessen",
                        "/api/interessen/{interest_id}/kaeufer-antwort", "/api/invites")):
        assert "buyer_nicht_gesperrt" in deps, f"{pfad}: Betreiber-Sperre (Runde 13 C6) fehlt"
    if pfad == "/api/marktplatz/listings/{listing_id}/interesse":
        assert "require_marketplace_access" in deps


def test_auth_routen():
    for pfad in ("/api/auth/logout", "/api/auth/me"):
        for _, deps in ROUTEN_JE_PFAD[pfad]:
            assert "current_user" in deps, pfad
