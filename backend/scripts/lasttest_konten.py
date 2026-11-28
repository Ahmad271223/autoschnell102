# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026), Schritt 6 — Konten fuer die Lasttests.

Konten legt nur noch der Super-Admin an; angemeldet wird per Kontonummer.
Die Lasttests (lasttest.py, lasttest_matrix.py, lasttest_stoss.py,
lasttest_sucher.py) holen sich deshalb EIN Super-Admin-Token und legen darueber
an:
  - Firma + Chef   POST /admin/users            (plan_type none)
  - Sucher         POST /admin/dealers/{id}/sucher
  - Fahrer         POST /admin/drivers
  - Zwischenhaendler POST /admin/buyers
und melden jedes Konto genau EINMAL per Kontonummer an (Single-Session).

Der Super-Admin ist ein Wegwerf-Konto direkt in der Datenbank des Laufs
(Benutzername 'last-sa-<zufall>', passt nie zum Nummernmuster) — nie der
geseedete Betreiber-Zugang, dessen Sitzung sonst endet.

Aufraeumen: ueber die IDs, die die Admin-Routen zurueckgeben (gesammelt in
`ids`), NICHT mehr per E-Mail-Regex — Kontaktadressen sind frei und nicht
eindeutig, ein breites Loeschmuster traefe fremde Konten (Vorfall
geloeschte Alt-Vertraege).

Wie alle lasttest*-Dateien NICHT im Produktions-Image (.dockerignore).
"""
import secrets

import aiohttp

WEGE = {"auth": "/auth/login", "buyer": "/buyer/login", "driver": "/driver/login"}


def neue_ids() -> dict:
    """Sammelstelle der angelegten Konten eines Laufs."""
    return {"users": [], "dealers": [], "driver_accounts": []}


def wegwerf_super_admin(dbx, suffix: str, ids: dict) -> dict:
    """Super-Admin direkt in der Datenbank des Laufs anlegen (ohne 2FA).
    Liefert {id, username, passwort}; die ID landet in ids['users']."""
    import bcrypt
    from datetime import datetime, timezone
    passwort = f"Last-{secrets.token_hex(10)}!7"
    konto = {"id": f"last_sa_{suffix}", "username": f"last-sa-{suffix}",
             "role": "admin", "is_super_admin": True, "active": True,
             "dealer_id": None, "current_session_id": None,
             "password_hash": bcrypt.hashpw(passwort.encode(), bcrypt.gensalt()).decode(),
             "created_at": datetime.now(timezone.utc).isoformat()}
    dbx.users.insert_one(konto)
    ids["users"].append(konto["id"])
    return {"id": konto["id"], "username": konto["username"], "passwort": passwort}


async def _post(sess, url, daten, kopf=None, timeout=90):
    async with sess.post(url, json=daten, headers=kopf,
                         timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        try:
            inhalt = await r.json(content_type=None)
        except Exception:                           # noqa: BLE001
            inhalt = {"detail": (await r.text())[:300]}
        return r.status, (inhalt if isinstance(inhalt, dict) else {"daten": inhalt})


async def anmelden(sess, api, kennung, passwort, weg="auth", timeout=90):
    """POST /auth|buyer|driver/login mit {kontonummer, password}. Beim
    Super-Admin ist die Kennung sein Benutzername. -> (status, json)"""
    return await _post(sess, f"{api}{WEGE[weg]}",
                       {"kontonummer": str(kennung), "password": passwort}, timeout=timeout)


def admin_kopf(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def super_admin_anmelden(sess, api, sa: dict) -> dict:
    st, js = await anmelden(sess, api, sa["username"], sa["passwort"])
    if st != 200 or not js.get("token"):
        raise SystemExit(f"Super-Admin-Anmeldung fehlgeschlagen ({st}): {str(js)[:200]}")
    return admin_kopf(js["token"])


def _ohne_leere(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "")}


async def firma_anlegen(sess, api, admin_h, ids, passwort, firmenname,
                        kontakt="", telefon="", email=None, timeout=90):
    """Firma + Chef ohne Abo. -> (status, {user_id, dealer_id, kunden_nr, kontonummer})"""
    st, js = await _post(sess, f"{api}/admin/users", {
        **_ohne_leere({"email": email}), "password": passwort,
        "company_name": firmenname, "contact_person": kontakt, "phone": telefon,
        "plan_type": "none"}, admin_h, timeout)
    if st == 200:
        ids["users"].append(js["user_id"])
        ids["dealers"].append(js["dealer_id"])
    return st, js


async def sucher_anlegen(sess, api, admin_h, ids, dealer_id, passwort,
                         vorname="", nachname="", email=None, timeout=90):
    """-> (status, {sucher_id, kontonummer, …})"""
    st, js = await _post(sess, f"{api}/admin/dealers/{dealer_id}/sucher", {
        **_ohne_leere({"email": email}), "password": passwort,
        "first_name": vorname, "last_name": nachname}, admin_h, timeout)
    if st == 200:
        ids["users"].append(js["sucher_id"])
    return st, js


async def fahrer_anlegen(sess, api, admin_h, ids, passwort, anzeigename,
                         email=None, timeout=90):
    """-> (status, {driver_id, kontonummer, driver_code})"""
    st, js = await _post(sess, f"{api}/admin/drivers", {
        **_ohne_leere({"email": email}), "password": passwort,
        "display_name": anzeigename}, admin_h, timeout)
    if st == 200:
        ids["driver_accounts"].append(js["driver_id"])
    return st, js


async def kaeufer_anlegen(sess, api, admin_h, ids, passwort, firmenname,
                          kontakt, telefon="", email=None, timeout=90):
    """Zwischenhaendler mit B2B-Nachweis. -> (status, {user_id, kontonummer})"""
    st, js = await _post(sess, f"{api}/admin/buyers", {
        **_ohne_leere({"email": email}), "password": passwort,
        "company_name": firmenname, "contact_name": kontakt, "phone": telefon,
        "b2b_nachweis": True}, admin_h, timeout)
    if st == 200:
        ids["users"].append(js["user_id"])
    return st, js


def konten_loeschen(dbx, ids: dict) -> dict:
    """Genau die gesammelten Konten entfernen (Abos der Konten und Firmen,
    Fahrer-Zuordnungen, Firmen, Konten). Leere Listen loeschen nichts."""
    users, dealers, fahrer = ids["users"], ids["dealers"], ids["driver_accounts"]
    n = {}
    n["subscriptions"] = (dbx.subscriptions.delete_many({"subject_user_id": {"$in": users}}).deleted_count
                          + dbx.subscriptions.delete_many({"dealer_id": {"$in": dealers}}).deleted_count)
    n["dealer_drivers"] = dbx.dealer_drivers.delete_many({"driver_account_id": {"$in": fahrer}}).deleted_count
    n["dealers"] = dbx.dealers.delete_many({"id": {"$in": dealers}}).deleted_count
    n["users"] = dbx.users.delete_many({"id": {"$in": users}}).deleted_count
    n["driver_accounts"] = dbx.driver_accounts.delete_many({"id": {"$in": fahrer}}).deleted_count
    return n
